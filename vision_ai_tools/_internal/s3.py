"""
Utilities for working with S3 and S3-compatible storage.

This module centralizes environment-based configuration for credentials and endpoints, so multiple
tools can reuse the same behavior consistently.
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional, Tuple

import boto3
from botocore.config import Config as BotocoreConfig
from botocore.exceptions import ClientError
from dotenv import load_dotenv


@lru_cache(maxsize=1)
def _load_local_dotenv_once() -> None:
    """
    Load a local .env file once per process.

    This uses python-dotenv to find `.env` by walking up from the current working directory. It
    does not override existing environment variables by default.
    """

    load_dotenv(override=False)


@dataclass(frozen=True)
class S3EnvConfig:
    """
    Hold S3 configuration resolved from environment variables.

    The following environment variables are used when present:
    - S3_ACCESS_KEY_ID
    - S3_SECRET_ACCESS_KEY
    - S3_ENDPOINT
    - S3_USE_HTTPS
    - S3_VERIFY_SSL
    """

    access_key_id: Optional[str]
    secret_access_key: Optional[str]
    endpoint_url: Optional[str]
    verify_ssl: Optional[bool]


def coerce_bool(value: object) -> Optional[bool]:
    """
    Convert common truthy/falsey values into a boolean.

    Returns None when the value is None or cannot be interpreted.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "t", "yes", "y", "on"}:
            return True
        if v in {"0", "false", "f", "no", "n", "off"}:
            return False
    return None


def build_endpoint_url(endpoint: Optional[str], use_https: Optional[bool]) -> Optional[str]:
    """
    Build a full endpoint URL from a host-like endpoint string.

    If the endpoint already includes a scheme, it is returned unchanged. Otherwise, the scheme is
    inferred from use_https (defaults to https when not specified).
    """

    if not endpoint:
        return None
    endpoint = endpoint.strip()
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    scheme = "https" if use_https is not False else "http"
    return f"{scheme}://{endpoint}"


def load_s3_env_config() -> S3EnvConfig:
    """
    Load S3 configuration from environment variables.

    This function does not validate connectivity; it only resolves configuration values.
    """

    _load_local_dotenv_once()

    access_key_id = os.environ.get("S3_ACCESS_KEY_ID") or None
    secret_access_key = os.environ.get("S3_SECRET_ACCESS_KEY") or None
    endpoint = os.environ.get("S3_ENDPOINT") or None
    use_https = coerce_bool(os.environ.get("S3_USE_HTTPS"))
    verify_ssl = coerce_bool(os.environ.get("S3_VERIFY_SSL"))

    endpoint_url = build_endpoint_url(endpoint, use_https)
    return S3EnvConfig(
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        endpoint_url=endpoint_url,
        verify_ssl=verify_ssl,
    )


def parse_s3_url(url: str) -> Tuple[str, str]:
    """
    Parse an S3 URL into (bucket, key).

    Expected format: s3://bucket/key
    """

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Unsupported S3 URL: {url}")
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not key:
        raise ValueError(f"Unsupported S3 URL (missing key): {url}")
    return bucket, key


def get_s3_client(config: Optional[S3EnvConfig] = None):
    """
    Create an S3 client using environment-based configuration.

    If credentials are not provided via S3_* env vars, boto3's default credential chain is used.
    Checksums are disabled for S3-compatible storage compatibility.
    """

    config = config or load_s3_env_config()
    verify = True if config.verify_ssl is None else config.verify_ssl

    # Disable automatic checksums for S3-compatible storage compatibility (boto3 1.26+)
    client_config = BotocoreConfig(
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    )

    return boto3.client(
        "s3",
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
        endpoint_url=config.endpoint_url,
        verify=verify,
        config=client_config,
    )


def download_s3_to_path(url: str, dest_path: str, config: Optional[S3EnvConfig] = None) -> None:
    """
    Download an S3 object to a local file path.

    This uses the environment-based S3 client configuration.
    """

    bucket, key = parse_s3_url(url)
    client = get_s3_client(config=config)
    try:
        client.download_file(bucket, key, dest_path)
    except ClientError as e:
        raise ValueError(f"Failed to download S3 file: {e}") from e


def presign_get_object_url(
    url: str, expires_in: int = 3600, config: Optional[S3EnvConfig] = None
) -> str:
    """
    Generate a presigned GET URL for an S3 object.

    This enables tools like ffprobe to read S3 objects over HTTP(S) without downloading them to
    disk in this tool. The ffprobe/ffmpeg build must support the chosen transport.
    """

    bucket, key = parse_s3_url(url)
    client = get_s3_client(config=config)
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
    except ClientError as e:
        raise ValueError(f"Failed to generate presigned URL: {e}") from e


def upload_path_to_s3(
    source_path: str,
    dest_url: str,
    *,
    config: Optional[S3EnvConfig] = None,
    extra_args: Optional[dict] = None,
) -> None:
    """
    Upload a local file to an S3 URL.

    Args:
        source_path: Local filesystem path to upload.
        dest_url: Destination S3 URL in the form s3://bucket/key.
        config: Optional resolved env configuration for the S3 client.
        extra_args: Optional boto3 ExtraArgs (e.g. {"ContentType": "image/jpeg"}).
    """

    bucket, key = parse_s3_url(dest_url)
    client = get_s3_client(config=config)
    try:
        if extra_args:
            client.upload_file(source_path, bucket, key, ExtraArgs=extra_args)
        else:
            client.upload_file(source_path, bucket, key)
    except ClientError as e:
        raise ValueError(f"Failed to upload S3 file: {e}") from e


def join_s3_url(prefix_url: str, key: str) -> str:
    """
    Join an S3 URL prefix and a key suffix.

    Example:
        >>> join_s3_url("s3://bucket/prefix/", "a/b.jpg")
        's3://bucket/prefix/a/b.jpg'
    """

    bucket, prefix_key = parse_s3_url(prefix_url.rstrip("/") + "/_")
    prefix_key = prefix_key.rsplit("/", 1)[0]  # drop the trailing "_" placeholder
    key = key.lstrip("/")
    joined = "/".join([p for p in [prefix_key, key] if p])
    return f"s3://{bucket}/{joined}"

