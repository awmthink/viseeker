"""
Shared ffprobe helpers.

This module runs ffprobe and returns a small, stable subset of metadata used by multiple tools:
duration, width/height, FPS, and audio/video presence.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, Optional

from . import ffmpeg


@dataclass(frozen=True)
class ProbeResult:
    """A simplified, stable probe output."""

    duration_s: float
    format_name: str
    bit_rate: int
    has_video: bool
    has_audio: bool
    video_codec: Optional[str]
    video_width: Optional[int]
    video_height: Optional[int]
    video_fps: Optional[float]
    audio_codec: Optional[str]


def _parse_fps(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    v = value.strip()
    if "/" in v:
        try:
            num, den = v.split("/", 1)
            n = float(num)
            d = float(den)
            return n / d if d > 0 else None
        except Exception:
            return None
    try:
        return float(v)
    except Exception:
        return None


def run_ffprobe_json(input_spec: str, *, timeout_s: int = 60) -> Dict[str, Any]:
    """
    Run ffprobe and return raw JSON output.
    """

    ffprobe_path = ffmpeg.find_ffprobe()
    ffmpeg.verify_ffprobe(ffprobe_path)

    cmd = [
        ffprobe_path,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        input_spec,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout_s)
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired as e:
        raise ValueError("ffprobe execution timeout") from e
    except subprocess.CalledProcessError as e:
        raise ValueError(f"ffprobe execution failed: {e.stderr}") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse ffprobe output: {e}") from e


def probe_video(input_spec: str, *, timeout_s: int = 60) -> ProbeResult:
    """
    Probe a media file and return a simplified ProbeResult.
    """

    data = run_ffprobe_json(input_spec, timeout_s=timeout_s)
    format_info: Dict[str, Any] = data.get("format", {}) or {}
    streams = data.get("streams", []) or []

    duration_s = float(format_info.get("duration", 0)) if format_info.get("duration") else 0.0
    format_name = str(format_info.get("format_name", "") or "")
    bit_rate = int(format_info.get("bit_rate", 0) or 0)

    video_stream = None
    audio_stream = None
    for s in streams:
        t = s.get("codec_type")
        if t == "video" and video_stream is None:
            video_stream = s
        elif t == "audio" and audio_stream is None:
            audio_stream = s

    has_video = video_stream is not None
    has_audio = audio_stream is not None

    video_codec = None
    video_width = None
    video_height = None
    video_fps = None
    if video_stream:
        video_codec = video_stream.get("codec_name")
        if video_stream.get("width") is not None:
            video_width = int(video_stream.get("width") or 0) or None
        if video_stream.get("height") is not None:
            video_height = int(video_stream.get("height") or 0) or None
        video_fps = _parse_fps(video_stream.get("r_frame_rate")) or _parse_fps(
            video_stream.get("avg_frame_rate")
        )

    audio_codec = None
    if audio_stream:
        audio_codec = audio_stream.get("codec_name")

    return ProbeResult(
        duration_s=duration_s,
        format_name=format_name,
        bit_rate=bit_rate,
        has_video=has_video,
        has_audio=has_audio,
        video_codec=video_codec,
        video_width=video_width,
        video_height=video_height,
        video_fps=video_fps,
        audio_codec=audio_codec,
    )
