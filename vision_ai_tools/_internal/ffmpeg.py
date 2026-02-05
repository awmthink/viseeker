"""
Helpers for locating and verifying FFmpeg binaries.

Centralizes common logic used across multiple tools (ffmpeg/ffprobe discovery and validation).
"""

from __future__ import annotations

import shutil
import subprocess


_INSTALL_HINT = (
    "Please install FFmpeg:\n"
    "  - macOS: brew install ffmpeg\n"
    "  - Ubuntu/Debian: apt-get install ffmpeg\n"
    "  - Windows: Download from https://ffmpeg.org/download.html"
)


def find_binary(name: str) -> str:
    """
    Find an executable on PATH.

    Returns the resolved path if found, otherwise returns the original name so subprocess can still
    attempt PATH resolution.
    """
    return shutil.which(name) or name


def find_ffprobe() -> str:
    """Find ffprobe on PATH."""
    return find_binary("ffprobe")


def find_ffmpeg() -> str:
    """Find ffmpeg on PATH."""
    return find_binary("ffmpeg")


def _verify_binary(name: str, path: str) -> None:
    """
    Verify a binary can be executed.

    Runs `<path> -version` and raises ValueError with installation hints when it fails.
    """
    try:
        subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise ValueError(f"{name} not found at {path}. {_INSTALL_HINT}") from e


def verify_ffprobe(ffprobe_path: str) -> None:
    """Verify ffprobe is available and executable."""
    _verify_binary("ffprobe", ffprobe_path)


def verify_ffmpeg(ffmpeg_path: str) -> None:
    """Verify ffmpeg is available and executable."""
    _verify_binary("ffmpeg", ffmpeg_path)


def verify_ffmpeg_tools(*, ffprobe_path: str, ffmpeg_path: str) -> None:
    """Verify both ffprobe and ffmpeg are available."""
    verify_ffprobe(ffprobe_path)
    verify_ffmpeg(ffmpeg_path)

