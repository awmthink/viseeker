import json
import os
from typing import Optional

from tests.conftest import assert_json_stdout


def test_video_split_fixed_mode_produces_segments(
    tmp_path,
    monkeypatch,
    mock_ffmpeg_ok,
    dummy_probe_result,
    patch_prepared_input,
):
    from vision_ai_tools import video_split

    in_file = tmp_path / "in.mp4"
    in_file.write_bytes(b"x")
    out_dir = tmp_path / "segs"

    patch_prepared_input(lambda input_path, mode: str(in_file))

    # Probe on input + per-segment probes
    monkeypatch.setattr(
        video_split.probe, "probe_video", lambda p: dummy_probe_result(duration_s=1.0)
    )

    def _fake_run_ffmpeg(cmd: list[str], *, timeout_s: int) -> None:
        # The segment pattern is the last cmd element before "-y"? In code it's after "-y".
        pattern = cmd[-1]
        # Create two segment files matching prefix/ext.
        for i in range(2):
            seg_path = pattern.replace("%04d", f"{i:04d}")
            os.makedirs(os.path.dirname(seg_path), exist_ok=True)
            with open(seg_path, "wb") as f:
                f.write(b"seg")

    monkeypatch.setattr(video_split, "_run_ffmpeg", _fake_run_ffmpeg)

    r = video_split.split_video(
        str(in_file),
        mode="fixed",
        output_dir=str(out_dir),
        segment_s=1.0,
        prefix="segment_",
        ext="mp4",
    )
    assert r["mode"] == "fixed"
    assert len(r["segments"]) == 2
    assert all(os.path.exists(s["local_path"]) for s in r["segments"])


def test_video_split_s3_upload_prefix_sets_segment_urls(
    tmp_path,
    monkeypatch,
    mock_ffmpeg_ok,
    dummy_probe_result,
    patch_prepared_input,
):
    from vision_ai_tools import video_split

    in_file = tmp_path / "in.mp4"
    in_file.write_bytes(b"x")
    out_dir = tmp_path / "segs"

    patch_prepared_input(lambda input_path, mode: str(in_file))
    monkeypatch.setattr(
        video_split.probe, "probe_video", lambda p: dummy_probe_result(duration_s=1.0)
    )

    def _fake_run_ffmpeg(cmd: list[str], *, timeout_s: int) -> None:
        pattern = cmd[-1]
        for i in range(2):
            seg_path = pattern.replace("%04d", f"{i:04d}")
            os.makedirs(os.path.dirname(seg_path), exist_ok=True)
            with open(seg_path, "wb") as f:
                f.write(b"seg")

    monkeypatch.setattr(video_split, "_run_ffmpeg", _fake_run_ffmpeg)
    monkeypatch.setattr(
        video_split,
        "_upload_segments_to_s3",
        lambda paths, s3_prefix, *, content_type: [
            f"{s3_prefix.rstrip('/')}/" + os.path.basename(p) for p in paths
        ],
    )

    r = video_split.split_video(
        str(in_file),
        mode="fixed",
        output_dir=str(out_dir),
        segment_s=1.0,
        s3_output_prefix="s3://bucket/prefix/",
        ext="mp4",
    )
    assert all(s["s3_url"] for s in r["segments"])


def test_video_split_main_prints_json(capsys, monkeypatch, mock_ffmpeg_ok):
    from vision_ai_tools import video_split

    monkeypatch.setattr(video_split, "split_video", lambda *a, **k: {"ok": True})
    code = video_split.main(
        ["./in.mp4", "--mode", "fixed", "--output-dir", "./out", "--segment-s", "1"]
    )
    assert code == 0
    assert assert_json_stdout(capsys) == {"ok": True}


