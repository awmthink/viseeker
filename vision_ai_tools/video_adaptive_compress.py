"""
Adaptive video compression to meet a target file size.

This tool tries multiple strategies in order:
  1) Reduce FPS (down to a minimum, default 8fps)
  2) Reduce resolution (down to a minimum, default 480p height)
  3) Apply bitrate control to hit the target size

Inputs can be local files, HTTP/HTTPS URLs, or S3 URLs (s3://bucket/key).
Outputs can be written to a local path or uploaded to S3 (s3://bucket/key).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ._internal import ffmpeg, inputs, outputs, probe


@dataclass(frozen=True)
class Attempt:
    fps: Optional[float]
    height: Optional[int]
    video_codec: str
    crf: Optional[int]
    video_bitrate: Optional[str]
    audio_bitrate: str
    output_bytes: int


def _run_ffmpeg(cmd: list[str], *, timeout_s: int) -> None:
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        raise ValueError("ffmpeg execution timeout") from e
    except subprocess.CalledProcessError as e:
        raise ValueError(f"ffmpeg execution failed: {e.stderr}") from e


def _encode_once(
    *,
    ffmpeg_path: str,
    input_path: str,
    output_path: str,
    chosen_vcodec: str,
    fps: Optional[float],
    height: Optional[int],
    crf: Optional[int],
    video_bitrate: Optional[str],
    preset: str,
    pix_fmt: str,
    audio_codec: str,
    audio_bitrate: str,
    timeout_s: int,
) -> None:
    filters: List[str] = []
    if fps is not None:
        filters.append(f"fps=fps={float(fps):.6f}")
    if height is not None:
        filters.append(f"scale=-2:{int(height)}")

    cmd: List[str] = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        input_path,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
    ]
    if filters:
        cmd += ["-vf", ",".join(filters)]

    cmd += ["-c:v", chosen_vcodec]
    if video_bitrate:
        cmd += ["-b:v", str(video_bitrate)]
        # Keep rate control stable; allow small bursts.
        cmd += ["-maxrate", str(video_bitrate), "-bufsize", str(video_bitrate)]
    else:
        if crf is None:
            raise ValueError("crf must be provided when video_bitrate is not set")
        cmd += ["-crf", str(int(crf))]
    if preset:
        cmd += ["-preset", str(preset)]
    if pix_fmt:
        cmd += ["-pix_fmt", str(pix_fmt)]

    cmd += ["-c:a", audio_codec, "-b:a", str(audio_bitrate)]
    cmd += ["-movflags", "+faststart", "-y", output_path]

    _run_ffmpeg(cmd, timeout_s=timeout_s)


def _pick_codec(auto_codec: str) -> Tuple[str, Optional[str]]:
    """
    Return (preferred, fallback) codec names.
    """
    auto_codec = (auto_codec or "auto").strip().lower()
    if auto_codec == "auto":
        return "libx265", "libx264"
    if auto_codec in {"libx265", "libx264"}:
        return auto_codec, None
    raise ValueError("video_codec must be one of: auto, libx265, libx264")


def adaptive_compress_video(
    input_path: str,
    *,
    output: str,
    target_bytes: Optional[int] = None,
    target_mb: Optional[float] = None,
    video_codec: str = "auto",
    crf: int = 28,
    preset: str = "medium",
    pix_fmt: str = "yuv420p",
    audio_codec: str = "aac",
    audio_bitrate: str = "128k",
    min_fps: float = 8.0,
    min_height: int = 480,
    timeout_s: int = 7200,
) -> Dict:
    """
    Compress a video to meet a target file size.

    Args:
        input_path: Local path, HTTP/HTTPS URL, or S3 URL.
        output: Local output path or s3:// URL.
        target_bytes: Target size in bytes.
        target_mb: Target size in megabytes (MiB). If set, converted to bytes.
        video_codec: auto/libx265/libx264.
        crf: CRF for x264/x265 for FPS/scale stages (before bitrate control).
        preset: Encoder preset.
        pix_fmt: Pixel format.
        audio_codec: Audio codec.
        audio_bitrate: Audio bitrate for AAC (e.g. 128k).
        min_fps: Minimum FPS (default 8).
        min_height: Minimum height (default 480).
        timeout_s: Per-attempt ffmpeg timeout seconds.

    Returns:
        JSON-serializable dict with attempts and final output info.
    """

    if (target_bytes is None) == (target_mb is None):
        raise ValueError("Provide exactly one of target_bytes or target_mb")
    if target_mb is not None:
        if target_mb <= 0:
            raise ValueError("target_mb must be positive")
        target_bytes = int(target_mb * 1024 * 1024)
    assert target_bytes is not None
    if target_bytes <= 0:
        raise ValueError("target_bytes must be positive")
    if min_fps <= 0:
        raise ValueError("min_fps must be positive")
    if min_height <= 0:
        raise ValueError("min_height must be positive")
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")

    ffmpeg_path = ffmpeg.find_ffmpeg()
    ffprobe_path = ffmpeg.find_ffprobe()
    ffmpeg.verify_ffmpeg_tools(ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_path)

    preferred_codec, fallback_codec = _pick_codec(video_codec)
    default_name = os.path.basename(output) or "compressed.mp4"

    attempts: List[Attempt] = []

    with inputs.PreparedInput(input_path, mode="download") as local_in:
        p = probe.probe_video(local_in)
        if not p.has_video:
            raise ValueError("Input has no video stream")
        if not p.duration_s or p.duration_s <= 0:
            raise ValueError("Failed to determine input duration")

        input_fps = p.video_fps or 30.0
        input_h = p.video_height or 0

        # Stage 1: FPS reduction candidates (keep -> 24 -> 15 -> 12 -> 8).
        fps_candidates: List[Optional[float]] = [None]
        for f in [24.0, 15.0, 12.0, float(min_fps)]:
            if f < input_fps and f not in fps_candidates:
                fps_candidates.append(f)
        # Ensure min_fps is present if it's below input.
        if float(min_fps) < input_fps and float(min_fps) not in fps_candidates:
            fps_candidates.append(float(min_fps))

        # Stage 2: resolution candidates (only downscale; keep -> 2160 -> 1440 -> 1080 -> 720 -> 480).
        height_candidates: List[Optional[int]] = [None]
        for h in [2160, 1440, 1080, 720, int(min_height)]:
            if input_h and h < input_h and h >= int(min_height) and h not in height_candidates:
                height_candidates.append(h)
        if input_h and int(min_height) < input_h and int(min_height) not in height_candidates:
            height_candidates.append(int(min_height))

        def try_encode(
            *,
            fps_value: Optional[float],
            height_value: Optional[int],
            chosen_codec: str,
            crf_value: Optional[int],
            v_bitrate: Optional[str],
            a_bitrate: str,
            out_path: str,
        ) -> int:
            _encode_once(
                ffmpeg_path=ffmpeg_path,
                input_path=local_in,
                output_path=out_path,
                chosen_vcodec=chosen_codec,
                fps=fps_value,
                height=height_value,
                crf=crf_value,
                video_bitrate=v_bitrate,
                preset=preset,
                pix_fmt=pix_fmt,
                audio_codec=audio_codec,
                audio_bitrate=a_bitrate,
                timeout_s=timeout_s,
            )
            return int(os.path.getsize(out_path))

        with tempfile.TemporaryDirectory() as td:
            temp_out = os.path.join(td, "attempt.mp4")
            s3_url = output if inputs.is_s3_url(output) else None
            local_path = None if s3_url else output

            # Helper to run with auto codec fallback.
            def encode_with_codec_fallback(
                *,
                fps_value: Optional[float],
                height_value: Optional[int],
                crf_value: Optional[int],
                v_bitrate: Optional[str],
                a_bitrate: str,
            ) -> Tuple[str, int]:
                try:
                    size = try_encode(
                        fps_value=fps_value,
                        height_value=height_value,
                        chosen_codec=preferred_codec,
                        crf_value=crf_value,
                        v_bitrate=v_bitrate,
                        a_bitrate=a_bitrate,
                        out_path=temp_out,
                    )
                    return preferred_codec, size
                except ValueError:
                    if not fallback_codec:
                        raise
                    size = try_encode(
                        fps_value=fps_value,
                        height_value=height_value,
                        chosen_codec=fallback_codec,
                        crf_value=crf_value,
                        v_bitrate=v_bitrate,
                        a_bitrate=a_bitrate,
                        out_path=temp_out,
                    )
                    return fallback_codec, size

            # Stage 1: FPS only (keep height).
            last_fps: Optional[float] = None
            for f in fps_candidates:
                codec_used, size = encode_with_codec_fallback(
                    fps_value=f,
                    height_value=None,
                    crf_value=int(crf),
                    v_bitrate=None,
                    a_bitrate=audio_bitrate,
                )
                attempts.append(
                    Attempt(
                        fps=f,
                        height=None,
                        video_codec=codec_used,
                        crf=int(crf),
                        video_bitrate=None,
                        audio_bitrate=audio_bitrate,
                        output_bytes=size,
                    )
                )
                last_fps = f
                if size <= target_bytes:
                    # Write final output.
                    with outputs.PreparedOutput(output, default_filename=default_name, content_type="video/mp4") as out:
                        os.replace(temp_out, out.local_path)
                    return {
                        "input_path": input_path,
                        "output": output,
                        "local_path": local_path,
                        "s3_url": s3_url,
                        "target_bytes": target_bytes,
                        "actual_bytes": size,
                        "strategy": "fps",
                        "success": True,
                        "attempts": [a.__dict__ for a in attempts],
                    }

            # Stage 2: resolution (use last FPS tried, typically min_fps).
            last_height: Optional[int] = None
            for h in height_candidates[1:]:
                codec_used, size = encode_with_codec_fallback(
                    fps_value=last_fps,
                    height_value=h,
                    crf_value=int(crf),
                    v_bitrate=None,
                    a_bitrate=audio_bitrate,
                )
                attempts.append(
                    Attempt(
                        fps=last_fps,
                        height=h,
                        video_codec=codec_used,
                        crf=int(crf),
                        video_bitrate=None,
                        audio_bitrate=audio_bitrate,
                        output_bytes=size,
                    )
                )
                last_height = h
                if size <= target_bytes:
                    with outputs.PreparedOutput(output, default_filename=default_name, content_type="video/mp4") as out:
                        os.replace(temp_out, out.local_path)
                    return {
                        "input_path": input_path,
                        "output": output,
                        "local_path": local_path,
                        "s3_url": s3_url,
                        "target_bytes": target_bytes,
                        "actual_bytes": size,
                        "strategy": "fps+scale",
                        "success": True,
                        "attempts": [a.__dict__ for a in attempts],
                    }

            # Stage 3: bitrate control (use most aggressive fps/height from stages above).
            # Compute target video bitrate from duration and target size.
            duration = float(p.duration_s)
            total_bps = int(target_bytes * 8 / max(0.1, duration))

            # Parse audio bitrate like "128k".
            a_bps = 128_000
            try:
                if audio_bitrate.lower().endswith("k"):
                    a_bps = int(float(audio_bitrate[:-1]) * 1000)
                elif audio_bitrate.lower().endswith("m"):
                    a_bps = int(float(audio_bitrate[:-1]) * 1_000_000)
                else:
                    a_bps = int(audio_bitrate)
            except Exception:
                a_bps = 128_000

            # Leave some headroom for container overhead.
            target_video_bps = max(200_000, int(total_bps * 0.95) - a_bps)
            v_bitrate = f"{int(target_video_bps/1000)}k"

            codec_used, size = encode_with_codec_fallback(
                fps_value=last_fps if last_fps is not None else float(min_fps),
                height_value=last_height if last_height is not None else (int(min_height) if input_h and input_h > int(min_height) else None),
                crf_value=None,
                v_bitrate=v_bitrate,
                a_bitrate=audio_bitrate,
            )
            attempts.append(
                Attempt(
                    fps=last_fps if last_fps is not None else float(min_fps),
                    height=last_height,
                    video_codec=codec_used,
                    crf=None,
                    video_bitrate=v_bitrate,
                    audio_bitrate=audio_bitrate,
                    output_bytes=size,
                )
            )

            # Always return the last attempt as output (even if it fails to hit target),
            # so callers can decide next steps.
            with outputs.PreparedOutput(output, default_filename=default_name, content_type="video/mp4") as out:
                os.replace(temp_out, out.local_path)

            return {
                "input_path": input_path,
                "output": output,
                "local_path": local_path,
                "s3_url": s3_url,
                "target_bytes": target_bytes,
                "actual_bytes": size,
                "strategy": "fps+scale+bitrate",
                "success": bool(size <= target_bytes),
                "attempts": [a.__dict__ for a in attempts],
            }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video_adaptive_compress",
        description="Adaptively compress a video to meet a target size (fps -> scale -> bitrate).",
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
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--target-bytes", type=int, default=None, help="Target size in bytes.")
    group.add_argument("--target-mb", type=float, default=None, help="Target size in MiB.")
    parser.add_argument(
        "--video-codec",
        default="auto",
        choices=("auto", "libx265", "libx264"),
        help="Video codec (default: auto).",
    )
    parser.add_argument("--crf", type=int, default=28, help="CRF for x264/x265 stages (default: 28).")
    parser.add_argument("--preset", default="medium", help="Encoder preset (default: medium).")
    parser.add_argument("--pix-fmt", default="yuv420p", help="Pixel format (default: yuv420p).")
    parser.add_argument("--audio-codec", default="aac", help="Audio codec (default: aac).")
    parser.add_argument("--audio-bitrate", default="128k", help="Audio bitrate (default: 128k).")
    parser.add_argument("--min-fps", type=float, default=8.0, help="Minimum FPS (default: 8).")
    parser.add_argument(
        "--min-height",
        type=int,
        default=480,
        help="Minimum height for downscaling (default: 480).",
    )
    parser.add_argument(
        "--timeout-s",
        type=int,
        default=7200,
        help="Per-attempt ffmpeg timeout seconds (default: 7200).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    try:
        result = adaptive_compress_video(
            args.input_path,
            output=args.output,
            target_bytes=args.target_bytes,
            target_mb=args.target_mb,
            video_codec=args.video_codec,
            crf=args.crf,
            preset=args.preset,
            pix_fmt=args.pix_fmt,
            audio_codec=args.audio_codec,
            audio_bitrate=args.audio_bitrate,
            min_fps=args.min_fps,
            min_height=args.min_height,
            timeout_s=args.timeout_s,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("success", True) else 2
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

