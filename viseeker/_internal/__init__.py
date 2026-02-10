"""
Internal shared utilities for viseeker.

This package is intentionally **not** part of the public tool surface. It exists so multiple
modules under `viseeker/` can reuse common behavior (S3, ffmpeg checks, input preparation)
without copying code.
"""

# Intentionally do not import submodules here to avoid side effects at import time.
# Callers should import the needed module explicitly, e.g.:
#   from . import ffmpeg
#   from . import s3

__all__ = ["ffmpeg", "inputs", "outputs", "probe", "s3"]
