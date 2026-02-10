#!/usr/bin/env python3
"""
Video understanding via a generic multimodal VLM (multi-frame).

This module provides:
- a Python API: describe_video(...)
- a CLI: python -m viseeker.video_describe ...

Inputs can be:
- local video file paths
- HTTP/HTTPS URLs
- S3 URLs (s3://bucket/key)

Behavior:
- Extract frames at a fixed interval (default 1 fps) with a maximum of 128 frames.
- If the video is longer than 128 seconds, uniformly sample 128 frames over the full duration.
- For each frame, prepend a text tag like "[0.0 second]" to indicate its timestamp.
- Write frames to local JPEG files and call Ark Responses API (volcenginesdkarkruntime) using
  `input_image` items with `file://...` URLs (SDK uploads the files via Files API).

Limitations:
- Input video duration must be < 5 minutes (300 seconds).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

from ._internal import inputs, probe

# Load environment variables from a .env file if present.
load_dotenv()


DEFAULT_PROMPT = (
    "You are an expert video captioning assistant. "
    "You will be given a sequence of video frames with timestamps "
    "sampled from a full video. "
    "Write a caption for the entire video: summarize the scene, main "
    "subjects, actions, and key events in a clear, coherent way. "
    "Use timestamps only when helpful. Avoid speculation beyond what "
    "is visible."
)

MAX_VIDEO_DURATION_S = 300.0


def _require_cv2() -> None:
    """
    Ensure OpenCV and numpy are available.
    """
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except ImportError as e:
        raise ValueError(
            "OpenCV and numpy are required for video_describe. "
            "Install: pip install opencv-python-headless numpy"
        ) from e


def _create_async_client():
    """
    Create an Ark Runtime client for the configured VLM endpoint.
    """
    resolved_api_key = os.getenv("VLM_API_KEY")
    if not resolved_api_key:
        raise ValueError("Missing API key: set VLM_API_KEY in environment.")
    resolved_base_url = os.getenv("VLM_BASE_URL")
    if not resolved_base_url:
        raise ValueError("Missing base URL: set VLM_BASE_URL in environment.")

    try:
        from volcenginesdkarkruntime import AsyncArk
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "The 'volcengine-python-sdk[ark]' package is required for video_describe.\n"
            "Install it with: pip install 'volcengine-python-sdk[ark]'"
        ) from e

    return AsyncArk(base_url=resolved_base_url, api_key=resolved_api_key)


def _extract_response_text(resp: Any) -> str:
    """
    Best-effort extraction of text from an Ark Responses API response.
    """
    output = getattr(resp, "output", None) or []
    if not output:
        return ""
    out_parts: list[str] = []
    for item in output:
        if getattr(item, "type", None) != "message":
            continue
        content_list = getattr(item, "content", None) or []
        for c in content_list:
            if getattr(c, "type", None) == "output_text":
                t = getattr(c, "text", "")
                if t:
                    out_parts.append(t)
    return "\n".join(out_parts).strip()


def _compute_timestamps(duration_s: float, *, fps: float, max_frames: int) -> list[float]:
    if max_frames <= 0:
        raise ValueError("max_frames must be positive")
    if fps <= 0:
        raise ValueError("fps must be positive")

    safe_end = max(float(duration_s) - 1e-3, 0.0)

    if safe_end <= 0:
        return [0.0]

    # Prefer sampling by fps; if that would exceed max_frames, fall back to uniform max_frames.
    desired_frames = int(safe_end * float(fps)) + 1
    if desired_frames <= max_frames:
        step = 1.0 / float(fps)
        return [min(i * step, safe_end) for i in range(desired_frames)]

    if max_frames == 1:
        return [0.0]

    return [i * safe_end / (max_frames - 1) for i in range(max_frames)]


def _stream_sample_frames_opencv(
    video_input_spec: str,
    *,
    duration_s: float,
    fps: float,
    max_frames: int,
) -> list[tuple[float, bytes]]:
    """
    Stream-read video via OpenCV and return sampled frames as (timestamp_s, jpeg_bytes).
    """
    _require_cv2()
    import cv2

    cap = cv2.VideoCapture(video_input_spec)
    if not cap.isOpened():
        raise ValueError(f"Failed to open video: {video_input_spec}")
    try:
        cap_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if cap_fps <= 0:
            cap_fps = 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

        safe_end = max(float(duration_s) - 1e-3, 0.0)
        desired = int(safe_end * float(fps)) + 1 if safe_end > 0 else 1

        # If frame count is known, sample by frame indices.
        if total_frames > 0:
            if desired <= max_frames:
                step = 1.0 / float(fps)
                times = [min(i * step, safe_end) for i in range(desired)]
                idx = [int(round(t * float(cap_fps))) for t in times]
                idx = [min(max(i, 0), total_frames - 1) for i in idx]
            else:
                if max_frames == 1:
                    idx = [0]
                else:
                    idx = [
                        int(round(i * (total_frames - 1) / (max_frames - 1)))
                        for i in range(max_frames)
                    ]
                    idx = [min(max(i, 0), total_frames - 1) for i in idx]

            wanted = set(idx)
            target_count = min(len(wanted), max_frames)
            picked: list[tuple[float, bytes]] = []
            frame_idx = 0
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                if frame_idx in wanted:
                    ts = float(frame_idx) / float(cap_fps)
                    ok2, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                    if ok2:
                        picked.append((ts, bytes(buf)))
                        if len(picked) >= target_count:
                            break
                frame_idx += 1
            return picked[:max_frames]

        # Frame count unknown (common for some HTTP streams): stream and
        # pick when crossing target times.
        targets = _compute_timestamps(duration_s, fps=fps, max_frames=max_frames)
        picked2: list[tuple[float, bytes]] = []
        j = 0
        frame_idx = 0
        eps = 1e-6
        while j < len(targets):
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            ts = float(frame_idx) / float(cap_fps)
            if ts + eps >= float(targets[j]):
                ok2, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                if ok2:
                    picked2.append((float(targets[j]), bytes(buf)))
                    j += 1
                    if len(picked2) >= max_frames:
                        break
            frame_idx += 1
        return picked2[:max_frames]
    finally:
        cap.release()


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.2f} KiB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.2f} MiB"
    return f"{n / (1024 * 1024 * 1024):.2f} GiB"


def _log_payload_size(
    *,
    frame_count: int,
    prompt_text: str,
    timestamps: list[float],
    total_jpeg_bytes: int,
    total_b64_chars: int,
    detail: str,
    max_frames: int,
    fps: float,
) -> None:
    # This is an estimate of the JSON payload size for the /responses request.
    # With file:// inputs, large bytes are uploaded separately by SDK's Files API.
    timestamp_text_chars = sum(len(f"[{t:.1f} second]") for t in timestamps)
    prompt_chars = len(prompt_text or "")
    overhead_chars = 2000 + frame_count * 200  # rough JSON/key overhead
    estimated_payload_bytes = total_b64_chars + timestamp_text_chars + prompt_chars + overhead_chars

    print(
        (
            "[video_describe] payload_size_estimate: "
            f"frames={frame_count}, detail={detail}, fps={fps}, max_frames={max_frames}, "
            f"total_jpeg={_format_bytes(total_jpeg_bytes)}, "
            f"total_file_url_chars={total_b64_chars}, "
            f"estimated_payload={_format_bytes(int(estimated_payload_bytes))}"
        ),
        file=sys.stderr,
    )


def _to_file_url(local_path: str) -> str:
    # Ensure correct file URL (e.g. file:///abs/path) and proper escaping.
    return Path(local_path).absolute().as_uri()


def _sample_frames_to_files(
    video_input_spec: str,
    *,
    duration_s: float,
    fps: float,
    max_frames: int,
    output_dir: str,
) -> list[tuple[float, str, int]]:
    """
    Sample frames and write JPEGs to output_dir.

    Returns:
        List of (timestamp_s, local_path, jpeg_bytes).
    """
    sampled = _stream_sample_frames_opencv(
        video_input_spec,
        duration_s=duration_s,
        fps=fps,
        max_frames=max_frames,
    )
    out: list[tuple[float, str, int]] = []
    for idx, (ts, jpeg_bytes) in enumerate(sampled):
        out_path = os.path.join(output_dir, f"frame_{idx:04d}.jpg")
        with open(out_path, "wb") as f:
            f.write(jpeg_bytes)
        out.append((float(ts), out_path, int(len(jpeg_bytes))))
    return out


async def _call_vlm_responses_api(*, model: str, input_items: list[dict]) -> str:
    """
    Call Ark Responses API using volcenginesdkarkruntime.

    Note:
        file:// URLs in input_image will be uploaded to Files API automatically by the async
        Responses client before creating the response.
    """
    client = _create_async_client()
    try:
        resp = await client.responses.create(model=model, input=input_items)
        return _extract_response_text(resp)
    finally:
        with contextlib.suppress(Exception):
            await client.close()


def describe_video(
    input_path: str,
    *,
    prompt: Optional[str] = None,
    detail: str = "low",
    fps: float = 1.0,
    max_frames: int = 128,
) -> dict:
    """
    Describe/answer questions about a video using a multimodal VLM (multi-frame).

    Args:
        input_path: Local path, HTTP/HTTPS URL, or S3 URL to the video.
        prompt: User prompt/question. When None, a built-in default prompt is used.
        detail: Image detail level for the VLM, one of "low" or "high".
        fps: Sampling fps for short videos (default 1.0).
        max_frames: Maximum number of frames to send (default 128).

    Returns:
        JSON-serializable dict with the model response and metadata.
    """
    used_prompt = prompt or DEFAULT_PROMPT
    used_detail = (detail or "low").strip().lower()
    if used_detail not in {"low", "high"}:
        raise ValueError('detail must be "low" or "high"')

    used_model = (os.getenv("VLM_MODEL_ID") or "").strip()
    if not used_model:
        raise ValueError("Missing model id: set VLM_MODEL_ID in environment.")

    # Use URL mode so that S3 inputs are converted to presigned HTTPS URLs. OpenCV VideoCapture
    # can stream from HTTP(S), so we avoid downloading remote inputs when possible.
    with inputs.PreparedInput(input_path, mode="url") as input_spec:
        p = probe.probe_video(input_spec)
        if not p.has_video:
            raise ValueError("Input has no video stream")
        duration_s = float(p.duration_s or 0.0)

        if duration_s >= MAX_VIDEO_DURATION_S:
            raise ValueError(
                f"Video is too long: duration {duration_s:.3f}s, "
                f"limit is < {MAX_VIDEO_DURATION_S:.3f}s"
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            frames = _sample_frames_to_files(
                input_spec,
                duration_s=duration_s,
                fps=fps,
                max_frames=max_frames,
                output_dir=temp_dir,
            )

            content_parts: list[dict] = [{"type": "input_text", "text": used_prompt}]
            total_jpeg_bytes = 0
            total_file_url_chars = 0
            timestamps: list[float] = []

            for ts, local_path, jpeg_n in frames:
                file_url = _to_file_url(local_path)
                total_jpeg_bytes += int(jpeg_n)
                total_file_url_chars += len(file_url)
                timestamps.append(float(ts))

                content_parts.append({"type": "input_text", "text": f"[{ts:.1f} second]"})
                content_parts.append(
                    {
                        "type": "input_image",
                        "image_url": file_url,
                        "detail": used_detail,
                    }
                )

            input_items = [{"type": "message", "role": "user", "content": content_parts}]

            _log_payload_size(
                frame_count=len(frames),
                prompt_text=used_prompt,
                timestamps=timestamps,
                total_jpeg_bytes=total_jpeg_bytes,
                total_b64_chars=total_file_url_chars,
                detail=used_detail,
                max_frames=max_frames,
                fps=fps,
            )

            text = asyncio.run(_call_vlm_responses_api(model=used_model, input_items=input_items))

            return {
                "text": text,
                "detail": used_detail,
                "duration_s": duration_s,
                "frame_count": len(frames),
            }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video_describe",
        description=(
            "Understand a video by sampling frames and calling a multimodal VLM. "
            "Note: input duration must be < 5 minutes (300s)."
        ),
    )
    parser.add_argument(
        "input_path",
        help="Video path/URL (e.g., ./a.mp4, https://..., s3://bucket/key).",
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help="User prompt/question. When omitted, use a built-in default prompt.",
    )
    parser.add_argument(
        "--detail",
        choices=("low", "high"),
        default="low",
        help='Image detail level for all frames: "low" or "high" (default: low).',
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=1.0,
        help="Sampling FPS for short videos (default: 1.0).",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=128,
        help="Max number of frames to send (default: 128).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        result = describe_video(
            args.input_path,
            prompt=args.prompt,
            detail=args.detail,
            fps=args.fps,
            max_frames=args.max_frames,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
