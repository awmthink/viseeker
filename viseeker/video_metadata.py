#!/usr/bin/env python3
"""
Utilities for extracting video metadata.

Uses ffprobe to support various video formats.
Compatible with local files, remote files (S3), and HTTP/HTTPS URLs.
"""

import argparse
import json
import subprocess
import sys
from typing import Dict, Optional

from ._internal import ffmpeg, inputs


class VideoMetadataExtractor:
    """
    Extract metadata from video files.

    This class uses ffprobe to read container and stream information and returns a simplified
    dictionary suitable for downstream workflows.
    """

    def __init__(self):
        """
        Initialize the extractor instance.

        Raises:
            ValueError: If ffprobe is not found or is unusable.
        """
        self.ffprobe_path = ffmpeg.find_ffprobe()
        ffmpeg.verify_ffprobe(self.ffprobe_path)

    def _run_ffprobe(self, input_spec: str) -> Dict:
        """
        Run ffprobe and parse JSON output.

        Returns the raw ffprobe JSON as a Python dictionary.
        """
        cmd = [
            self.ffprobe_path,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            input_spec,
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
        """
        Build a simplified metadata dictionary from ffprobe output.

        The returned structure is stable and intentionally smaller than the raw ffprobe response.
        """
        format_info = ffprobe_data.get("format", {})
        streams = ffprobe_data.get("streams", [])

        # Extract basic container information
        duration = float(format_info.get("duration", 0))
        format_name = format_info.get("format_name", "")
        bit_rate = int(format_info.get("bit_rate", 0))

        # Identify first video and audio streams, if present
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

        # Gather video stream details
        video_codec = None
        video_width = None
        video_height = None
        video_fps = None

        if video_stream:
            video_codec = video_stream.get("codec_name")
            video_width = int(video_stream.get("width", 0))
            video_height = int(video_stream.get("height", 0))

            # Parse and compute frames per second (FPS)
            r_frame_rate = video_stream.get("r_frame_rate", "0/1")
            if "/" in r_frame_rate:
                num, den = map(int, r_frame_rate.split("/"))
                video_fps = num / den if den > 0 else None
            else:
                video_fps = float(r_frame_rate) if r_frame_rate else None

        # Gather audio stream details
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
        probe_mode: str = "url",
    ) -> Dict:
        """
        Run the full extraction and return video metadata.

        Args:
            input_path: Path to media file (local, S3 URL, or HTTP/HTTPS).
            s3_config: Reserved for future use; S3 config is currently read from environment vars.
            probe_mode: One of "download" or "url".
                - "download": Download remote inputs to a temp file before probing.
                - "url": Probe remote inputs via URL without downloading to disk. For S3 URLs this
                  generates a presigned HTTPS URL.

        Returns:
            Dictionary of extracted metadata fields.

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
        if probe_mode not in {"download", "url"}:
            raise ValueError(f"Unsupported probe_mode: {probe_mode}")

        # s3_config is reserved for future use; S3 config is currently read from env vars.
        _ = s3_config

        with inputs.PreparedInput(input_path, mode=probe_mode) as ffprobe_input:
            ffprobe_data = self._run_ffprobe(ffprobe_input)
            return self._extract_metadata(ffprobe_data)


def extract_video_metadata(
    input_path: str,
    s3_config: Optional[Dict] = None,
    probe_mode: str = "url",
) -> Dict:
    """
    Convenience wrapper for extracting video metadata.

    Args:
        input_path: Path to media file (local, S3, HTTP/HTTPS).
        s3_config: Reserved for future use; S3 config is currently read from environment vars.
        probe_mode: One of "download" or "url". See VideoMetadataExtractor.extract.

    Returns:
        Extracted video metadata as a dictionary.
    """
    extractor = VideoMetadataExtractor()
    return extractor.extract(
        input_path,
        s3_config=s3_config,
        probe_mode=probe_mode,
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    """
    Build the command line argument parser.

    The CLI is intended for local validation and agent/tooling integration. The main extraction
    logic is also available via the Python API.
    """
    parser = argparse.ArgumentParser(
        prog="video_metadata",
        description="Extract video metadata via ffprobe (local files, HTTP/HTTPS, or S3 URLs).",
    )

    parser.add_argument(
        "input_path",
        help="Video path/URL (e.g., ./a.mp4, https://..., s3://bucket/key).",
    )
    parser.add_argument(
        "--probe-mode",
        choices=("download", "url"),
        default="url",
        help="How to probe remote inputs: download or url (default: url).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """
    Run the CLI entrypoint.

    Prints extracted metadata as pretty JSON to stdout and returns a process exit code.
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        metadata = extract_video_metadata(
            args.input_path,
            probe_mode=args.probe_mode,
        )
        print(json.dumps(metadata, indent=2, ensure_ascii=False))
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
