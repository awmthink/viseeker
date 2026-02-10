import os
from typing import Any, Dict

import pytest


def test_inputs_is_http_s3_local(tmp_path):
    from viseeker._internal import inputs

    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")

    assert inputs.is_http_url("https://example.com/a.mp4") is True
    assert inputs.is_http_url("http://example.com/a.mp4") is True
    assert inputs.is_http_url("s3://b/k") is False

    assert inputs.is_s3_url("s3://bucket/key") is True
    assert inputs.is_s3_url("https://example.com") is False

    assert inputs.is_local_file(str(f)) is True
    assert inputs.is_local_file(str(tmp_path / "missing")) is False


def test_inputs_prepared_input_url_mode_http_passthrough():
    from viseeker._internal.inputs import PreparedInput

    with PreparedInput("https://example.com/a.mp4", mode="url") as spec:
        assert spec == "https://example.com/a.mp4"


def test_inputs_prepared_input_url_mode_s3_presign(monkeypatch):
    from viseeker._internal import inputs

    monkeypatch.setattr(
        inputs.s3, "presign_get_object_url", lambda url, expires_in: "https://signed/url"
    )
    with inputs.PreparedInput("s3://bucket/key.mp4", mode="url") as spec:
        assert spec == "https://signed/url"


def test_inputs_prepared_input_download_mode_http_download_and_cleanup(monkeypatch):
    from viseeker._internal import inputs

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int = 8192):
            yield b"abc"
            yield b""
            yield b"def"

    monkeypatch.setattr(inputs.requests, "get", lambda url, stream, timeout: _Resp())

    pi = inputs.PreparedInput("https://example.com/file.bin", mode="download")
    with pi as local_path:
        assert os.path.exists(local_path)
        assert open(local_path, "rb").read() == b"abcdef"

    assert not os.path.exists(local_path)
    assert pi._temp_dir is not None
    assert not os.path.exists(pi._temp_dir)


def test_inputs_prepared_input_download_mode_s3_download_and_cleanup(monkeypatch):
    from viseeker._internal import inputs

    def _download(url: str, dest_path: str) -> None:
        with open(dest_path, "wb") as f:
            f.write(b"data")

    monkeypatch.setattr(inputs.s3, "download_s3_to_path", _download)

    pi = inputs.PreparedInput("s3://bucket/path/to/in.mp4", mode="download")
    with pi as local_path:
        assert os.path.exists(local_path)
        assert open(local_path, "rb").read() == b"data"
        assert os.path.basename(local_path) == "in.mp4"

    assert not os.path.exists(local_path)
    assert pi._temp_dir is not None
    assert not os.path.exists(pi._temp_dir)


def test_outputs_prepared_output_local_creates_parent_dir(tmp_path):
    from viseeker._internal.outputs import PreparedOutput

    out_path = tmp_path / "nested" / "out.bin"
    assert not out_path.parent.exists()
    with PreparedOutput(str(out_path)) as out:
        assert out.local_path == str(out_path)
        assert out.s3_url is None
        assert out_path.parent.exists()


def test_outputs_prepared_output_s3_upload_on_success(monkeypatch):
    from viseeker._internal import outputs

    calls: Dict[str, Any] = {}

    def _upload(src: str, dest: str, *, extra_args=None, config=None):
        calls["src"] = src
        calls["dest"] = dest
        calls["extra_args"] = extra_args

    monkeypatch.setattr(outputs.s3, "upload_path_to_s3", _upload)

    po = outputs.PreparedOutput(
        "s3://bucket/out.bin", default_filename="out.bin", content_type="application/octet-stream"
    )
    with po as out:
        assert out.s3_url == "s3://bucket/out.bin"
        with open(out.local_path, "wb") as f:
            f.write(b"hello")
        assert os.path.exists(out.local_path)

    assert calls["dest"] == "s3://bucket/out.bin"
    assert calls["extra_args"] == {"ContentType": "application/octet-stream"}
    assert not os.path.exists(po._local_path)  # type: ignore[attr-defined]
    assert po._temp_dir is not None  # type: ignore[attr-defined]
    assert not os.path.exists(po._temp_dir)  # type: ignore[attr-defined]


def test_outputs_prepared_output_s3_no_upload_on_exception(monkeypatch):
    from viseeker._internal import outputs

    called = False

    def _upload(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(outputs.s3, "upload_path_to_s3", _upload)

    with pytest.raises(RuntimeError):
        with outputs.PreparedOutput("s3://bucket/out.bin") as out:
            with open(out.local_path, "wb") as f:
                f.write(b"x")
            raise RuntimeError("boom")

    assert called is False


def test_probe_parse_fps_and_probe_video(monkeypatch, mock_ffmpeg_ok):
    from viseeker._internal import probe

    raw = {
        "format": {"duration": "1.5", "format_name": "mp4", "bit_rate": "123"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 640,
                "height": 360,
                "r_frame_rate": "30/1",
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }

    monkeypatch.setattr(probe, "run_ffprobe_json", lambda input_spec, timeout_s=60: raw)
    p = probe.probe_video("x.mp4")
    assert p.duration_s == 1.5
    assert p.has_video is True
    assert p.has_audio is True
    assert p.video_width == 640
    assert p.video_height == 360
    assert abs((p.video_fps or 0) - 30.0) < 1e-6
