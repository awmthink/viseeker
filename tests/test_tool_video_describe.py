import os

import pytest


def test_video_describe_short_video_1fps_builds_messages_and_returns_text(
    tmp_path,
    monkeypatch,
    mock_ffmpeg_ok,
    dummy_probe_result,
    patch_prepared_input,
):
    from viseeker import video_describe

    monkeypatch.setenv("VLM_MODEL_ID", "dummy-model")

    in_file = tmp_path / "in.mp4"
    in_file.write_bytes(b"x")
    patch_prepared_input(lambda input_path, mode: str(in_file))

    # 6.2s => timestamps should include 0..6 at 1fps => 7 frames.
    monkeypatch.setattr(
        video_describe.probe,
        "probe_video",
        lambda p: dummy_probe_result(duration_s=6.2),
    )

    captured = {}

    def _fake_sample_frames_to_files(video_input_spec, *, duration_s, fps, max_frames, output_dir):
        frames = []
        for i in range(7):
            p = os.path.join(output_dir, f"frame_{i:04d}.jpg")
            with open(p, "wb") as f:
                f.write(b"jpg")
            frames.append((float(i), p, 3))
        return frames

    monkeypatch.setattr(video_describe, "_sample_frames_to_files", _fake_sample_frames_to_files)

    async def _fake_call_vlm(*, model: str, input_items: list[dict]) -> str:
        captured["model"] = model
        captured["input_items"] = input_items
        return "OK"

    monkeypatch.setattr(video_describe, "_call_vlm_responses_api", _fake_call_vlm)

    out = video_describe.describe_video(
        "s3://bucket/in.mp4",
        prompt="你觉得这个恐怖吗？",
        detail="high",
        fps=1.0,
        max_frames=128,
    )
    assert out["text"] == "OK"
    assert out["detail"] == "high"
    assert out["frame_count"] == 7
    assert out["duration_s"] == pytest.approx(6.2, rel=1e-6)

    assert captured["model"] == "dummy-model"
    inp = captured["input_items"]
    assert isinstance(inp, list) and inp
    msg = inp[0]
    assert msg["type"] == "message"
    assert msg["role"] == "user"
    content = msg["content"]
    assert content[0]["type"] == "input_text"
    assert "恐怖" in content[0]["text"]
    assert len(content) == 1 + 2 * out["frame_count"]
    for part in content:
        if part["type"] != "input_image":
            continue
        assert part["detail"] == "high"
        assert part["image_url"].startswith("file://")


def test_video_describe_long_video_uniform_samples_128_frames(
    tmp_path,
    monkeypatch,
    mock_ffmpeg_ok,
    dummy_probe_result,
    patch_prepared_input,
):
    from viseeker import video_describe

    monkeypatch.setenv("VLM_MODEL_ID", "dummy-model")

    in_file = tmp_path / "in.mp4"
    in_file.write_bytes(b"x")
    patch_prepared_input(lambda input_path, mode: str(in_file))

    monkeypatch.setattr(
        video_describe.probe,
        "probe_video",
        lambda p: dummy_probe_result(duration_s=200.0),
    )

    def _fake_sample_frames_to_files(video_input_spec, *, duration_s, fps, max_frames, output_dir):
        frames = []
        for i in range(128):
            p = os.path.join(output_dir, f"frame_{i:04d}.jpg")
            with open(p, "wb") as f:
                f.write(b"jpg")
            frames.append((float(i) * 0.1, p, 3))
        return frames

    monkeypatch.setattr(video_describe, "_sample_frames_to_files", _fake_sample_frames_to_files)

    async def _fake_call_vlm(*, model: str, input_items: list[dict]) -> str:
        return "OK"

    monkeypatch.setattr(video_describe, "_call_vlm_responses_api", _fake_call_vlm)

    out = video_describe.describe_video(
        str(in_file),
        max_frames=128,
        fps=1.0,
    )
    assert out["frame_count"] == 128


def test_video_describe_rejects_videos_longer_than_5min(
    tmp_path,
    monkeypatch,
    mock_ffmpeg_ok,
    dummy_probe_result,
    patch_prepared_input,
):
    from viseeker import video_describe

    monkeypatch.setenv("VLM_MODEL_ID", "dummy-model")

    in_file = tmp_path / "in.mp4"
    in_file.write_bytes(b"x")
    patch_prepared_input(lambda input_path, mode: str(in_file))

    monkeypatch.setattr(
        video_describe.probe,
        "probe_video",
        lambda p: dummy_probe_result(duration_s=301.0),
    )

    with pytest.raises(ValueError, match="too long"):
        video_describe.describe_video(
            str(in_file),
        )
