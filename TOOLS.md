# Tools

本目录包含可复用的视频/媒体处理工具，支持本地文件、HTTP/HTTPS URL 与 S3 URL（`s3://bucket/key`）。

---

## video_metadata

提取视频元数据（时长、编码、分辨率、帧率等）。

### SYNOPSIS

```bash
uv run python -m vision_ai_tools.video_metadata <input_path> [OPTIONS]
```

```python
from vision_ai_tools.video_metadata import extract_video_metadata
metadata = extract_video_metadata(input_path, **options)
```

### ARGUMENTS

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `input_path` | str | Yes | 视频路径：本地路径或 HTTP/HTTPS URL |

### OPTIONS

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `--probe-mode` | str | `url` | 远程文件处理模式：`download` (下载后探测) 或 `url` (直接探测URL) |

### OUTPUT

JSON 对象，包含以下字段：

```json
{
  "duration": 7.367,
  "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
  "bit_rate": 1058326,
  "has_video": true,
  "has_audio": true,
  "video_codec": "h264",
  "video_width": 720,
  "video_height": 720,
  "video_fps": 30.0,
  "audio_codec": "aac",
  "audio_sample_rate": 44100,
  "audio_channels": 2
}
```

### EXAMPLES

```bash
# 本地文件
uv run python -m vision_ai_tools.video_metadata ./video.mp4

# HTTP URL
uv run python -m vision_ai_tools.video_metadata "https://example.com/video.mp4"
```

### DEPENDENCIES

- ffprobe (FFmpeg)
- requests

---

## video_keyframes

从视频中提取关键帧图片，支持多种检测算法。

### SYNOPSIS

```bash
uv run python -m vision_ai_tools.video_keyframes <input_path> [OPTIONS]
```

```python
from vision_ai_tools.video_keyframes import extract_video_keyframes
results = extract_video_keyframes(input_path, **options)
```

### ARGUMENTS

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `input_path` | str | Yes | 视频路径：本地路径或 HTTP/HTTPS URL |

### OPTIONS

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `--method` | str | `I_frame` | 检测方法，逗号分隔可指定多个（按顺序尝试）。可选: `I_frame`, `difference`, `optical_flow`, `histogram` |
| `--threshold` | float | (auto) | 分数阈值（仅非 I_frame 方法）。difference≈12, histogram≈0.35, optical_flow≈1.5 |
| `--max-keyframes` | int | `20` | 最大关键帧数量 |
| `--min-interval-s` | float | `0.5` | 关键帧间最小时间间隔（秒） |
| `--output-dir` | str | None | 图片输出目录 |
| `--image-format` | str | `jpg` | 输出图片格式：`jpg` 或 `png` |
| `--manifest` | str | None | Manifest JSON 输出路径（本地路径） |
| `--flow-step` | int | `2` | optical_flow 方法每 N 帧计算一次 |
| `--no-cleanup` | flag | False | 保留临时文件 |

### OUTPUT

JSON 数组，每个元素为一个关键帧：

```json
[
  {
    "frame_index": 0,
    "timestamp_s": 0.0,
    "method": "I_frame",
    "score": null,
    "local_path": "./output/keyframe_0001_0000000000.jpg"
  }
]
```

### EXAMPLES

```bash
# 提取 I 帧到本地目录
uv run python -m vision_ai_tools.video_keyframes ./video.mp4 --output-dir ./frames

# 使用 difference 方法，最多 10 帧
uv run python -m vision_ai_tools.video_keyframes ./video.mp4 --method difference --max-keyframes 10 --output-dir ./frames

# 多方法回退（先尝试 I_frame，失败则用 difference）
uv run python -m vision_ai_tools.video_keyframes ./video.mp4 --method "I_frame,difference" --output-dir ./frames
```

### DEPENDENCIES

- ffprobe, ffmpeg (FFmpeg)
- requests
- opencv-python-headless, numpy (非 I_frame 方法)

---

## video_resize

按指定 `width/height` 调整视频分辨率，支持多种宽高比策略，默认保持音频流不变（copy）。

### SYNOPSIS

```bash
uv run python -m vision_ai_tools.video_resize <input_path> --output <output> [OPTIONS]
```

```python
from vision_ai_tools.video_resize import resize_video
result = resize_video(input_path, output="out.mp4", width=1280, height=720)
```

### ARGUMENTS

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `input_path` | str | Yes | 视频路径：本地/HTTP/HTTPS/S3 |

### OPTIONS

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `--output` | str | Yes | 输出路径或 `s3://...` |
| `--width` | int | None | 目标宽度（像素） |
| `--height` | int | None | 目标高度（像素） |
| `--aspect-policy` | str | `stretch` | 双维同时指定时的策略：`stretch`/`contain`/`cover`/`pad` |
| `--pad-color` | str | `black` | contain/pad 补边颜色 |
| `--video-codec` | str | `libx264` | 视频编码器（如 `libx265`/`libx264`） |
| `--crf` | int | `23` | CRF（未设置 bitrate 时使用） |
| `--bitrate` | str | None | 目标视频码率（如 `2000k`；设置后覆盖 CRF） |
| `--preset` | str | `medium` | 编码 preset |
| `--pix-fmt` | str | `yuv420p` | 像素格式 |
| `--no-faststart` | flag | False | 禁用 MP4 faststart |
| `--timeout-s` | int | `1800` | ffmpeg 超时秒数 |

### OUTPUT

JSON 对象（示例字段）：

```json
{
  "input_path": "s3://bucket/in.mp4",
  "output": "s3://bucket/out.mp4",
  "local_path": null,
  "s3_url": "s3://bucket/out.mp4",
  "input_width": 1920,
  "input_height": 1080,
  "output_width": 1280,
  "output_height": 720,
  "applied_policy": "stretch"
}
```

