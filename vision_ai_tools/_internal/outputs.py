"""
Shared output handling utilities.

Many tools write an output file and optionally upload it to S3 (s3://bucket/key). This module
provides a small abstraction that:
  - resolves whether the destination is local or S3
  - provides a local temp path to write to when destination is S3
  - uploads on successful completion
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Optional

from . import inputs, s3


@dataclass(frozen=True)
class OutputResult:
    """Hold a resolved output target."""

    local_path: str
    s3_url: Optional[str]


class PreparedOutput:
    """
    Context manager that prepares an output path.

    If output is a local path, it is returned as-is and nothing is uploaded.
    If output is an s3:// URL, a temp file path is returned and the file is uploaded on success.
    """

    def __init__(
        self,
        output: str,
        *,
        default_filename: str = "output.bin",
        content_type: Optional[str] = None,
    ) -> None:
        self.output = output
        self.default_filename = default_filename
        self.content_type = content_type
        self._temp_dir: Optional[str] = None
        self._local_path: Optional[str] = None

    def __enter__(self) -> OutputResult:
        if not self.output:
            raise ValueError("output must be provided")

        if inputs.is_s3_url(self.output):
            self._temp_dir = tempfile.mkdtemp()
            filename = os.path.basename(self.output) or self.default_filename
            self._local_path = os.path.join(self._temp_dir, filename)
            return OutputResult(local_path=self._local_path, s3_url=self.output)

        # local path
        out_dir = os.path.dirname(os.path.abspath(self.output))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        return OutputResult(local_path=self.output, s3_url=None)

    def __exit__(self, exc_type, exc, tb) -> None:
        # Only upload if there was no exception and destination is S3.
        if exc_type is None and self._local_path and inputs.is_s3_url(self.output):
            extra_args = {"ContentType": self.content_type} if self.content_type else None
            s3.upload_path_to_s3(self._local_path, self.output, extra_args=extra_args)

        # Cleanup temp artifacts.
        if self._local_path and os.path.exists(self._local_path):
            try:
                os.remove(self._local_path)
            except OSError:
                pass
        if self._temp_dir and os.path.exists(self._temp_dir):
            try:
                os.rmdir(self._temp_dir)
            except OSError:
                pass

