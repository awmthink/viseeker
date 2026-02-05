"""
Remove audio tracks from a video without re-encoding.

This tool uses ffmpeg with stream copy to drop all audio streams while preserving original video
encoding and quality (no video re-encode).

Inputs can be local files, HTTP/HTTPS URLs, or S3 URLs (s3://bucket/key).
Outputs can be written to a local path or uploaded to S3 (s3://bucket/key).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Dict, Optional

from ._internal import ffmpeg, inputs, outputs, probe


def remove_video_audio(
    input_path: str,
    *,
    output: str,
    timeout_s: int = 600,
) -> Dict:
    """
    Remove all audio streams from the input video.

    Args:
        input_path: Local path, HTTP/HTTPS URL, or S3 URL.
        output: Local output path or s3:// URL.
        timeout_s: Subprocess timeout seconds for ffmpeg.

    Returns:
        JSON-serializable dict describing the operation and output location.
    """

    ffmpeg_path = ffmpeg.find_ffmpeg()
    ffprobe_path = ffmpeg.find_ffprobe()
    ffmpeg.verify_ffmpeg_tools(ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_path)

    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")

    default_name = os.path.basename(output) or "no_audio.mp4"

    with inputs.PreparedInput(input_path, mode="download") as local_in:
        p = probe.probe_video(local_in)
        raw = probe.run_ffprobe_json(local_in, timeout_s=60)
        audio_streams = [
            s for s in (raw.get("streams") or []) if (s.get("codec_type") == "audio")
        ]

        with outputs.PreparedOutput(
            output,
            default_filename=default_name,
            content_type="video/mp4" if output.lower().endswith(".mp4") else None,
        ) as out:
            cmd = [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                local_in,
                "-map",
                "0",
                "-map",
                "-0:a",
                "-c",
                "copy",
                "-y",
                out.local_path,
            ]
            try:
                subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout_s)
            except subprocess.TimeoutExpired as e:
                raise ValueError("ffmpeg execution timeout") from e
            except subprocess.CalledProcessError as e:
                raise ValueError(f"ffmpeg execution failed: {e.stderr}") from e

            return {
                "input_path": input_path,
                "output": output,
                "local_path": None if out.s3_url else out.local_path,
                "s3_url": out.s3_url,
                "has_audio_before": bool(p.has_audio),
                "audio_streams_removed": len(audio_streams),
                "note": "Streams were copied; video was not re-encoded.",
            }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video_remove_audio",
        description="Remove all audio streams from a video using ffmpeg stream copy.",
    )
    parser.add_argument(
        "input_path",
        help="Video path/URL (e.g., ./a.mp4, https://..., s3://bucket/key).",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output path or s3:// URL (e.g., ./out.mp4, s3://bucket/out.mp4).",
    )
    parser.add_argument(
        "--timeout-s",
        type=int,
        default=600,
        help="ffmpeg timeout seconds (default: 600).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    try:
        result = remove_video_audio(args.input_path, output=args.output, timeout_s=args.timeout_s)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

