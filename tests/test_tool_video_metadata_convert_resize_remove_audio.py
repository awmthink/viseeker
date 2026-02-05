import json
import os
from typing import Any, Dict

import pytest

from tests.conftest import DummyCompletedProcess, assert_json_stdout


def test_video_metadata_extract_video_metadata(monkeypatch, mock_ffmpeg_ok, patch_prepared_input, fake_subprocess_run):
    from vision_ai_tools import video_metadata

    patch_prepared_input(lambda input_path, mode: "spec://input")

    ffprobe_json = {
        "format": {"duration": "2.5", "format_name": "mp4", "bit_rate": "1000"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720, "r_frame_rate": "25/1"},
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "44100", "channels": 2},
        ],
    }

    def _handler(cmd, capture_output, text, check, timeout):
        assert "ffprobe" in cmd[0]
        return DummyCompletedProcess(stdout=json.dumps(ffprobe_json), stderr="")

    fake_subprocess_run(_handler, target_module=video_metadata)

    out = video_metadata.extract_video_metadata("s3://bucket/in.mp4", probe_mode="url")
    assert out["has_video"] is True
    assert out["has_audio"] is True
    assert out["video_width"] == 1280
    assert out["video_height"] == 720
    assert abs(out["video_fps"] - 25.0) < 1e-6


def test_video_metadata_main_prints_json(capsys, monkeypatch, mock_ffmpeg_ok):
    from vision_ai_tools import video_metadata

    monkeypatch.setattr(video_metadata, "extract_video_metadata", lambda *a, **k: {"ok": True})
    code = video_metadata.main(["./in.mp4"])
    assert code == 0
    data = assert_json_stdout(capsys)
    assert data == {"ok": True}


def test_video_convert_mp4_auto_codec_fallback(
    tmp_path,
    monkeypatch,
    mock_ffmpeg_ok,
    dummy_probe_result,
    patch_prepared_input,
    patch_prepared_output,
):
    from vision_ai_tools import video_convert_mp4
    from vision_ai_tools._internal.probe import ProbeResult

    in_file = tmp_path / "in.mov"
    in_file.write_bytes(b"x")
    out_file = tmp_path / "out.mp4"

    patch_prepared_input(lambda input_path, mode: str(in_file))
    patch_prepared_output(str(out_file), s3_url=None)

    monkeypatch.setattr(video_convert_mp4.probe, "probe_video", lambda p: dummy_probe_result())

    calls = {"n": 0}

    def _fake_run_ffmpeg(cmd: list[str], *, timeout_s: int) -> None:
        calls["n"] += 1
        if "-c:v" in cmd and "libx265" in cmd:
            raise ValueError("encoder unavailable")
        # output path is last arg
        outp = cmd[-1]
        with open(outp, "wb") as f:
            f.write(b"mp4")

    monkeypatch.setattr(video_convert_mp4, "_run_ffmpeg", _fake_run_ffmpeg)

    result = video_convert_mp4.convert_to_mp4(str(in_file), output=str(out_file), video_codec="auto")
    assert result["video_codec"] == "libx264"
    assert os.path.exists(str(out_file))
    assert calls["n"] == 2


def test_video_resize_keep_aspect_when_one_dimension(
    tmp_path,
    monkeypatch,
    mock_ffmpeg_ok,
    dummy_probe_result,
    patch_prepared_input,
    patch_prepared_output,
):
    from vision_ai_tools import video_resize

    in_file = tmp_path / "in.mp4"
    in_file.write_bytes(b"x")
    out_file = tmp_path / "out.mp4"

    patch_prepared_input(lambda input_path, mode: str(in_file))
    patch_prepared_output(str(out_file), s3_url=None)
    monkeypatch.setattr(video_resize.probe, "probe_video", lambda p: dummy_probe_result(video_width=1920, video_height=1080))

    def _fake_run(cmd, capture_output, text, check, timeout):
        # last arg is output path
        with open(cmd[-1], "wb") as f:
            f.write(b"resized")
        return DummyCompletedProcess(stdout="", stderr="")

    monkeypatch.setattr(video_resize.subprocess, "run", _fake_run)

    r = video_resize.resize_video(str(in_file), output=str(out_file), width=640)
    assert r["applied_policy"] == "keep_aspect"
    assert r["output_width"] == 640
    assert r["output_height"] % 2 == 0
    assert os.path.exists(str(out_file))


def test_video_remove_audio_counts_streams_removed(
    tmp_path,
    monkeypatch,
    mock_ffmpeg_ok,
    dummy_probe_result,
    patch_prepared_input,
    patch_prepared_output,
):
    from vision_ai_tools import video_remove_audio

    in_file = tmp_path / "in.mp4"
    in_file.write_bytes(b"x")
    out_file = tmp_path / "out.mp4"

    patch_prepared_input(lambda input_path, mode: str(in_file))
    patch_prepared_output(str(out_file), s3_url=None)
    monkeypatch.setattr(video_remove_audio.probe, "probe_video", lambda p: dummy_probe_result(has_audio=True))
    monkeypatch.setattr(
        video_remove_audio.probe,
        "run_ffprobe_json",
        lambda p, timeout_s=60: {"streams": [{"codec_type": "audio"}, {"codec_type": "audio"}, {"codec_type": "video"}]},
    )

    def _fake_run(cmd, capture_output, text, check, timeout):
        with open(cmd[-1], "wb") as f:
            f.write(b"noaudio")
        return DummyCompletedProcess(stdout="", stderr="")

    monkeypatch.setattr(video_remove_audio.subprocess, "run", _fake_run)

    r = video_remove_audio.remove_video_audio(str(in_file), output=str(out_file))
    assert r["has_audio_before"] is True
    assert r["audio_streams_removed"] == 2
    assert os.path.exists(str(out_file))

