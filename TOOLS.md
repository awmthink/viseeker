# Tools

This directory contains reusable video/media processing tools that support local files, HTTP/HTTPS URLs, and S3 URLs (`s3://bucket/key`).

---

## video_metadata

Extract video metadata (duration, codec, resolution, framerate, etc.).

### SYNOPSIS

```bash
 python -m viseeker.video_metadata <input_path> [OPTIONS]
```

```python
from viseeker.video_metadata import extract_video_metadata
metadata = extract_video_metadata(input_path, **options)
```

### ARGUMENTS

| Name         | Type | Required | Description                              |
| ------------ | ---- | -------- | ---------------------------------------- |
| `input_path` | str  | Yes      | Video path: local path or HTTP/HTTPS URL |

### OPTIONS

| Name           | Type | Default | Description                                                                                |
| -------------- | ---- | ------- | ------------------------------------------------------------------------------------------ |
| `--probe-mode` | str  | `url`   | Remote file handling mode: `download` (probe after download) or `url` (probe URL directly) |

### OUTPUT

JSON object with the following fields:

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
# Local file
python -m viseeker.video_metadata ./video.mp4

# HTTP URL
python -m viseeker.video_metadata "https://example.com/video.mp4"
```

### DEPENDENCIES

- ffprobe (FFmpeg)
- requests

---

## video_keyframes

Extract keyframe images from videos, supporting multiple detection algorithms.

### SYNOPSIS

```bash
python -m viseeker.video_keyframes <input_path> [OPTIONS]
```

```python
from viseeker.video_keyframes import extract_video_keyframes
results = extract_video_keyframes(input_path, **options)
```

### ARGUMENTS

| Name         | Type | Required | Description                              |
| ------------ | ---- | -------- | ---------------------------------------- |
| `input_path` | str  | Yes      | Video path: local path or HTTP/HTTPS URL |

### OPTIONS

| Name               | Type  | Default   | Description                                                                                                                    |
| ------------------ | ----- | --------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `--method`         | str   | `I_frame` | Detection method, comma-separated for multiple (tried in order). Options: `I_frame`, `difference`, `optical_flow`, `histogram` |
| `--threshold`      | float | (auto)    | Score threshold (non-I_frame methods only). difference≈12, histogram≈0.35, optical_flow≈1.5                                    |
| `--max-keyframes`  | int   | `20`      | Maximum number of keyframes                                                                                                    |
| `--min-interval-s` | float | `0.5`     | Minimum time interval between keyframes (seconds)                                                                              |
| `--output-dir`     | str   | None      | Image output directory                                                                                                         |
| `--image-format`   | str   | `jpg`     | Output image format: `jpg` or `png`                                                                                            |
| `--manifest`       | str   | None      | Manifest JSON output path (local path)                                                                                         |
| `--flow-step`      | int   | `2`       | optical_flow method calculates every N frames                                                                                  |
| `--no-cleanup`     | flag  | False     | Keep temporary files                                                                                                           |

### OUTPUT

JSON array, each element is a keyframe:

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
# Extract I-frames to local directory
python -m viseeker.video_keyframes ./video.mp4 --output-dir ./frames

# Use difference method, max 10 frames
python -m viseeker.video_keyframes ./video.mp4 --method difference --max-keyframes 10 --output-dir ./frames

# Multiple methods fallback (try I_frame first, then difference if failed)
python -m viseeker.video_keyframes ./video.mp4 --method "I_frame,difference" --output-dir ./frames
```

### DEPENDENCIES

- ffprobe, ffmpeg (FFmpeg)
- requests
- opencv-python-headless, numpy (non-I_frame methods)

---

## video_resize

Resize video to specified `width/height`, supporting multiple aspect ratio strategies. Audio stream is preserved by default (copy).

### SYNOPSIS

```bash
python -m viseeker.video_resize <input_path> --output <output> [OPTIONS]
```

```python
from viseeker.video_resize import resize_video
result = resize_video(input_path, output="out.mp4", width=1280, height=720)
```

