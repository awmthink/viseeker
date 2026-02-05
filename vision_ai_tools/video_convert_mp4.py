"""
Convert various video formats to MP4.

This tool converts the first video stream (v:0) and the first audio stream (a:0) to an MP4
container. By default it prefers H.265 (libx265) for video and AAC for audio.

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


def _run_ffmpeg(cmd: list[str], *, timeout_s: int) -> None:
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        raise ValueError("ffmpeg execution timeout") from e
    except subprocess.CalledProcessError as e:
        raise ValueError(f"ffmpeg execution failed: {e.stderr}") from e


def convert_to_mp4(
    input_path: str,
    *,
    output: str,
    video_codec: str = "auto",
    crf: int = 28,
    preset: str = "medium",
    bitrate: Optional[str] = None,
    max_height: Optional[int] = None,
    pix_fmt: Optional[str] = "yuv420p",
    audio_codec: str = "aac",
    audio_bitrate: str = "128k",
    audio_sample_rate: Optional[int] = None,
    audio_channels: Optional[int] = None,
    timeout_s: int = 3600,
) -> Dict:
    """
    Convert a video to MP4.

    Args:
        input_path: Local path, HTTP/HTTPS URL, or S3 URL.
        output: Local output path or s3:// URL.
        video_codec: "auto" (prefer libx265 then fallback libx264), "libx265", or "libx264".
        crf: CRF for x264/x265 when bitrate is not set.
        preset: Encoder preset for x264/x265.
        bitrate: Optional target video bitrate (e.g. 2500k). If set, overrides CRF.
        max_height: Optional max output height; if input exceeds it, scale down preserving aspect.
        pix_fmt: Pixel format (default yuv420p).
        audio_codec: Audio codec (default aac).
        audio_bitrate: Audio bitrate (default 128k).
        audio_sample_rate: Optional audio sample rate (e.g. 44100).
        audio_channels: Optional number of audio channels (e.g. 2).
        timeout_s: Subprocess timeout seconds for ffmpeg.

    Returns:
        JSON-serializable dict describing the conversion and output location.
    """

    ffmpeg_path = ffmpeg.find_ffmpeg()
    ffprobe_path = ffmpeg.find_ffprobe()
    ffmpeg.verify_ffmpeg_tools(ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_path)

    video_codec = (video_codec or "auto").strip().lower()
    if video_codec not in {"auto", "libx265", "libx264"}:
        raise ValueError("video_codec must be one of: auto, libx265, libx264")
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    if max_height is not None and max_height <= 0:
        raise ValueError("max_height must be a positive integer")

    default_name = os.path.basename(output) or "converted.mp4"

    with inputs.PreparedInput(input_path, mode="download") as local_in:
        p = probe.probe_video(local_in)
        if not p.has_video:
            raise ValueError("Input has no video stream")

        vf = None
        scaled = False
        output_height = p.video_height
        output_width = p.video_width
        if max_height and p.video_height and p.video_height > max_height:
            vf = f"scale=-2:{int(max_height)}"
            scaled = True
            output_height = int(max_height)
            if p.video_width and p.video_height:
                output_width = int(round(p.video_width * output_height / p.video_height))

        def build_cmd(chosen_vcodec: str) -> list[str]:
            cmd = [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                local_in,
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
            ]
            if vf:
                cmd += ["-vf", vf]

            cmd += ["-c:v", chosen_vcodec]
            if bitrate:
                cmd += ["-b:v", str(bitrate)]
            else:
                cmd += ["-crf", str(int(crf))]
            if preset:
                cmd += ["-preset", str(preset)]
            if pix_fmt:
                cmd += ["-pix_fmt", str(pix_fmt)]

            cmd += ["-c:a", audio_codec]
            if audio_bitrate:
                cmd += ["-b:a", str(audio_bitrate)]
            if audio_sample_rate:
                cmd += ["-ar", str(int(audio_sample_rate))]
            if audio_channels:
                cmd += ["-ac", str(int(audio_channels))]

            cmd += ["-movflags", "+faststart", "-y"]
            return cmd

        with outputs.PreparedOutput(
            output,
            default_filename=default_name,
            content_type="video/mp4",
        ) as out:
            chosen = video_codec
            if video_codec == "auto":
                # Prefer H.265; fallback to H.264 if encoder is unavailable.
                try:
                    cmd = build_cmd("libx265") + [out.local_path]
                    _run_ffmpeg(cmd, timeout_s=timeout_s)
                    chosen = "libx265"
                except ValueError:
                    cmd = build_cmd("libx264") + [out.local_path]
                    _run_ffmpeg(cmd, timeout_s=timeout_s)
                    chosen = "libx264"
            else:
                cmd = build_cmd(video_codec) + [out.local_path]
                _run_ffmpeg(cmd, timeout_s=timeout_s)

            return {
                "input_path": input_path,
                "output": output,
                "local_path": None if out.s3_url else out.local_path,
                "s3_url": out.s3_url,
                "selected_video_stream": "v:0",
                "selected_audio_stream": "a:0" if p.has_audio else None,
                "video_codec": chosen,
                "crf": None if bitrate else int(crf),
                "bitrate": str(bitrate) if bitrate else None,
                "preset": preset,
                "pix_fmt": pix_fmt,
                "audio_codec": audio_codec,
                "audio_bitrate": audio_bitrate,
                "audio_sample_rate": int(audio_sample_rate) if audio_sample_rate else None,
                "audio_channels": int(audio_channels) if audio_channels else None,
                "scaled": scaled,
                "max_height": int(max_height) if max_height else None,
                "input_width": p.video_width,
                "input_height": p.video_height,
                "output_width": output_width,
                "output_height": output_height,
            }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video_convert_mp4",
        description="Convert video to MP4 (prefer H.265, select first audio track).",
    )
    parser.add_argument(
        "input_path",
        help="Video path/URL (e.g., ./a.mov, https://..., s3://bucket/key).",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output path or s3:// URL (e.g., ./out.mp4, s3://bucket/out.mp4).",
    )
    parser.add_argument(
        "--video-codec",
        default="auto",
        choices=("auto", "libx265", "libx264"),
        help="Video codec (default: auto).",
    )
    parser.add_argument("--crf", type=int, default=28, help="CRF for x264/x265 (default: 28).")
    parser.add_argument("--preset", default="medium", help="Encoder preset (default: medium).")
    parser.add_argument(
        "--bitrate",
        default=None,
        help="Target video bitrate (e.g. 2500k). If set, overrides CRF.",
    )
    parser.add_argument(
        "--max-height",
        type=int,
        default=None,
        help="Max output height (downscale only, keep aspect).",
    )
    parser.add_argument("--pix-fmt", default="yuv420p", help="Pixel format (default: yuv420p).")
    parser.add_argument("--audio-codec", default="aac", help="Audio codec (default: aac).")
    parser.add_argument(
        "--audio-bitrate",
        default="128k",
        help="Audio bitrate (default: 128k).",
    )
    parser.add_argument(
        "--audio-sample-rate",
        type=int,
        default=None,
        help="Audio sample rate (e.g. 44100).",
    )
    parser.add_argument(
        "--audio-channels",
        type=int,
        default=None,
        help="Audio channels (e.g. 2).",
    )
    parser.add_argument(
        "--timeout-s",
        type=int,
        default=3600,
        help="ffmpeg timeout seconds (default: 3600).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    try:
        result = convert_to_mp4(
            args.input_path,
            output=args.output,
            video_codec=args.video_codec,
            crf=args.crf,
            preset=args.preset,
            bitrate=args.bitrate,
            max_height=args.max_height,
            pix_fmt=args.pix_fmt,
            audio_codec=args.audio_codec,
            audio_bitrate=args.audio_bitrate,
            audio_sample_rate=args.audio_sample_rate,
            audio_channels=args.audio_channels,
            timeout_s=args.timeout_s,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

