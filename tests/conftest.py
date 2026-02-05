import contextlib
import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional

import pytest


@dataclass(frozen=True)
class DummyCompletedProcess:
    stdout: str = ""
    stderr: str = ""


@pytest.fixture()
def tmp_video_file(tmp_path: pytest.TempPathFactory) -> str:
    p = tmp_path.mktemp("data") / "in.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42")  # minimal-ish bytes; not actually parsed in tests
    return str(p)


@pytest.fixture()
def mock_ffmpeg_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Make ffmpeg/ffprobe discovery + verification always succeed.
    """
    from vision_ai_tools._internal import ffmpeg as ff

    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ff, "find_ffprobe", lambda: "ffprobe")
    monkeypatch.setattr(ff, "verify_ffprobe", lambda _path: None)
    monkeypatch.setattr(ff, "verify_ffmpeg", lambda _path: None)
    monkeypatch.setattr(ff, "verify_ffmpeg_tools", lambda *, ffprobe_path, ffmpeg_path: None)


@pytest.fixture()
def dummy_probe_result() -> Callable[..., Any]:
    """
    Factory for a vision_ai_tools._internal.probe.ProbeResult-like object.
    """
    from vision_ai_tools._internal.probe import ProbeResult

    def _make(
        *,
        duration_s: float = 10.0,
        has_video: bool = True,
        has_audio: bool = True,
        video_width: Optional[int] = 1920,
        video_height: Optional[int] = 1080,
        video_fps: Optional[float] = 30.0,
    ) -> ProbeResult:
        return ProbeResult(
            duration_s=duration_s,
            format_name="mov,mp4,m4a,3gp,3g2,mj2",
            bit_rate=1_000_000,
            has_video=has_video,
            has_audio=has_audio,
            video_codec="h264" if has_video else None,
            video_width=video_width if has_video else None,
            video_height=video_height if has_video else None,
            video_fps=video_fps if has_video else None,
            audio_codec="aac" if has_audio else None,
        )

    return _make


@pytest.fixture()
def patch_prepared_input(monkeypatch: pytest.MonkeyPatch):
    """
    Patch vision_ai_tools._internal.inputs.PreparedInput to a deterministic context manager.

    Usage:
        patch_prepared_input(lambda input_path, mode: "/tmp/local.mp4")
    """

    def _apply(resolver: Callable[[str, str], str]) -> None:
        from vision_ai_tools._internal import inputs

        class _FakePreparedInput:
            def __init__(self, input_path: str, mode: str = "download", presign_expires_in: int = 3600):
                self.input_path = input_path
                self.mode = mode
                self.presign_expires_in = presign_expires_in

            def __enter__(self) -> str:
                return resolver(self.input_path, self.mode)

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        monkeypatch.setattr(inputs, "PreparedInput", _FakePreparedInput)

    return _apply


@pytest.fixture()
def patch_prepared_output(monkeypatch: pytest.MonkeyPatch):
    """
    Patch vision_ai_tools._internal.outputs.PreparedOutput to a deterministic context manager.
    """

    def _apply(local_path: str, *, s3_url: Optional[str] = None) -> None:
        from vision_ai_tools._internal import outputs

        @dataclass(frozen=True)
        class _Out:
            local_path: str
            s3_url: Optional[str]

        class _FakePreparedOutput:
            def __init__(self, output: str, *, default_filename: str = "output.bin", content_type: Optional[str] = None):
                self.output = output
                self.default_filename = default_filename
                self.content_type = content_type

            def __enter__(self) -> _Out:
                os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
                return _Out(local_path=local_path, s3_url=s3_url)

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        monkeypatch.setattr(outputs, "PreparedOutput", _FakePreparedOutput)

    return _apply


@pytest.fixture()
def fake_subprocess_run(monkeypatch: pytest.MonkeyPatch):
    """
    Patch subprocess.run with a configurable fake.
    """

    def _apply(
        handler: Callable[..., DummyCompletedProcess],
        *,
        target_module: Optional[Any] = None,
    ) -> None:
        import subprocess

        def _fake_run(*args, **kwargs):
            cp = handler(*args, **kwargs)
            # Mimic subprocess.CompletedProcess attributes used by code.
            return cp

        if target_module is None:
            monkeypatch.setattr(subprocess, "run", _fake_run)
        else:
            monkeypatch.setattr(target_module.subprocess, "run", _fake_run)

    return _apply


def assert_json_stdout(capsys: pytest.CaptureFixture[str]) -> Any:
    out = capsys.readouterr().out.strip()
    assert out, "expected stdout to contain JSON"
    return json.loads(out)

