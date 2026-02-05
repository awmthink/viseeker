#!/usr/bin/env python3
"""
Split a video into segments.

Modes:
  - iframe: split using I-frame timestamps (keyframes)
  - fixed: split by a fixed segment duration in seconds

By default this tool uses stream copy (-c copy) to avoid re-encoding.

Inputs can be local files, HTTP/HTTPS URLs, or S3 URLs (s3://bucket/key).
Outputs are written to a local directory, and can optionally be uploaded to S3.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

from ._internal import ffmpeg, inputs, probe, s3


def _run_ffmpeg(cmd: list[str], *, timeout_s: int) -> None:
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        raise ValueError("ffmpeg execution timeout") from e
    except subprocess.CalledProcessError as e:
        raise ValueError(f"ffmpeg execution failed: {e.stderr}") from e


def _probe_iframe_timestamps(local_path: str, *, timeout_s: int = 300) -> List[float]:
    """
    Return sorted I-frame timestamps (seconds) for v:0.
    """
    ffprobe_path = ffmpeg.find_ffprobe()
    ffmpeg.verify_ffprobe(ffprobe_path)
    cmd = [
        ffprobe_path,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "frame=best_effort_timestamp_time,pict_type",
        "-of",
        "csv=p=0",
        local_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        raise ValueError("ffprobe execution timeout") from e
    except subprocess.CalledProcessError as e:
        raise ValueError(f"ffprobe execution failed: {e.stderr}") from e

    ts: List[float] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        t, pict = parts[0], parts[1]
        if pict != "I":
            continue
        if t in {"N/A", ""}:
            continue
        try:
            ts.append(float(t))
        except ValueError:
            continue
    ts = sorted(set(t for t in ts if t >= 0))
    return ts


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _pick_ext(local_input_path: str, requested_ext: Optional[str]) -> str:
    if requested_ext:
        e = requested_ext.lower().lstrip(".")
        return e
    ext = os.path.splitext(local_input_path)[1].lower().lstrip(".")
    return ext or "mp4"


def _list_segments(output_dir: str, prefix: str, ext: str) -> List[str]:
    files = []
    for name in sorted(os.listdir(output_dir)):
        if not name.startswith(prefix):
            continue
        if ext and not name.lower().endswith(f".{ext}"):
            continue
        files.append(os.path.join(output_dir, name))
    return files


def _upload_segments_to_s3(paths: List[str], s3_prefix: str, *, content_type: str) -> List[str]:
    urls: List[str] = []
    for pth in paths:
        key = os.path.basename(pth)
        dest = s3.join_s3_url(s3_prefix, key)
        s3.upload_path_to_s3(pth, dest, extra_args={"ContentType": content_type})
        urls.append(dest)
    return urls


def _write_manifest(manifest_path: str, data: object) -> None:
    if inputs.is_s3_url(manifest_path):
        with tempfile.TemporaryDirectory() as td:
            tmp = os.path.join(td, "manifest.json")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            s3.upload_path_to_s3(tmp, manifest_path, extra_args={"ContentType": "application/json"})
        return
    out_dir = os.path.dirname(os.path.abspath(manifest_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def split_video(
    input_path: str,
    *,
    mode: str,
    output_dir: Optional[str] = None,
    s3_output_prefix: Optional[str] = None,
    manifest_path: Optional[str] = None,
    segment_s: Optional[float] = None,
    every_n_iframes: int = 1,
    max_segments: Optional[int] = None,
    prefix: str = "segment_",
    ext: Optional[str] = None,
    timeout_s: int = 3600,
) -> Dict:
    """
    Split a video into segments.

    Args:
        input_path: Local path, HTTP/HTTPS URL, or S3 URL.
        mode: "iframe" or "fixed".
        output_dir: Local directory for segments. If not provided and s3_output_prefix is set,
            a temporary directory is used.
        s3_output_prefix: Optional s3://bucket/prefix/ to upload segments.
        manifest_path: Optional manifest JSON path (local or s3://).
        segment_s: Segment length (seconds) for fixed mode.
        every_n_iframes: Use every N-th I-frame as a split point in iframe mode.
        max_segments: Optional cap on number of output segments (downsamples split points).
        prefix: Output filename prefix (default: segment_).
        ext: Output extension/container (default: same as input; fallback mp4).
        timeout_s: ffmpeg timeout seconds.
    """

    mode = (mode or "").strip().lower()
    if mode not in {"iframe", "fixed"}:
        raise ValueError("mode must be one of: iframe, fixed")
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    if every_n_iframes <= 0:
        raise ValueError("every_n_iframes must be positive")
    if max_segments is not None and max_segments <= 0:
        raise ValueError("max_segments must be positive when provided")
    if mode == "fixed":
        if segment_s is None or segment_s <= 0:
            raise ValueError("segment_s must be provided and positive for fixed mode")

    ffmpeg_path = ffmpeg.find_ffmpeg()
    ffprobe_path = ffmpeg.find_ffprobe()
    ffmpeg.verify_ffmpeg_tools(ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_path)

    temp_output_dir = None
    if not output_dir:
        if not s3_output_prefix:
            raise ValueError("output_dir must be provided unless s3_output_prefix is set")
        temp_output_dir = tempfile.mkdtemp()
        output_dir = temp_output_dir
    _ensure_dir(output_dir)
    try:
        with inputs.PreparedInput(input_path, mode="download") as local_in:
            p = probe.probe_video(local_in)
            if not p.has_video:
                raise ValueError("Input has no video stream")

            out_ext = _pick_ext(local_in, ext)
            pattern = os.path.join(output_dir, f"{prefix}%04d.{out_ext}")

            cmd: List[str] = [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                local_in,
                "-map",
                "0",
                "-c",
                "copy",
                "-f",
                "segment",
                "-reset_timestamps",
                "1",
            ]

            split_times: Optional[List[float]] = None
            if mode == "fixed":
                cmd += ["-segment_time", f"{float(segment_s):.6f}"]
            else:
                ts = _probe_iframe_timestamps(local_in)
                # Use every N-th I-frame, skipping t=0 boundary.
                split_points = [t for i, t in enumerate(ts) if i % every_n_iframes == 0 and t > 0]
                if max_segments is not None and len(split_points) + 1 > max_segments:
                    # Downsample split points uniformly by index.
                    keep = max_segments - 1
                    if keep <= 0:
                        split_points = []
                    else:
                        step = max(1, len(split_points) // keep)
                        split_points = split_points[::step][:keep]
                split_times = split_points
                times_str = ",".join(f"{t:.6f}" for t in split_points)
                if times_str:
                    cmd += ["-segment_times", times_str]
                # If no times_str, ffmpeg will produce a single segment (whole file).

            cmd += ["-y", pattern]
            _run_ffmpeg(cmd, timeout_s=timeout_s)

            segment_paths = _list_segments(output_dir, prefix=prefix, ext=out_ext)
            if not segment_paths:
                raise ValueError("No segments were produced")

            # Build segment metadata (duration by probing each segment).
            segments: List[Dict] = []
            start_s = 0.0
            for idx, seg_path in enumerate(segment_paths):
                seg_probe = probe.probe_video(seg_path)
                dur = float(seg_probe.duration_s or 0.0)
                if split_times is not None and idx < len(split_times):
                    # For iframe mode, boundaries come from split_times; still prefer probed duration.
                    pass
                seg = {
                    "index": idx,
                    "local_path": seg_path,
                    "s3_url": None,
                    "start_s": start_s,
                    "duration_s": dur,
                    "end_s": start_s + dur if dur else None,
                    "filename": os.path.basename(seg_path),
                }
                segments.append(seg)
                start_s += dur if dur else 0.0

            if s3_output_prefix:
                content_type = "video/mp4" if out_ext == "mp4" else "application/octet-stream"
                urls = _upload_segments_to_s3(
                    segment_paths, s3_output_prefix, content_type=content_type
                )
                for seg, url in zip(segments, urls):
                    seg["s3_url"] = url

            result = {
                "input_path": input_path,
                "mode": mode,
                "segment_s": float(segment_s) if segment_s is not None else None,
                "every_n_iframes": int(every_n_iframes) if mode == "iframe" else None,
                "max_segments": int(max_segments) if max_segments is not None else None,
                "output_dir": output_dir if not temp_output_dir else None,
                "s3_output_prefix": s3_output_prefix,
                "segments": segments,
            }

            if manifest_path:
                _write_manifest(manifest_path, result)
                result["manifest"] = manifest_path

            return result
    finally:
        if temp_output_dir and os.path.exists(temp_output_dir):
            try:
                for name in os.listdir(temp_output_dir):
                    try:
                        os.remove(os.path.join(temp_output_dir, name))
                    except OSError:
                        pass
                os.rmdir(temp_output_dir)
            except OSError:
                pass


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video_split",
        description="Split video into segments (iframe-based or fixed seconds).",
    )
    parser.add_argument(
        "input_path",
        help="Video path/URL (e.g., ./a.mp4, https://..., s3://bucket/key).",
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=("iframe", "fixed"),
        help="Split mode: iframe or fixed.",
    )
    parser.add_argument("--output-dir", default=None, help="Local output directory for segments.")
    parser.add_argument(
        "--s3-output-prefix",
        default=None,
        help="Optional S3 prefix to upload segments: s3://bucket/prefix/",
    )
    parser.add_argument("--manifest", default=None, help="Manifest JSON path (local or s3://).")
    parser.add_argument(
        "--segment-s",
        type=float,
        default=None,
        help="Segment duration in seconds (fixed mode).",
    )
    parser.add_argument(
        "--every-n-iframes",
        type=int,
        default=1,
        help="Use every N-th I-frame as a split point (iframe mode).",
    )
    parser.add_argument(
        "--max-segments",
        type=int,
        default=None,
        help="Cap number of output segments (iframe mode; downsamples split points).",
    )
    parser.add_argument(
        "--prefix",
        default="segment_",
        help="Output filename prefix (default: segment_).",
    )
    parser.add_argument(
        "--ext",
        default=None,
        help="Output extension/container (default: same as input; fallback mp4).",
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
        result = split_video(
            args.input_path,
            mode=args.mode,
            output_dir=args.output_dir,
            s3_output_prefix=args.s3_output_prefix,
            manifest_path=args.manifest,
            segment_s=args.segment_s,
            every_n_iframes=args.every_n_iframes,
            max_segments=args.max_segments,
            prefix=args.prefix,
            ext=args.ext,
            timeout_s=args.timeout_s,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