def test_video_keyframes_iframe_writes_images_and_manifest(
    tmp_path,
    monkeypatch,
    mock_ffmpeg_ok,
    patch_prepared_input,
):
    from vision_ai_tools import video_keyframes

    in_file = tmp_path / "in.mp4"
    in_file.write_bytes(b"x")
    out_dir = tmp_path / "frames"
    manifest_path = tmp_path / "manifest.json"

    patch_prepared_input(lambda input_path, mode: str(in_file))

    extractor = video_keyframes.VideoKeyframeExtractor()
    monkeypatch.setattr(
        extractor,
        "_run_ffprobe_csv",
        lambda p: [(0.0, "I"), (0.5, "P"), (1.0, "I"), (2.0, "I")],
    )

    def _fake_extract_one(file_path: str, timestamp_s: float, output_path: str) -> None:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(b"img")

    monkeypatch.setattr(extractor, "_extract_one_frame_ffmpeg", _fake_extract_one)
    monkeypatch.setattr(video_keyframes, "VideoKeyframeExtractor", lambda: extractor)

    res = video_keyframes.extract_video_keyframes(
        str(in_file),
        method="I_frame",
        max_keyframes=10,
        min_interval_s=0.1,
        output_dir=str(out_dir),
        image_format="jpg",
        manifest_path=str(manifest_path),
        s3_output_prefix=None,
    )

    assert len(res) >= 2
    assert all(os.path.exists(r["local_path"]) for r in res if r["local_path"])
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(manifest, list)
    assert len(manifest) == len(res)


def test_video_keyframes_main_prints_json(capsys, monkeypatch, mock_ffmpeg_ok):
    from vision_ai_tools import video_keyframes

    monkeypatch.setattr(video_keyframes, "extract_video_keyframes", lambda *a, **k: [{"t": 1.0}])
    code = video_keyframes.main(["./in.mp4"])
    assert code == 0
    assert assert_json_stdout(capsys) == [{"t": 1.0}]


def test_video_adaptive_compress_succeeds_in_fps_stage(
    tmp_path,
    monkeypatch,
    mock_ffmpeg_ok,
    dummy_probe_result,
    patch_prepared_input,
):
    from vision_ai_tools import video_adaptive_compress

    in_file = tmp_path / "in.mp4"
    in_file.write_bytes(b"x")
    out_file = tmp_path / "out.mp4"

    patch_prepared_input(lambda input_path, mode: str(in_file))
    monkeypatch.setattr(
        video_adaptive_compress.probe,
        "probe_video",
        lambda p: dummy_probe_result(duration_s=10.0, video_fps=30.0, video_height=1080),
    )

    def _fake_encode_once(
        *,
        ffmpeg_path: str,
        input_path: str,
        output_path: str,
        chosen_vcodec: str,
        fps: Optional[float],
        height: Optional[int],
        crf: Optional[int],
        video_bitrate: Optional[str],
        preset: str,
        pix_fmt: str,
        audio_codec: str,
        audio_bitrate: str,
        timeout_s: int,
    ) -> None:
        # Deterministic size by stage: keep fps -> big, fps reduction -> smaller
        size = 1200 if fps is None else (500 if float(fps) <= 24.0 else 800)
        # further downscale makes it even smaller
        if height is not None:
            size = min(size, 400)
        with open(output_path, "wb") as f:
            f.write(b"x" * size)

    monkeypatch.setattr(video_adaptive_compress, "_encode_once", _fake_encode_once)

    r = video_adaptive_compress.adaptive_compress_video(
        str(in_file),
        output=str(out_file),
        target_bytes=600,
        video_codec="libx264",
        min_fps=8.0,
        min_height=480,
        timeout_s=30,
    )
    assert r["success"] is True
    assert r["strategy"] == "fps"
    assert os.path.exists(str(out_file))
    assert r["actual_bytes"] <= 600
    assert len(r["attempts"]) >= 2


def test_video_adaptive_compress_main_exit_code_0_on_success(capsys, monkeypatch, mock_ffmpeg_ok):
    from vision_ai_tools import video_adaptive_compress

    monkeypatch.setattr(
        video_adaptive_compress, "adaptive_compress_video", lambda *a, **k: {"success": True}
    )
    code = video_adaptive_compress.main(
        ["./in.mp4", "--output", "./out.mp4", "--target-bytes", "123"]
    )
    assert code == 0
    assert assert_json_stdout(capsys)["success"] is True
