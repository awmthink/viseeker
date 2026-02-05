## Purpose

This repo (**vision-ai-tools**) is a set of computer-vision utilities (image/video processing &
understanding) designed to be easy for AI coding agents to call.

## TL;DR (repo-root commands)

```bash
# Install dependencies (including dev)
uv sync --dev

# Run tests
uv run pytest

# Run a tool (CLI)
uv run python -m vision_ai_tools.video_metadata ./demo.mp4
```

## Repo layout

- `vision_ai_tools/`: reusable Python utilities (each tool is both a module and a CLI)
- `vision_ai_tools/_internal/`: shared internal helpers (S3, ffmpeg checks, input preparation)

## Conventions for tools (must follow)

### Tool shape: Python API + CLI

- **Python API**
  - Expose a top-level function (e.g. `extract_*`, `convert_*`, `split_*`) returning a
    **JSON-serializable** object (`dict` / `list[dict]` / primitives).
  - Prefer explicit keyword arguments with sensible defaults.
  - Raise clear exceptions (usually `ValueError`) on invalid inputs or failed processing.

- **CLI**
  - Must run via `python -m vision_ai_tools.<tool_name> ...`
  - Provide `main(argv: Optional[list[str]] = None) -> int`
  - Print **result JSON to stdout**
  - Print errors to **stderr** and return a **non-zero** exit code
  - Non-interactive (no prompts)

### Inputs / outputs (agent-friendly)

- **Inputs**
  - Prefer a single primary positional argument: `input_path`
  - Support local paths
  - When relevant, also support:
    - HTTP/HTTPS URLs
    - S3 URLs: `s3://bucket/key`
  - Reuse `vision_ai_tools._internal.inputs.PreparedInput` instead of re-implementing downloads/presigning.

- **Outputs**
  - Default to JSON outputs (stdout for CLI; return value for API).
  - If a tool produces artifacts (videos/images/manifests), support:
    - local output paths
    - S3 output URLs (`s3://...`)
  - Prefer reusing shared helpers (e.g. `vision_ai_tools._internal.outputs.PreparedOutput`,
    `vision_ai_tools._internal.s3.upload_path_to_s3`) rather than duplicating upload logic.

### Documentation

`TOOLS.md` is the “man page”. When adding a new tool, document:

- NAME
- SYNOPSIS (CLI + Python)
- DESCRIPTION
- ARGUMENTS / OPTIONS
- OUTPUT (minimal JSON schema/example)
- EXAMPLES
- NOTES / PITFALLS
- DEPENDENCIES (FFmpeg, Python deps)
- ENVIRONMENT VARIABLES (if any)

## Development workflow (uv)

### Install / update deps

```bash
uv sync --dev
```

### Run tests

```bash
uv run pytest
```

Optionally, run a single test file:

```bash
uv run pytest path/to/test_file.py
```

### Run a tool during development

```bash
uv run python -m vision_ai_tools.<tool_name> --help
uv run python -m vision_ai_tools.<tool_name> <input_path> [OPTIONS]
```

（注：包目录已改为 `vision_ai_tools/`，对应 CLI 为 `python -m vision_ai_tools.<tool_name>`。）

## Dependency & safety guidelines

- **Docstrings**: English, triple double quotes `\"\"\"`, first line = brief purpose, blank line,
  then details.
- **Readability over over-defensiveness**: prefer clear, minimal code; do not silently swallow
  exceptions (including `ImportError`) unless there is a strong reason.
- **Security**: never commit secrets/tokens/private data or `.env` files. Use placeholders in docs.

## Runtime dependencies

### FFmpeg

Many video tools depend on `ffprobe` / `ffmpeg` being available on PATH.

```bash
brew install ffmpeg
```

### S3 / S3-compatible storage

S3 config is resolved from environment variables (loaded via `python-dotenv` when available):

- `S3_ACCESS_KEY_ID`
- `S3_SECRET_ACCESS_KEY`
- `S3_ENDPOINT` (optional for S3-compatible storage)
- `S3_USE_HTTPS` (optional)
- `S3_VERIFY_SSL` (optional)

