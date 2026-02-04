"""
视频元数据提取工具

支持多种视频文件格式，使用 ffprobe 工具进行元数据提取。
支持本地文件、远程文件(S3)、HTTP/HTTPS链接。
"""

import json
import os
import subprocess
import tempfile
import urllib.parse
from typing import Dict, Optional, Tuple

import boto3
import requests
from botocore.exceptions import ClientError


class VideoMetadataExtractor:
    """视频元数据提取器"""

    def __init__(self, ffprobe_path: Optional[str] = None):
        """
        初始化提取器

        Args:
            ffprobe_path: ffprobe 可执行文件路径，如果为 None 则使用系统 PATH 中的 ffprobe

        Raises:
            ValueError: 如果找不到 ffprobe 可执行文件
        """
        self.ffprobe_path = ffprobe_path or self._find_ffprobe()
        self._verify_ffprobe()

    @staticmethod
    def _find_ffprobe() -> str:
        """查找系统中的 ffprobe 可执行文件"""
        try:
            result = subprocess.run(
                ["which", "ffprobe"], capture_output=True, text=True, check=True
            )
            return result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            # 如果找不到，尝试直接使用 ffprobe
            return "ffprobe"

    def _verify_ffprobe(self) -> None:
        """验证 ffprobe 是否可用"""
        try:
            result = subprocess.run(
                [self.ffprobe_path, "-version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            if "ffprobe" not in result.stdout.lower():
                raise ValueError(
                    f"ffprobe not found at {self.ffprobe_path}. "
                    "Please install FFmpeg: https://ffmpeg.org/download.html"
                )
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            raise ValueError(
                f"ffprobe not found at {self.ffprobe_path}. "
                "Please install FFmpeg:\n"
                "  - macOS: brew install ffmpeg\n"
                "  - Ubuntu/Debian: apt-get install ffmpeg\n"
                "  - Windows: Download from https://ffmpeg.org/download.html"
            )

    def _is_local_file(self, path: str) -> bool:
        """判断是否为本地文件路径"""
        return os.path.exists(path) and os.path.isfile(path)

    def _is_http_url(self, path: str) -> bool:
        """判断是否为 HTTP/HTTPS URL"""
        parsed = urllib.parse.urlparse(path)
        return parsed.scheme in ("http", "https")

    def _is_s3_url(self, path: str) -> bool:
        """判断是否为 S3 URL"""
        parsed = urllib.parse.urlparse(path)
        return parsed.scheme == "s3"

    def _download_http_file(self, url: str, temp_dir: str) -> str:
        """下载 HTTP/HTTPS 文件到临时目录"""
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()

        # 从 URL 或 Content-Disposition 获取文件名
        filename = os.path.basename(urllib.parse.urlparse(url).path)
        if not filename:
            filename = "video_file"

        temp_path = os.path.join(temp_dir, filename)
        with open(temp_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        return temp_path

    def _download_s3_file(
        self,
        url: str,
        temp_dir: str,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        region_name: Optional[str] = None,
    ) -> str:
        """下载 S3 文件到临时目录"""
        parsed = urllib.parse.urlparse(url)
        bucket_name = parsed.netloc
        object_key = parsed.path.lstrip("/")

        s3_client = boto3.client(
            "s3",
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=region_name,
        )

        filename = os.path.basename(object_key) or "video_file"
        temp_path = os.path.join(temp_dir, filename)

        try:
            s3_client.download_file(bucket_name, object_key, temp_path)
        except ClientError as e:
            raise ValueError(f"Failed to download S3 file: {e}")

        return temp_path

    def _prepare_file(
        self,
        input_path: str,
        s3_config: Optional[Dict] = None,
    ) -> Tuple[str, bool]:
        """
        准备文件路径，如果是远程文件则下载到临时目录

        Returns:
            (file_path, is_temporary) 元组，is_temporary 表示是否为临时文件需要清理
        """
        if self._is_local_file(input_path):
            return input_path, False

        if self._is_http_url(input_path):
            temp_dir = tempfile.mkdtemp()
            temp_path = self._download_http_file(input_path, temp_dir)
            return temp_path, True

        if self._is_s3_url(input_path):
            s3_config = s3_config or {}
            temp_dir = tempfile.mkdtemp()
            temp_path = self._download_s3_file(
                input_path,
                temp_dir,
                aws_access_key_id=s3_config.get("access_key_id"),
                aws_secret_access_key=s3_config.get("secret_access_key"),
                region_name=s3_config.get("region"),
            )
            return temp_path, True

        raise ValueError(f"Unsupported input path: {input_path}")

    def _run_ffprobe(self, file_path: str) -> Dict:
        """运行 ffprobe 并返回 JSON 输出"""
        cmd = [
            self.ffprobe_path,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            file_path,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
            return json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            raise ValueError("ffprobe execution timeout")
        except subprocess.CalledProcessError as e:
            raise ValueError(f"ffprobe execution failed: {e.stderr}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse ffprobe output: {e}")

    def _extract_metadata(self, ffprobe_data: Dict) -> Dict:
        """从 ffprobe 输出中提取元数据"""
        format_info = ffprobe_data.get("format", {})
        streams = ffprobe_data.get("streams", [])

        # 提取基础信息
        duration = float(format_info.get("duration", 0))
        format_name = format_info.get("format_name", "")
        bit_rate = int(format_info.get("bit_rate", 0))

        # 提取视频流信息
        video_stream = None
        audio_stream = None

        for stream in streams:
            codec_type = stream.get("codec_type", "")
            if codec_type == "video" and video_stream is None:
                video_stream = stream
            elif codec_type == "audio" and audio_stream is None:
                audio_stream = stream

        has_video = video_stream is not None
        has_audio = audio_stream is not None

        # 视频信息
        video_codec = None
        video_width = None
        video_height = None
        video_fps = None

        if video_stream:
            video_codec = video_stream.get("codec_name")
            video_width = int(video_stream.get("width", 0))
            video_height = int(video_stream.get("height", 0))

            # 计算帧率
            r_frame_rate = video_stream.get("r_frame_rate", "0/1")
            if "/" in r_frame_rate:
                num, den = map(int, r_frame_rate.split("/"))
                video_fps = num / den if den > 0 else None
            else:
                video_fps = float(r_frame_rate) if r_frame_rate else None

        # 音频信息
        audio_codec = None
        audio_sample_rate = None
        audio_channels = None

        if audio_stream:
            audio_codec = audio_stream.get("codec_name")
            audio_sample_rate = int(audio_stream.get("sample_rate", 0))
            audio_channels = int(audio_stream.get("channels", 0))

        return {
            "duration": duration,
            "format_name": format_name,
            "bit_rate": bit_rate,
            "has_video": has_video,
            "has_audio": has_audio,
            "video_codec": video_codec,
            "video_width": video_width,
            "video_height": video_height,
            "video_fps": video_fps,
            "audio_codec": audio_codec,
            "audio_sample_rate": audio_sample_rate,
            "audio_channels": audio_channels,
        }

    def extract(
        self,
        input_path: str,
        s3_config: Optional[Dict] = None,
    ) -> Dict:
        """
        提取视频元数据

        Args:
            input_path: 视频文件路径（本地文件、S3 URL 或 HTTP/HTTPS URL）
            s3_config: S3 配置字典，包含 access_key_id, secret_access_key, region

        Returns:
            包含视频元数据的字典

        Example:
            >>> extractor = VideoMetadataExtractor()
            >>> metadata = extractor.extract("video.mp4")
            >>> print(metadata)
            {
                "duration": 7.367,
                "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                "bit_rate": 1058326,
                "has_video": True,
                "has_audio": True,
                "video_codec": "h264",
                "video_width": 720,
                "video_height": 720,
                "video_fps": 30.0,
                "audio_codec": "aac",
                "audio_sample_rate": 44100,
                "audio_channels": 2
            }
        """
        file_path = None
        is_temporary = False

        try:
            file_path, is_temporary = self._prepare_file(input_path, s3_config)
            ffprobe_data = self._run_ffprobe(file_path)
            metadata = self._extract_metadata(ffprobe_data)
            return metadata
        finally:
            # 清理临时文件
            if is_temporary and file_path and os.path.exists(file_path):
                temp_dir = os.path.dirname(file_path)
                try:
                    os.remove(file_path)
                    os.rmdir(temp_dir)
                except OSError:
                    pass


def extract_video_metadata(
    input_path: str,
    s3_config: Optional[Dict] = None,
    ffprobe_path: Optional[str] = None,
) -> Dict:
    """
    便捷函数：提取视频元数据

    Args:
        input_path: 视频文件路径（本地文件、S3 URL 或 HTTP/HTTPS URL）
        s3_config: S3 配置字典，包含 access_key_id, secret_access_key, region
        ffprobe_path: ffprobe 可执行文件路径

    Returns:
        包含视频元数据的字典
    """
    extractor = VideoMetadataExtractor(ffprobe_path=ffprobe_path)
    return extractor.extract(input_path, s3_config=s3_config)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python video_metadata.py <video_file_path>")
        sys.exit(1)

    input_file = sys.argv[1]
    try:
        metadata = extract_video_metadata(input_file)
        print(json.dumps(metadata, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
