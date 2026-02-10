#!/usr/bin/env python3
"""
Utilities for extracting video keyframes.

This module provides a Python API and a small CLI for keyframe extraction across multiple
algorithms:
  - difference: frame-to-frame pixel difference score (OpenCV)
  - optical_flow: dense optical flow magnitude score (OpenCV)
  - histogram: histogram distance score (OpenCV)
  - I_frame: I-frame timestamps from ffprobe and extraction via ffmpeg (fastest)

Inputs can be local files, HTTP/HTTPS URLs, or S3 URLs (s3://bucket/key).
Outputs can be written locally (JPG/PNG) and optionally uploaded to S3.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Iterable, List, Optional

from ._internal import ffmpeg, inputs, s3


@dataclass(frozen=True)
class KeyframeResult:
    """
    Hold a single keyframe selection result.

    Attributes:
        frame_index: 0-based frame index when known (OpenCV methods); None for I_frame mode when
            selection happens from timestamps only.
        timestamp_s: Timestamp in seconds.
        method: Method that selected the keyframe.
        score: Selection score when applicable (difference/histogram/optical_flow). None for
            I_frame.
        local_path: Local output image path if written.
        s3_url: S3 URL if uploaded.
    """

    frame_index: Optional[int]
    timestamp_s: float
    method: str
    score: Optional[float]
    local_path: Optional[str] = None
    s3_url: Optional[str] = None


class VideoKeyframeExtractor:
    """
    Extract keyframes from videos using multiple algorithms.

    For I_frame, this tool uses ffprobe + ffmpeg. For other methods, it uses OpenCV.
    """

    def __init__(self):
        self.ffprobe_path = ffmpeg.find_ffprobe()
        self.ffmpeg_path = ffmpeg.find_ffmpeg()

    def _verify_ffmpeg_tools(self) -> None:
        ffmpeg.verify_ffmpeg_tools(ffprobe_path=self.ffprobe_path, ffmpeg_path=self.ffmpeg_path)

    def _run_ffprobe_csv(self, file_path: str) -> List[tuple[float, str]]:
        """
        Return a list of (timestamp_s, pict_type) for video frames.
        """
        self._verify_ffmpeg_tools()
        cmd = [
            self.ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=best_effort_timestamp_time,pict_type",
            "-of",
            "csv=p=0",
            file_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=300)
        except subprocess.TimeoutExpired:
            raise ValueError("ffprobe execution timeout")
        except subprocess.CalledProcessError as e:
            raise ValueError(f"ffprobe execution failed: {e.stderr}")

        rows: List[tuple[float, str]] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            ts_s = parts[0]
            pict = parts[1]
            if ts_s in {"N/A", ""}:
                continue
            try:
                rows.append((float(ts_s), pict))
            except ValueError:
                continue
        return rows

    @staticmethod
    def _apply_min_interval(
        candidates: Iterable[KeyframeResult], min_interval_s: float
    ) -> List[KeyframeResult]:
        if min_interval_s <= 0:
            return list(candidates)
        picked: List[KeyframeResult] = []
        last_ts: Optional[float] = None
        for c in candidates:
            if last_ts is None or (c.timestamp_s - last_ts) >= min_interval_s:
                picked.append(c)
                last_ts = c.timestamp_s
        return picked

    @staticmethod
    def _uniform_sample_by_time(
        candidates: List[KeyframeResult], max_keyframes: Optional[int]
    ) -> List[KeyframeResult]:
        if not max_keyframes or max_keyframes <= 0:
            return candidates
        if len(candidates) <= max_keyframes:
            return candidates
        if max_keyframes == 1:
            return [candidates[0]]
        times = [c.timestamp_s for c in candidates]
        t0, t1 = times[0], times[-1]
        if t1 <= t0:
            step = max(1, len(candidates) // max_keyframes)
            return candidates[::step][:max_keyframes]
        targets = [t0 + (t1 - t0) * i / (max_keyframes - 1) for i in range(max_keyframes)]
        out: List[KeyframeResult] = []
        j = 0
        for t in targets:
            while j + 1 < len(candidates) and candidates[j + 1].timestamp_s <= t:
                j += 1
            out.append(candidates[j])
        # de-dup while keeping order
        uniq: List[KeyframeResult] = []
        seen = set()
        for k in out:
            if k.timestamp_s in seen:
                continue
            uniq.append(k)
            seen.add(k.timestamp_s)
        return uniq[:max_keyframes]

    def _detect_iframes(
        self,
        file_path: str,
        *,
        max_keyframes: Optional[int],
        min_interval_s: float,
    ) -> List[KeyframeResult]:
        rows = self._run_ffprobe_csv(file_path)
        candidates = [
            KeyframeResult(frame_index=None, timestamp_s=ts, method="I_frame", score=None)
            for ts, pict in rows
            if pict == "I"
        ]
        candidates = self._apply_min_interval(candidates, min_interval_s=min_interval_s)
        return self._uniform_sample_by_time(candidates, max_keyframes=max_keyframes)

    @staticmethod
    def _ensure_output_dir(output_dir: str) -> None:
        os.makedirs(output_dir, exist_ok=True)

    @staticmethod
    def _build_output_filename(index: int, timestamp_s: float, image_format: str) -> str:
        ts_ms = int(round(timestamp_s * 1000))
        ext = image_format.lower().lstrip(".")
        return f"keyframe_{index:04d}_{ts_ms:010d}.{ext}"

    def _extract_one_frame_ffmpeg(
        self,
        file_path: str,
        timestamp_s: float,
        output_path: str,
    ) -> None:
        self._verify_ffmpeg_tools()
        # -ss before -i for faster seek; output a single frame.
        cmd = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp_s:.6f}",
            "-i",
            file_path,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-y",
            output_path,
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=300)
        except subprocess.TimeoutExpired:
            raise ValueError("ffmpeg execution timeout")
        except subprocess.CalledProcessError as e:
            raise ValueError(f"ffmpeg execution failed: {e.stderr}")

    @staticmethod
    def _require_cv2() -> None:
        try:
            import cv2  # noqa: F401
            import numpy  # noqa: F401
        except ImportError as e:
            raise ValueError(
                "OpenCV and numpy are required for this method. "
                "Install: pip install opencv-python-headless numpy"
            ) from e

    def _detect_opencv_scores(
        self,
        file_path: str,
        *,
        method: str,
        threshold: Optional[float],
        max_keyframes: Optional[int],
        min_interval_s: float,
        flow_step: int,
    ) -> List[KeyframeResult]:
        self._require_cv2()
        import cv2
        import numpy as np

        cap = cv2.VideoCapture(file_path)
        if not cap.isOpened():
            raise ValueError(f"Failed to open video: {file_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        if fps <= 0:
            fps = 30.0
        frame_index = -1

        prev_gray = None
        prev_hist = None
        picked: List[KeyframeResult] = []
        last_ts: Optional[float] = None

        def ok_interval(ts: float) -> bool:
            return last_ts is None or (ts - last_ts) >= min_interval_s

        # A practical default when user doesn't provide thresholds.
        default_thresholds = {
            "difference": 12.0,
            "histogram": 0.35,  # Bhattacharyya: 0..1, larger means more different
            "optical_flow": 1.5,
        }
        thr = threshold if threshold is not None else default_thresholds.get(method)
        if thr is None:
            thr = 0.0

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frame_index += 1
            ts_s = frame_index / fps

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            score: Optional[float] = None

            if method == "difference":
                if prev_gray is not None:
                    diff = cv2.absdiff(gray, prev_gray)
                    score = float(np.mean(diff))
            elif method == "histogram":
                hist = cv2.calcHist([gray], [0], None, [64], [0, 256])
                cv2.normalize(hist, hist)
                if prev_hist is not None:
                    score = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA))
                prev_hist = hist
            elif method == "optical_flow":
                if prev_gray is not None and (frame_index % max(1, flow_step) == 0):
                    flow = cv2.calcOpticalFlowFarneback(
                        prev_gray,
                        gray,
                        None,
                        pyr_scale=0.5,
                        levels=3,
                        winsize=15,
                        iterations=3,
                        poly_n=5,
                        poly_sigma=1.2,
                        flags=0,
                    )
                    mag, _ang = cv2.cartToPolar(flow[..., 0], flow[..., 1])
                    score = float(np.mean(mag))
            else:
                cap.release()
                raise ValueError(f"Unsupported OpenCV method: {method}")

            prev_gray = gray

            if score is None:
                continue
            if score < thr:
                continue
            if not ok_interval(ts_s):
                continue

            picked.append(
                KeyframeResult(
                    frame_index=frame_index,
                    timestamp_s=ts_s,
                    method=method,
                    score=score,
                )
            )
            last_ts = ts_s

            if max_keyframes and max_keyframes > 0 and len(picked) >= max_keyframes:
                break

        cap.release()
        return picked

    def extract(
        self,
        input_path: str,
        *,
        method: str | List[str] = "I_frame",
        threshold: Optional[float] = None,
        max_keyframes: Optional[int] = 20,
        min_interval_s: float = 0.5,
        output_dir: Optional[str] = None,
        image_format: str = "jpg",
        manifest_path: Optional[str] = None,
        s3_output_prefix: Optional[str] = None,
        cleanup: bool = True,
        flow_step: int = 2,
    ) -> List[KeyframeResult]:
        """
        Extract keyframes from the input video.

        Args:
            input_path: Local path, HTTP/HTTPS URL, or S3 URL.
            method: One method or a list of methods. When a list is provided, methods are tried in
                order and the first one returning at least one keyframe is used.
            threshold: Method-specific score threshold (ignored for I_frame).
            max_keyframes: Maximum number of keyframes to return (None to disable).
            min_interval_s: Minimum time interval between selected keyframes.
            output_dir: Directory to write images. If not set and S3 output is requested, a
                temporary directory is used.
            image_format: "jpg" or "png".
            manifest_path: Optional path or s3:// URL to write a JSON manifest.
            s3_output_prefix: Optional s3://bucket/prefix/ to upload images.
            cleanup: Whether to delete temporary downloaded inputs and temp outputs.
            flow_step: For optical_flow, compute flow once per N frames (trade speed vs accuracy).
        """
        methods = [method] if isinstance(method, str) else list(method)
        if not methods:
            raise ValueError("method must not be empty")

        image_format = image_format.lower().lstrip(".")
        if image_format not in {"jpg", "jpeg", "png"}:
            raise ValueError(f"Unsupported image_format: {image_format}")
        if image_format == "jpeg":
            image_format = "jpg"

        temp_output_dir = None
        results: List[KeyframeResult] = []
        used_method: Optional[str] = None

        with inputs.PreparedInput(input_path, mode="download") as file_path:
            try:
                if output_dir:
                    self._ensure_output_dir(output_dir)
                elif s3_output_prefix:
                    temp_output_dir = tempfile.mkdtemp()
                    output_dir = temp_output_dir

                for m in methods:
                    if m == "I_frame":
                        res = self._detect_iframes(
                            file_path,
                            max_keyframes=max_keyframes,
                            min_interval_s=min_interval_s,
                        )
                    elif m in {"difference", "histogram", "optical_flow"}:
                        res = self._detect_opencv_scores(
                            file_path,
                            method=m,
                            threshold=threshold,
                            max_keyframes=max_keyframes,
                            min_interval_s=min_interval_s,
                            flow_step=flow_step,
                        )
                    else:
                        raise ValueError(f"Unsupported method: {m}")

                    if res:
                        results = res
                        used_method = m
                        break

                if not results:
                    return []

                # Write images locally (if requested or needed for S3 upload).
                if output_dir:
                    for idx, r in enumerate(results, start=1):
                        filename = self._build_output_filename(
                            idx, r.timestamp_s, image_format=image_format
                        )
                        out_path = os.path.join(output_dir, filename)
                        self._extract_one_frame_ffmpeg(file_path, r.timestamp_s, out_path)
                        results[idx - 1] = KeyframeResult(
                            frame_index=r.frame_index,
                            timestamp_s=r.timestamp_s,
                            method=used_method or r.method,
                            score=r.score,
                            local_path=out_path,
                            s3_url=r.s3_url,
                        )

                # Upload to S3 if requested.
                if s3_output_prefix:
                    for i, r in enumerate(results):
                        if not r.local_path:
                            continue
                        key_name = os.path.basename(r.local_path)
                        dest_url = s3.join_s3_url(s3_output_prefix, key_name)
                        content_type = "image/jpeg" if image_format == "jpg" else "image/png"
                        s3.upload_path_to_s3(
                            r.local_path,
                            dest_url,
                            extra_args={"ContentType": content_type},
                        )
                        results[i] = KeyframeResult(
                            frame_index=r.frame_index,
                            timestamp_s=r.timestamp_s,
                            method=r.method,
                            score=r.score,
                            local_path=r.local_path,
                            s3_url=dest_url,
                        )

                # Manifest
                if manifest_path:
                    manifest = [
                        {
                            "frame_index": r.frame_index,
                            "timestamp_s": r.timestamp_s,
                            "method": r.method,
                            "score": r.score,
                            "local_path": r.local_path,
                            "s3_url": r.s3_url,
                        }
                        for r in results
                    ]

                    if inputs.is_s3_url(manifest_path):
                        temp_manifest_dir = tempfile.mkdtemp()
                        temp_manifest_path = os.path.join(
                            temp_manifest_dir, "keyframes_manifest.json"
                        )
                        with open(temp_manifest_path, "w", encoding="utf-8") as f:
                            json.dump(manifest, f, indent=2, ensure_ascii=False)
                        s3.upload_path_to_s3(
                            temp_manifest_path,
                            manifest_path,
                            extra_args={"ContentType": "application/json"},
                        )
                        if cleanup:
                            try:
                                os.remove(temp_manifest_path)
                                os.rmdir(temp_manifest_dir)
                            except OSError:
                                pass
                    else:
                        with open(manifest_path, "w", encoding="utf-8") as f:
                            json.dump(manifest, f, indent=2, ensure_ascii=False)

                return results
            finally:
                if cleanup and temp_output_dir and os.path.exists(temp_output_dir):
                    try:
                        for name in os.listdir(temp_output_dir):
                            try:
                                os.remove(os.path.join(temp_output_dir, name))
                            except OSError:
                                pass
                        os.rmdir(temp_output_dir)
                    except OSError:
                        pass


def extract_video_keyframes(
    input_path: str,
    *,
    method: str | List[str] = "I_frame",
    threshold: Optional[float] = None,
    max_keyframes: Optional[int] = 20,
    min_interval_s: float = 0.5,
    output_dir: Optional[str] = None,
    image_format: str = "jpg",
    manifest_path: Optional[str] = None,
    s3_output_prefix: Optional[str] = None,
    cleanup: bool = True,
    flow_step: int = 2,
) -> List[dict]:
    """
    Convenience wrapper for extracting keyframes.

    Returns:
        A list of dictionaries (JSON-serializable) with keyframe metadata.
    """
    extractor = VideoKeyframeExtractor()
    results = extractor.extract(
        input_path,
        method=method,
        threshold=threshold,
        max_keyframes=max_keyframes,
        min_interval_s=min_interval_s,
        output_dir=output_dir,
        image_format=image_format,
        manifest_path=manifest_path,
        s3_output_prefix=s3_output_prefix,
        cleanup=cleanup,
        flow_step=flow_step,
    )
    return [
        {
            "frame_index": r.frame_index,
            "timestamp_s": r.timestamp_s,
            "method": r.method,
            "score": r.score,
            "local_path": r.local_path,
            "s3_url": r.s3_url,
        }
        for r in results
    ]


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video_keyframes",
        description=(
            "Extract video keyframes (I_frame/difference/optical_flow/histogram) to images."
        ),
    )
    parser.add_argument(
        "input_path",
        help="Video path/URL (e.g., ./a.mp4, https://..., s3://bucket/key).",
    )
    parser.add_argument(
        "--method",
        default="I_frame",
        help=(
            "Method name or comma-separated list (first successful wins). "
            "Options: I_frame,difference,optical_flow,histogram. Default: I_frame."
        ),
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Score threshold (non-I_frame methods).",
    )
    parser.add_argument(
        "--max-keyframes",
        type=int,
        default=20,
        help="Max number of keyframes (default: 20).",
    )
    parser.add_argument(
        "--min-interval-s",
        type=float,
        default=0.5,
        help="Minimum seconds between keyframes (default: 0.5).",
    )
    parser.add_argument("--output-dir", default=None, help="Directory to write images.")
    parser.add_argument(
        "--image-format",
        default="jpg",
        choices=("jpg", "png"),
        help="Output image format.",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Write manifest JSON to a path or s3:// URL.",
    )
    parser.add_argument(
        "--s3-output-prefix",
        default=None,
        help="Upload images to S3 prefix: s3://bucket/prefix/",
    )
    parser.add_argument(
        "--flow-step",
        type=int,
        default=2,
        help="For optical_flow, compute flow once per N frames (default: 2).",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Do not delete temporary downloads/outputs.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    methods = [m.strip() for m in args.method.split(",") if m.strip()]
    method_value: str | List[str] = methods[0] if len(methods) == 1 else methods
    cleanup = not args.no_cleanup

    try:
        results = extract_video_keyframes(
            args.input_path,
            method=method_value,
            threshold=args.threshold,
            max_keyframes=args.max_keyframes,
            min_interval_s=args.min_interval_s,
            output_dir=args.output_dir,
            image_format=args.image_format,
            manifest_path=args.manifest,
            s3_output_prefix=args.s3_output_prefix,
            cleanup=cleanup,
            flow_step=args.flow_step,
        )
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
