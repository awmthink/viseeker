"""
Shared input preparation utilities.

Many tools accept `input_path` that can be:
- a local filesystem path
- an HTTP/HTTPS URL
- an S3 URL: s3://bucket/key

This module centralizes detection, download-to-temp, and optional S3 presigning.
"""

from __future__ import annotations

import os
import tempfile
import urllib.parse
from dataclasses import dataclass
from typing import Literal, Optional

import requests

from . import s3


def is_http_url(path: str) -> bool:
    parsed = urllib.parse.urlparse(path)
    return parsed.scheme in ("http", "https")


def is_s3_url(path: str) -> bool:
    parsed = urllib.parse.urlparse(path)
    return parsed.scheme == "s3"


def is_local_file(path: str) -> bool:
    return os.path.exists(path) and os.path.isfile(path)


def _filename_from_url(url: str, fallback: str) -> str:
    name = os.path.basename(urllib.parse.urlparse(url).path)
    return name or fallback


def download_http_to_path(url: str, dest_path: str) -> None:
    response = requests.get(url, stream=True, timeout=30)
    response.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            f.write(chunk)


@dataclass
class PreparedInput:
    """
    Context manager for preparing an input spec.

    In download mode, remote inputs are downloaded to a temp directory and a local file path is
    returned.
    In url mode:
    - local file paths are returned as-is
    - HTTP/HTTPS URLs are returned as-is
    - S3 URLs are converted to a presigned HTTP(S) URL
    """

    input_path: str
    mode: Literal["download", "url"] = "download"
    presign_expires_in: int = 3600
    _temp_dir: Optional[str] = None
    _local_path: Optional[str] = None

    def __enter__(self) -> str:
        if self.mode not in {"download", "url"}:
            raise ValueError(f"Unsupported mode: {self.mode}")

        if is_local_file(self.input_path):
            return self.input_path

        if self.mode == "url":
            if is_s3_url(self.input_path):
                return s3.presign_get_object_url(
                    self.input_path, expires_in=self.presign_expires_in
                )
            if is_http_url(self.input_path):
                return self.input_path
            raise ValueError(f"Unsupported input path: {self.input_path}")

        # download mode
        if is_http_url(self.input_path):
            self._temp_dir = tempfile.mkdtemp()
            filename = _filename_from_url(self.input_path, fallback="input_file")
            self._local_path = os.path.join(self._temp_dir, filename)
            download_http_to_path(self.input_path, self._local_path)
            return self._local_path

        if is_s3_url(self.input_path):
            self._temp_dir = tempfile.mkdtemp()
            _bucket, key = s3.parse_s3_url(self.input_path)
            filename = os.path.basename(key) or "input_file"
            self._local_path = os.path.join(self._temp_dir, filename)
            s3.download_s3_to_path(self.input_path, self._local_path)
            return self._local_path

        raise ValueError(f"Unsupported input path: {self.input_path}")

    def __exit__(self, exc_type, exc, tb) -> None:
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
