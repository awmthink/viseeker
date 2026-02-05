"""
Video resolution resizing utility.

This tool resizes the first video stream to a target width/height, with multiple aspect policies.
Audio streams are kept unchanged by default (copied).

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


def _even(n: int) -> int:
    return n if n % 2 == 0 else n + 1


def _build_scale_filter(
    *,
    input_w: int,
    input_h: int,
    width: Optional[int],
    height: Optional[int],
    aspect_policy: str,
    pad_color: str,
) -> tuple[str, int, int, str]:
    """
    Return (vf, out_w, out_h, applied_policy).

    Rules:
      - If only width or only height is specified, keep aspect ratio.
      - If both are specified, output is exactly width x height.
        * stretch: allow distortion (simple scale)
        * contain: scale down to fit then pad
        * cover: scale up to cover then crop
        * pad: alias of contain (explicit)
    """

    if width is None and height is None:
        raise ValueError("At least one of width/height must be specified")

    if input_w <= 0 or input_h <= 0:
        raise ValueError("Invalid input resolution from probe")

    if width is not None and width <= 0:
        raise ValueError("width must be a positive integer")
    if height is not None and height <= 0:
        raise ValueError("height must be a positive integer")

    aspect_policy = (aspect_policy or "stretch").strip().lower()
    if aspect_policy not in {"stretch", "contain", "cover", "pad"}:
        raise ValueError(f"Unsupported aspect_policy: {aspect_policy}")
    if aspect_policy == "pad":
        aspect_policy = "contain"

    # Single-dimension scaling: always keep aspect ratio; ignore aspect_policy.
    if width is None or height is None:
        if width is None:
            out_h = int(height)
            out_w = _even(int(round(input_w * out_h / input_h)))
            vf = f"scale=-2:{out_h}"
            return vf, out_w, out_h, "keep_aspect"
        out_w = int(width)
        out_h = _even(int(round(input_h * out_w / input_w)))
        vf = f"scale={out_w}:-2"
        return vf, out_w, out_h, "keep_aspect"

    # Dual-dimension scaling.
    out_w = int(width)
    out_h = int(height)

    if aspect_policy == "stretch":
        vf = f"scale={out_w}:{out_h}"
        return vf, out_w, out_h, "stretch"

    if aspect_policy == "contain":
        vf = (
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
            f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color={pad_color}"
        )
        return vf, out_w, out_h, "contain"

    # cover
    vf = f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h}"
    return vf, out_w, out_h, "cover"


def resize_video(
    input_path: str,
    *,
    output: str,
    width: Optional[int] = None,
    height: Optional[int] = None,
    aspect_policy: str = "stretch",
    pad_color: str = "black",
    video_codec: str = "libx264",
    crf: int = 23,
    preset: str = "medium",
    bitrate: Optional[str] = None,
    pix_fmt: Optional[str] = "yuv420p",
    faststart: bool = True,
    timeout_s: int = 1800,
) -> Dict:
    """
    Resize a video to a target width/height.

    Args:
        input_path: Local path, HTTP/HTTPS URL, or S3 URL.
        output: Local output path or s3:// URL.
        width: Target width in pixels. Optional.
        height: Target height in pixels. Optional.
        aspect_policy: When both width and height are provided:
            - stretch: exact scale (may distort)
            - contain: keep aspect ratio, pad to target size
            - cover: keep aspect ratio, crop to target size
            - pad: alias of contain
          When only one dimension is provided, aspect ratio is always preserved.
        pad_color: Color used for padding (contain/pad).
        video_codec: Video encoder (e.g. libx264, libx265).
        crf: Constant Rate Factor for codecs that support it (x264/x265).
        preset: Encoder preset (x264/x265).
        bitrate: Optional target video bitrate string (e.g. 2000k). If set, uses ABR instead of CRF.
        pix_fmt: Optional pixel format (default yuv420p for compatibility).
        faststart: If True, enable MP4 faststart when output container supports it.
        timeout_s: Subprocess timeout seconds for ffmpeg.

    Returns:
        JSON-serializable dict with input/output info.
    """

    ffmpeg_path = ffmpeg.find_ffmpeg()
    ffprobe_path = ffmpeg.find_ffprobe()
    ffmpeg.verify_ffmpeg_tools(ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_path)

    if width is None and height is None:
        raise ValueError("At least one of width/height must be specified")
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")

    # Choose a reasonable default output filename for S3 temp path.
    default_name = os.path.basename(output) or "resized.mp4"

    with inputs.PreparedInput(input_path, mode="download") as local_in:
        p = probe.probe_video(local_in)
        if not p.has_video:
            raise ValueError("Input has no video stream")
        if not p.video_width or not p.video_height:
            raise ValueError("Failed to determine input video resolution")

        vf, out_w, out_h, applied_policy = _build_scale_filter(
            input_w=p.video_width,
            input_h=p.video_height,
            width=width,
            height=height,
            aspect_policy=aspect_policy,
            pad_color=pad_color,
        )

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
                "0:v:0",
                "-map",
                "0:a?",
                "-map",
                "0:s?",
                "-vf",
                vf,
                "-c:v",
                video_codec,
            ]

            # Rate control
            if bitrate:
                cmd += ["-b:v", str(bitrate)]
            else:
                cmd += ["-crf", str(int(crf))]

            if preset:
                cmd += ["-preset", str(preset)]
            if pix_fmt:
                cmd += ["-pix_fmt", str(pix_fmt)]

            # Keep audio/subtitles unchanged by default.
            cmd += ["-c:a", "copy", "-c:s", "copy"]

            if faststart:
                cmd += ["-movflags", "+faststart"]

            cmd += ["-y", out.local_path]

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
                "input_width": p.video_width,
                "input_height": p.video_height,
                "output_width": out_w,
                "output_height": out_h,
                "requested_width": width,
                "requested_height": height,
                "aspect_policy": aspect_policy,
                "applied_policy": applied_policy,
                "video_codec": video_codec,
                "crf": None if bitrate else int(crf),
                "bitrate": str(bitrate) if bitrate else None,
                "preset": preset,
                "pix_fmt": pix_fmt,
                "faststart": faststart,
            }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video_resize",
        description="Resize video resolution (local/HTTP/S3 input, local or S3 output).",
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
    parser.add_argument("--width", type=int, default=None, help="Target width in pixels.")
    parser.add_argument("--height", type=int, default=None, help="Target height in pixels.")
    parser.add_argument(
        "--aspect-policy",
        default="stretch",
        choices=("stretch", "contain", "cover", "pad"),
        help="Aspect policy when both width and height are provided (default: stretch).",
    )
    parser.add_argument(
        "--pad-color",
        default="black",
        help="Padding color for contain/pad (default: black).",
    )
    parser.add_argument(
        "--video-codec",
        default="libx264",
        help="Video codec (default: libx264). Example: libx265, libx264.",
    )
    parser.add_argument("--crf", type=int, default=23, help="CRF for x264/x265 (default: 23).")
    parser.add_argument(
        "--preset",
        default="medium",
        help="Encoder preset for x264/x265 (default: medium).",
    )
    parser.add_argument(
        "--bitrate",
        default=None,
        help="Target video bitrate (e.g. 2000k). If set, overrides CRF.",
    )
    parser.add_argument(
        "--pix-fmt",
        default="yuv420p",
        help="Pixel format (default: yuv420p).",
    )
    parser.add_argument(
        "--no-faststart",
        action="store_true",
        help="Disable MP4 faststart (moov atom at end).",
    )
    parser.add_argument(
        "--timeout-s",
        type=int,
        default=1800,
        help="ffmpeg timeout seconds (default: 1800).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    try:
        result = resize_video(
            args.input_path,
            output=args.output,
            width=args.width,
            height=args.height,
            aspect_policy=args.aspect_policy,
            pad_color=args.pad_color,
            video_codec=args.video_codec,
            crf=args.crf,
            preset=args.preset,
            bitrate=args.bitrate,
            pix_fmt=args.pix_fmt,
            faststart=not args.no_faststart,
            timeout_s=args.timeout_s,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