### EXAMPLES

```bash
# 严格输出 1280x720（允许拉伸）
uv run python -m vision_ai_tools.video_resize ./in.mp4 --output ./out.mp4 --width 1280 --height 720 --aspect-policy stretch

# 只指定高度：等比缩放
uv run python -m vision_ai_tools.video_resize ./in.mp4 --output ./out.mp4 --height 720
```

### DEPENDENCIES

- ffmpeg, ffprobe (FFmpeg)
- requests
- boto3 (S3 输入/输出时)

---

## video_remove_audio

移除视频音轨（不重编码，保留原始视频编码与质量）。

### SYNOPSIS

```bash
uv run python -m vision_ai_tools.video_remove_audio <input_path> --output <output> [OPTIONS]
```

```python
from vision_ai_tools.video_remove_audio import remove_video_audio
result = remove_video_audio(input_path, output="noaudio.mp4")
```

### OPTIONS

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `--output` | str | Yes | 输出路径或 `s3://...` |
| `--timeout-s` | int | `600` | ffmpeg 超时秒数 |

### OUTPUT

```json
{
  "has_audio_before": true,
  "audio_streams_removed": 1,
  "local_path": "./noaudio.mp4"
}
```

### DEPENDENCIES

- ffmpeg, ffprobe (FFmpeg)
- requests
- boto3 (S3 输入/输出时)

---

## video_convert_mp4

将多种视频格式转换为 MP4，默认优先使用 H.265（`libx265`），并自动选择第一个音轨。

### SYNOPSIS

```bash
uv run python -m vision_ai_tools.video_convert_mp4 <input_path> --output <output> [OPTIONS]
```

```python
from vision_ai_tools.video_convert_mp4 import convert_to_mp4
result = convert_to_mp4(input_path, output="out.mp4", video_codec="auto", max_height=1080)
```

### OPTIONS

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `--output` | str | Yes | 输出路径或 `s3://...` |
| `--video-codec` | str | `auto` | `auto`/`libx265`/`libx264` |
| `--crf` | int | `28` | CRF（未设置 bitrate 时使用） |
| `--bitrate` | str | None | 目标视频码率（如 `2500k`；设置后覆盖 CRF） |
| `--preset` | str | `medium` | 编码 preset |
| `--max-height` | int | None | 最大高度（仅降采样，不放大；等比缩放） |
| `--audio-codec` | str | `aac` | 音频编码器 |
| `--audio-bitrate` | str | `128k` | 音频码率 |
| `--audio-sample-rate` | int | None | 采样率 |
| `--audio-channels` | int | None | 声道数 |
| `--timeout-s` | int | `3600` | ffmpeg 超时秒数 |

### DEPENDENCIES

- ffmpeg, ffprobe (FFmpeg)
- requests
- boto3 (S3 输入/输出时)

---

## video_adaptive_compress

根据目标文件大小自适应压缩视频（策略顺序：fps → 分辨率 → 码率控制）。

### SYNOPSIS

```bash
uv run python -m vision_ai_tools.video_adaptive_compress <input_path> --output <output> (--target-bytes N | --target-mb M) [OPTIONS]
```

```python
from vision_ai_tools.video_adaptive_compress import adaptive_compress_video
result = adaptive_compress_video(input_path, output="out.mp4", target_mb=8)
```

### OPTIONS

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `--output` | str | Yes | 输出路径或 `s3://...` |
| `--target-bytes` | int | - | 目标字节数 |
| `--target-mb` | float | - | 目标大小（MiB） |
| `--video-codec` | str | `auto` | `auto`/`libx265`/`libx264` |
| `--crf` | int | `28` | 前两级策略使用的 CRF |
| `--audio-bitrate` | str | `128k` | 音频码率 |
| `--min-fps` | float | `8` | 最低 fps |
| `--min-height` | int | `480` | 最低高度 |
| `--timeout-s` | int | `7200` | 每次尝试的超时秒数 |

### OUTPUT

返回对象包含 `success`、`actual_bytes` 与 `attempts`（每次尝试的参数与产物大小）。

### DEPENDENCIES

- ffmpeg, ffprobe (FFmpeg)
- requests
- boto3 (S3 输入/输出时)

---

## video_split

将视频切分为多个片段：支持基于 I 帧切分（`iframe`）或按固定秒长切分（`fixed`）。

### SYNOPSIS

```bash
uv run python -m vision_ai_tools.video_split <input_path> --mode (iframe|fixed) [OPTIONS]
```

```python
from vision_ai_tools.video_split import split_video
result = split_video(input_path, mode="fixed", output_dir="./segs", segment_s=10)
```

### OPTIONS

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `--mode` | str | Yes | `iframe` 或 `fixed` |
| `--output-dir` | str | None | 片段输出目录（若不提供则需 `--s3-output-prefix`） |
| `--s3-output-prefix` | str | None | 上传片段到 `s3://bucket/prefix/` |
| `--manifest` | str | None | manifest JSON（本地或 S3） |
| `--segment-s` | float | None | 固定秒长（fixed 模式必填） |
| `--every-n-iframes` | int | `1` | I 帧切分粒度（iframe 模式） |
| `--max-segments` | int | None | 限制输出片段数量（iframe 模式） |
| `--prefix` | str | `segment_` | 文件名前缀 |
| `--ext` | str | None | 输出容器扩展名（默认同输入） |
| `--timeout-s` | int | `3600` | ffmpeg 超时秒数 |

### NOTES / PITFALLS

- 默认使用 `-c copy` 不重编码，切分点可能会对齐到关键帧附近（尤其是 fixed 模式）。

### DEPENDENCIES

- ffmpeg, ffprobe (FFmpeg)
- requests
- boto3 (S3 输入/输出时)
