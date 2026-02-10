#!/usr/bin/env python3
"""
S3 storage command line utilities.

Provides common S3 operations:
- ls: List buckets, objects, or multipart uploads
- du: Calculate size and count of objects and multipart uploads
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError

from ._internal.s3 import get_s3_client


def _human_readable_size(size_bytes: int) -> str:
    """Convert bytes to human readable format."""
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} EB"


def _parse_s3_bucket_url(url: str) -> Tuple[str, Optional[str]]:
    """
    Parse S3 URL into bucket and optional prefix.

    Args:
        url: S3 URL (s3://bucket or s3://bucket/prefix)

    Returns:
        Tuple of (bucket, prefix) where prefix may be None
    """
    import urllib.parse

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "s3":
        raise ValueError(f"URL must use s3:// scheme: {url}")

    bucket = parsed.netloc
    if not bucket:
        raise ValueError(f"S3 URL missing bucket: {url}")

    # Path includes leading slash, strip it to get prefix
    prefix = parsed.path.lstrip("/")
    if not prefix:
        prefix = None

    return bucket, prefix


def list_buckets(s3_client) -> List[Dict[str, Any]]:
    """List all S3 buckets."""
    try:
        response = s3_client.list_buckets()
        buckets = []
        for bucket in response.get("Buckets", []):
            buckets.append(
                {
                    "name": bucket["Name"],
                    "creation_date": bucket["CreationDate"].isoformat(),
                }
            )
        return buckets
    except ClientError as e:
        raise ValueError(f"Failed to list buckets: {e}") from e


def list_objects(
    s3_client,
    bucket: str,
    prefix: Optional[str] = None,
    delimiter: Optional[str] = None,
    max_keys: Optional[int] = None,
) -> Dict[str, Any]:
    """List objects in a bucket with optional prefix."""
    try:
        params = {"Bucket": bucket}
        if prefix:
            params["Prefix"] = prefix
        if delimiter:
            params["Delimiter"] = delimiter
        if max_keys:
            params["MaxKeys"] = max_keys

        paginator = s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(**params)

        objects = []
        prefixes = []
        total_size = 0

        for page in pages:
            for obj in page.get("Contents", []):
                objects.append(
                    {
                        "key": obj["Key"],
                        "size": obj["Size"],
                        "last_modified": obj["LastModified"].isoformat(),
                        "etag": obj.get("ETag", "").strip('"'),
                    }
                )
                total_size += obj["Size"]

            for common_prefix in page.get("CommonPrefixes", []):
                prefixes.append(common_prefix["Prefix"])

        result = {
            "bucket": bucket,
            "prefix": prefix,
            "objects": objects,
            "total_count": len(objects),
            "total_size": total_size,
            "total_size_human": _human_readable_size(total_size),
        }
        if prefixes:
            result["common_prefixes"] = prefixes

        return result

    except ClientError as e:
        raise ValueError(f"Failed to list objects: {e}") from e


def list_multipart_uploads(
    s3_client,
    bucket: str,
    prefix: Optional[str] = None,
) -> Dict[str, Any]:
    """List in-progress multipart uploads."""
    try:
        params = {"Bucket": bucket}
        if prefix:
            params["Prefix"] = prefix

        paginator = s3_client.get_paginator("list_multipart_uploads")
        pages = paginator.paginate(**params)

        uploads = []
        total_parts = 0

        for page in pages:
            for upload in page.get("Uploads", []):
                upload_info = {
                    "key": upload["Key"],
                    "upload_id": upload["UploadId"],
                    "initiated": upload["Initiated"].isoformat(),
                    "storage_class": upload.get("StorageClass", "STANDARD"),
                }
                uploads.append(upload_info)

        # Get parts count for each upload
        for upload in uploads:
            try:
                parts_paginator = s3_client.get_paginator("list_parts")
                parts_pages = parts_paginator.paginate(
                    Bucket=bucket,
                    Key=upload["key"],
                    UploadId=upload["upload_id"],
                )
                parts_count = 0
                for parts_page in parts_pages:
                    parts_count += len(parts_page.get("Parts", []))
                upload["parts_count"] = parts_count
                total_parts += parts_count
            except ClientError:
                upload["parts_count"] = None

        return {
            "bucket": bucket,
            "prefix": prefix,
            "uploads": uploads,
            "total_count": len(uploads),
            "total_parts": total_parts,
        }

    except ClientError as e:
        raise ValueError(f"Failed to list multipart uploads: {e}") from e


def calculate_du(
    s3_client,
    bucket: str,
    prefix: Optional[str] = None,
    include_multipart: bool = True,
) -> Dict[str, Any]:
    """
    Calculate disk usage (size and count) for objects and multipart uploads.

    Args:
        s3_client: Boto3 S3 client
        bucket: Bucket name
        prefix: Optional prefix filter
        include_multipart: Whether to include multipart uploads in calculation

    Returns:
        Dictionary with size and count statistics
    """
    result = {
        "bucket": bucket,
        "prefix": prefix,
        "objects": {
            "count": 0,
            "size": 0,
            "size_human": "0 B",
        },
    }

    # List all objects and calculate totals
    try:
        params = {"Bucket": bucket}
        if prefix:
            params["Prefix"] = prefix

        paginator = s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(**params)

        total_size = 0
        total_count = 0

        for page in pages:
            for obj in page.get("Contents", []):
                total_size += obj["Size"]
                total_count += 1

        result["objects"]["count"] = total_count
        result["objects"]["size"] = total_size
        result["objects"]["size_human"] = _human_readable_size(total_size)

    except ClientError as e:
        raise ValueError(f"Failed to calculate objects usage: {e}") from e

    # Include multipart uploads if requested
    if include_multipart:
        try:
            params = {"Bucket": bucket}
            if prefix:
                params["Prefix"] = prefix

            paginator = s3_client.get_paginator("list_multipart_uploads")
            pages = paginator.paginate(**params)

            multipart_count = 0
            total_parts = 0

            for page in pages:
                for upload in page.get("Uploads", []):
                    multipart_count += 1
                    # Get parts count for each upload
                    try:
                        parts_paginator = s3_client.get_paginator("list_parts")
                        parts_pages = parts_paginator.paginate(
                            Bucket=bucket,
                            Key=upload["Key"],
                            UploadId=upload["UploadId"],
                        )
                        for parts_page in parts_pages:
                            total_parts += len(parts_page.get("Parts", []))
                    except ClientError:
                        pass

            result["multipart_uploads"] = {
                "count": multipart_count,
                "total_parts": total_parts,
            }

        except ClientError as e:
            raise ValueError(f"Failed to calculate multipart uploads: {e}") from e

    return result


def cmd_ls(args) -> int:
    """Handle the ls command."""
    try:
        s3_client = get_s3_client()

        # If no URL provided, list all buckets
        if not args.s3_url:
            buckets = list_buckets(s3_client)
            result = {"buckets": buckets, "total_count": len(buckets)}
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0

        # Parse S3 URL - allow bucket-only for ls
        bucket, prefix = _parse_s3_bucket_url(args.s3_url)

        # Check if we should list multipart uploads
        if args.multipart:
            result = list_multipart_uploads(
                s3_client,
                bucket=bucket,
                prefix=prefix,
            )
        else:
            # Determine delimiter: use '/' by default for hierarchical listing
            # unless --recursive is specified or custom delimiter is provided
            delimiter = args.delimiter
            if delimiter is None and not args.recursive:
                delimiter = "/"
            result = list_objects(
                s3_client,
                bucket=bucket,
                prefix=prefix,
                delimiter=delimiter,
                max_keys=args.max_keys,
            )

        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_du(args) -> int:
    """Handle the du command."""
    try:
        s3_client = get_s3_client()

        # Parse S3 URL - require bucket at minimum
        if not args.s3_url:
            print("Error: S3 URL is required for du command", file=sys.stderr)
            return 1

        bucket, prefix = _parse_s3_bucket_url(args.s3_url)

        result = calculate_du(
            s3_client,
            bucket=bucket,
            prefix=prefix,
            include_multipart=not args.exclude_multipart,
        )

        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the command line argument parser."""
    parser = argparse.ArgumentParser(
        prog="s3_storage",
        description="S3 storage utilities for listing and analyzing storage usage.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all buckets
  %(prog)s ls

  # List objects in a bucket
  %(prog)s ls s3://mybucket

  # List objects with prefix
  %(prog)s ls s3://mybucket/prefix/

  # List multipart uploads
  %(prog)s ls --multipart s3://mybucket

  # Calculate disk usage
  %(prog)s du s3://mybucket

  # Calculate disk usage for prefix, exclude multipart uploads
  %(prog)s du --exclude-multipart s3://mybucket/prefix/
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ls command
    ls_parser = subparsers.add_parser(
        "ls",
        help="List buckets, objects, or multipart uploads",
        description="List S3 buckets, list objects in a bucket, or list multipart uploads.",
    )
    ls_parser.add_argument(
        "s3_url",
        nargs="?",
        help="S3 URL (s3://bucket or s3://bucket/prefix). If omitted, lists all buckets.",
    )
    ls_parser.add_argument(
        "--multipart",
        action="store_true",
        help="List multipart uploads instead of objects",
    )
    ls_parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively list all objects (default shows only one level)",
    )
    ls_parser.add_argument(
        "--delimiter",
        default=None,
        help="Delimiter for hierarchical listing (default: '/' when not recursive)",
    )
    ls_parser.add_argument(
        "--max-keys",
        type=int,
        default=None,
        help="Maximum number of keys to return",
    )
    ls_parser.set_defaults(func=cmd_ls)

    # du command
    du_parser = subparsers.add_parser(
        "du",
        help="Calculate disk usage (size and count) of objects and multipart uploads",
        description="Calculate total size and count of S3 objects and multipart uploads.",
    )
    du_parser.add_argument(
        "s3_url",
        help="S3 URL (s3://bucket or s3://bucket/prefix)",
    )
    du_parser.add_argument(
        "--exclude-multipart",
        action="store_true",
        help="Exclude multipart uploads from calculation",
    )
    du_parser.set_defaults(func=cmd_du)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """
    Run the CLI entrypoint.

    Parses command line arguments and dispatches to the appropriate subcommand.
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