### ARGUMENTS

| Name         | Type | Required | Description                     |
| ------------ | ---- | -------- | ------------------------------- |
| `input_path` | str  | Yes      | Video path: local/HTTP/HTTPS/S3 |

### OPTIONS

| Name              | Type | Default   | Description                                                                |
| ----------------- | ---- | --------- | -------------------------------------------------------------------------- |
| `--output`        | str  | Yes       | Output path or `s3://...`                                                  |
| `--width`         | int  | None      | Target width (pixels)                                                      |
| `--height`        | int  | None      | Target height (pixels)                                                     |
| `--aspect-policy` | str  | `stretch` | Strategy when both dimensions specified: `stretch`/`contain`/`cover`/`pad` |
| `--pad-color`     | str  | `black`   | Padding color for contain/pad                                              |
| `--video-codec`   | str  | `libx264` | Video codec (e.g., `libx265`/`libx264`)                                    |
| `--crf`           | int  | `23`      | CRF (used when bitrate not set)                                            |
| `--bitrate`       | str  | None      | Target video bitrate (e.g., `2000k`; overrides CRF if set)                 |
| `--preset`        | str  | `medium`  | Encoding preset                                                            |
| `--pix-fmt`       | str  | `yuv420p` | Pixel format                                                               |
| `--no-faststart`  | flag | False     | Disable MP4 faststart                                                      |
| `--timeout-s`     | int  | `1800`    | ffmpeg timeout (seconds)                                                   |

### OUTPUT

JSON object (example fields):

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
# Strict output 1280x720 (allow stretching)
python -m viseeker.video_resize ./in.mp4 --output ./out.mp4 --width 1280 --height 720 --aspect-policy stretch

# Only specify height: proportional scaling
python -m viseeker.video_resize ./in.mp4 --output ./out.mp4 --height 720
```

### DEPENDENCIES

- ffmpeg, ffprobe (FFmpeg)
- requests
- boto3 (for S3 input/output)

---

## video_remove_audio

Remove audio track from video (no re-encoding, preserves original video codec and quality).

### SYNOPSIS

```bash
python -m viseeker.video_remove_audio <input_path> --output <output> [OPTIONS]
```

```python
from viseeker.video_remove_audio import remove_video_audio
result = remove_video_audio(input_path, output="noaudio.mp4")
```

### OPTIONS

| Name          | Type | Default | Description               |
| ------------- | ---- | ------- | ------------------------- |
| `--output`    | str  | Yes     | Output path or `s3://...` |
| `--timeout-s` | int  | `600`   | ffmpeg timeout (seconds)  |

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
- boto3 (for S3 input/output)

---

## video_convert_mp4

Convert various video formats to MP4. Defaults to H.265 (`libx265`) and automatically selects the first audio track.

### SYNOPSIS

```bash
python -m viseeker.video_convert_mp4 <input_path> --output <output> [OPTIONS]
```

```python
from viseeker.video_convert_mp4 import convert_to_mp4
result = convert_to_mp4(input_path, output="out.mp4", video_codec="auto", max_height=1080)
```

### OPTIONS

| Name                  | Type | Default  | Description                                                 |
| --------------------- | ---- | -------- | ----------------------------------------------------------- |
| `--output`            | str  | Yes      | Output path or `s3://...`                                   |
| `--video-codec`       | str  | `auto`   | `auto`/`libx265`/`libx264`                                  |
| `--crf`               | int  | `28`     | CRF (used when bitrate not set)                             |
| `--bitrate`           | str  | None     | Target video bitrate (e.g., `2500k`; overrides CRF if set)  |
| `--preset`            | str  | `medium` | Encoding preset                                             |
| `--max-height`        | int  | None     | Maximum height (downscale only, no upscaling; proportional) |
| `--audio-codec`       | str  | `aac`    | Audio codec                                                 |
| `--audio-bitrate`     | str  | `128k`   | Audio bitrate                                               |
| `--audio-sample-rate` | int  | None     | Sample rate                                                 |
| `--audio-channels`    | int  | None     | Number of channels                                          |
| `--timeout-s`         | int  | `3600`   | ffmpeg timeout (seconds)                                    |

