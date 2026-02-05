# vision-ai-tools

A collection of computer vision utilities for image and video processing, designed to be easy for AI agents to use.

## Quick Start

### Install Dependencies

```bash
uv sync --dev
```

Some tools require FFmpeg:

```bash
brew install ffmpeg
```

### Run a Tool

```bash
uv run python -m vision_ai_tools.video_metadata ./demo.mp4
```

## Available Tools

See `TOOLS.md` for detailed usage and examples.

- **video_metadata**: Extract video metadata (duration, codec, resolution, framerate, etc.)
- **video_keyframes**: Extract keyframes using multiple algorithms
- **video_resize**: Resize videos with various aspect ratio strategies
- **video_remove_audio**: Remove audio track from videos
- **video_convert_mp4**: Convert videos to MP4 format
- **video_adaptive_compress**: Adaptive video compression to target file size
- **video_split**: Split videos by I-frames or fixed duration

## Features

- **Unified Input**: Supports local paths, HTTP/HTTPS URLs, and S3 URLs (`s3://bucket/key`)
- **JSON Output**: All tools return JSON-serializable results
- **Composable**: Single-responsibility tools that can be combined in workflows
