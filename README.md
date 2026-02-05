# vision-ai-tools

面向 **LLM Agent** 的计算机视觉工具集：提供一组可复用的 **Python 工具模块**（并带有简单 CLI），用于图片/视频的处理、理解与数据工作流集成。

- **输入统一**：本地文件路径、HTTP/HTTPS URL、S3 URL（`s3://bucket/key`）
- **输出友好**：尽量输出 **JSON 可序列化** 结果，便于 Agent 直接消费
- **可组合**：工具尽量保持“单一职责”，方便在 Agent workflow 里拼装

## 快速开始

### 安装依赖

```bash
uv sync --dev
```

部分能力依赖 FFmpeg（`ffprobe`/`ffmpeg`）：

```bash
brew install ffmpeg
```

### 运行一个工具（CLI）

```bash
python -m vision_ai_tools.video_metadata ./demo.mp4
```

```bash
python -m vision_ai_tools.video_keyframes ./demo.mp4 --method I_frame --max-keyframes 20 --output-dir ./out
```

## 当前包含的工具

详细用法见 `TOOLS.md`（包含参数表、输出结构、示例）。

- **`vision_ai_tools/video_metadata.py`**：视频元数据提取（时长、编码、分辨率、帧率等），输出 JSON
- **`vision_ai_tools/video_keyframes.py`**：视频关键帧抽取（多算法 + 回退），可输出图片与 manifest
- **`vision_ai_tools/video_resize.py`**：按指定宽高调整分辨率（单维等比缩放），支持多种宽高比策略，支持 S3 输出
- **`vision_ai_tools/video_remove_audio.py`**：移除音轨（stream copy，不重编码）
- **`vision_ai_tools/video_convert_mp4.py`**：各种格式转 MP4（默认优先 H.265，自动选择第一个音轨，支持限高缩放）
- **`vision_ai_tools/video_adaptive_compress.py`**：按目标文件大小自适应压缩（fps → 分辨率 → 码率控制）
- **`vision_ai_tools/video_split.py`**：视频切分（基于 I 帧或固定秒长），支持 manifest 与 S3 上传

## 示例：视频关键帧抽取（多算法）

工具模块：`vision_ai_tools/video_keyframes.py`

- **支持算法**：`I_frame`（推荐，最高效）、`difference`、`histogram`、`optical_flow`
- **输入**：本地文件、`http(s)://`、`s3://bucket/key`
- **输出**：JPG/PNG；可写本地目录；可选上传到 S3；可选输出 manifest JSON（本地或 S3）

### CLI 用法

I 帧方式（推荐）：

```bash
python -m vision_ai_tools.video_keyframes ./demo.mp4 \
  --method I_frame \
  --max-keyframes 30 \
  --min-interval-s 0.5 \
  --output-dir ./out \
  --image-format jpg \
  --manifest ./out/manifest.json
```

多算法兜底（按顺序尝试，首个产出关键帧的方法胜出）：

```bash
python -m vision_ai_tools.video_keyframes ./demo.mp4 \
  --method I_frame,difference,histogram,optical_flow \
  --max-keyframes 20 \
  --output-dir ./out
```

输出到 S3：

```bash
python -m vision_ai_tools.video_keyframes s3://my-bucket/videos/demo.mp4 \
  --method I_frame \
  --max-keyframes 50 \
  --s3-output-prefix s3://my-bucket/keyframes/demo/ \
  --manifest s3://my-bucket/keyframes/demo/manifest.json
```

### Python API 用法

```python
from vision_ai_tools.video_keyframes import extract_video_keyframes

results = extract_video_keyframes(
    "./demo.mp4",
    method=["I_frame", "difference", "histogram"],
    max_keyframes=20,
    min_interval_s=0.5,
    output_dir="./out",
    image_format="jpg",
    manifest_path="./out/manifest.json",
)
print(results)
```

### 性能建议

- **优先使用 `I_frame`**：避免 OpenCV 全量解码，通常速度/资源占用更优。