### DEPENDENCIES

- ffmpeg, ffprobe (FFmpeg)
- requests
- boto3 (for S3 input/output)

---

## video_adaptive_compress

Adaptively compress video to target file size (strategy order: fps → resolution → bitrate control).

### SYNOPSIS

```bash
python -m viseeker.video_adaptive_compress <input_path> --output <output> (--target-bytes N | --target-mb M) [OPTIONS]
```

```python
from viseeker.video_adaptive_compress import adaptive_compress_video
result = adaptive_compress_video(input_path, output="out.mp4", target_mb=8)
```

### OPTIONS

| Name              | Type  | Default | Description                           |
| ----------------- | ----- | ------- | ------------------------------------- |
| `--output`        | str   | Yes     | Output path or `s3://...`             |
| `--target-bytes`  | int   | -       | Target bytes                          |
| `--target-mb`     | float | -       | Target size (MiB)                     |
| `--video-codec`   | str   | `auto`  | `auto`/`libx265`/`libx264`            |
| `--crf`           | int   | `28`    | CRF used by first two strategy levels |
| `--audio-bitrate` | str   | `128k`  | Audio bitrate                         |
| `--min-fps`       | float | `8`     | Minimum fps                           |
| `--min-height`    | int   | `480`   | Minimum height                        |
| `--timeout-s`     | int   | `7200`  | Timeout per attempt (seconds)         |

### OUTPUT

Returns object containing `success`, `actual_bytes`, and `attempts` (parameters and output size for each attempt).

### DEPENDENCIES

- ffmpeg, ffprobe (FFmpeg)
- requests
- boto3 (for S3 input/output)

---

## video_split

Split video into multiple segments: supports I-frame based splitting (`iframe`) or fixed duration splitting (`fixed`).

### SYNOPSIS

```bash
python -m viseeker.video_split <input_path> --mode (iframe|fixed) [OPTIONS]
```

```python
from viseeker.video_split import split_video
result = split_video(input_path, mode="fixed", output_dir="./segs", segment_s=10)
```

### OPTIONS

| Name                 | Type  | Default    | Description                                                              |
| -------------------- | ----- | ---------- | ------------------------------------------------------------------------ |
| `--mode`             | str   | Yes        | `iframe` or `fixed`                                                      |
| `--output-dir`       | str   | None       | Segment output directory (required if `--s3-output-prefix` not provided) |
| `--s3-output-prefix` | str   | None       | Upload segments to `s3://bucket/prefix/`                                 |
| `--manifest`         | str   | None       | Manifest JSON (local or S3)                                              |
| `--segment-s`        | float | None       | Fixed duration (required for fixed mode)                                 |
| `--every-n-iframes`  | int   | `1`        | I-frame splitting granularity (iframe mode)                              |
| `--max-segments`     | int   | None       | Limit number of output segments (iframe mode)                            |
| `--prefix`           | str   | `segment_` | Filename prefix                                                          |
| `--ext`              | str   | None       | Output container extension (defaults to input extension)                 |
| `--timeout-s`        | int   | `3600`     | ffmpeg timeout (seconds)                                                 |

### NOTES / PITFALLS

- Default uses `-c copy` (no re-encoding), split points may align near keyframes (especially in fixed mode).

### DEPENDENCIES

- ffmpeg, ffprobe (FFmpeg)
- requests
- boto3 (for S3 input/output)

---

## image_describe

Describe an image using a generic multimodal VLM via an OpenAI-compatible client.

### SYNOPSIS

```bash
uv run python -m viseeker.image_describe <input_path> [OPTIONS]
```

```python
from viseeker.image_describe import describe_image
result = describe_image(input_path, prompt="Describe this image in detail.")
```

### ARGUMENTS

| Name         | Type | Required | Description                     |
| ------------ | ---- | -------- | ------------------------------- |
| `input_path` | str  | Yes      | Image path: local/HTTP/HTTPS/S3 |

### OPTIONS

| Name       | Type | Default                   | Description                                                                 |
| ---------- | ---- | ------------------------- | --------------------------------------------------------------------------- |
| `--prompt` | str  | (built-in default prompt) | Custom prompt to guide description. When omitted, use built-in generic one. |

### OUTPUT

The tool returns a single string value (JSON string) containing the natural language description of the image.

### EXAMPLES

```bash
# Basic usage with built-in prompt
uv run python -m viseeker.image_describe ./image.png

# With custom Chinese prompt
uv run python -m viseeker.image_describe ./image.png \
  --prompt "请用中文详细描述图片中的场景、人物和文字内容"
```

### DEPENDENCIES

- openai (for the OpenAI-compatible client)
- requests (via PreparedInput for HTTP/S3 handling)

### ENVIRONMENT VARIABLES

- `VLM_API_KEY`: VLM 多模态接口 API Key（必需）。
- `VLM_BASE_URL`: VLM 接口 Base URL（必需）。
- `VLM_MODEL_ID`: 模型 ID（必需）。

---

## video_describe

Understand a video by sampling frames and calling a multimodal VLM (multi-frame).

### SYNOPSIS

```bash
uv run python -m viseeker.video_describe <input_path> [OPTIONS]
```

```python
from viseeker.video_describe import describe_video
result = describe_video(input_path, prompt="你觉得这个恐怖吗？", detail="low")
```

### ARGUMENTS

| Name         | Type | Required | Description                     |
| ------------ | ---- | -------- | ------------------------------- |
| `input_path` | str  | Yes      | Video path: local/HTTP/HTTPS/S3 |

### OPTIONS

| Name              | Type  | Default | Description                                       |
| ----------------- | ----- | ------- | ------------------------------------------------- |
| `--prompt`        | str   | None    | User prompt/question (default: built-in generic)  |
| `--detail`        | str   | `low`   | Frame detail for VLM: `low` or `high`             |
| `--fps`           | float | `1.0`   | Sampling FPS for short videos                     |
| `--max-frames`    | int   | `128`   | Maximum frames to send                            |

### OUTPUT

JSON object:

```json
{
  "text": "......",
  "detail": "low",
  "duration_s": 12.34,
  "frame_count": 13
}
```

### EXAMPLES

```bash
# Basic usage (1 fps, max 128 frames), note: duration must be < 300s
uv run python -m viseeker.video_describe ./video.mp4 \
  --prompt "你觉得这个恐怖吗？" \
  --detail low

# Use high detail
uv run python -m viseeker.video_describe "s3://bucket/video.mp4" \
  --prompt "总结这个视频发生了什么" \
  --detail high
```

### NOTES / PITFALLS

- Input video duration must be **< 5 minutes (300 seconds)**. Longer videos are rejected.
- Frames are decoded by OpenCV (streaming). For HTTP/HTTPS inputs and S3 inputs (via presigned HTTPS URLs), the full video is not downloaded.
- Sampled frames are written as local JPEGs and passed to the model via Responses API `input_image` with `file://...` URLs (SDK uploads files via Files API), which avoids large base64 payloads.

### DEPENDENCIES

- ffprobe (FFmpeg)
- requests (via PreparedInput for HTTP/S3 handling)
- opencv-python-headless, numpy (for streaming frame decoding)
- boto3 (for S3 presigned URL generation)
- volcengine-python-sdk[ark] (volcenginesdkarkruntime, for Responses API + Files upload)

### ENVIRONMENT VARIABLES

- `VLM_API_KEY`: VLM 多模态接口 API Key（必需）。
- `VLM_BASE_URL`: VLM 接口 Base URL（必需）。
- `VLM_MODEL_ID`: 模型 ID（必需）。
