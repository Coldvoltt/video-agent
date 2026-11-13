import subprocess
from pathlib import Path

import pytest

from editor import config, store


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Every test gets its own DB and storage root, and no OpenAI key."""
    monkeypatch.setattr(config.settings, "db_path", tmp_path / "test.db")
    monkeypatch.setattr(config.settings, "storage_root", tmp_path / "storage")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("EDITOR_BILLING", "0")   # plan limits off; test_billing turns them on
    monkeypatch.setattr(store, "_initialized_for", None)
    yield


def _ffmpeg(*args: str) -> None:
    subprocess.run([config.require_ffmpeg(), "-y", "-v", "error", *args], check=True)


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("media")
    # 6 s, 1280x720 @ 30 fps, with a 440 Hz tone
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=6",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "landscape.mp4"))
    # 4 s, 480x640 @ 25 fps, no audio
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=480x640:rate=25:duration=4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(d / "portrait_silent.mp4"))
    # phone-style portrait: 480x270 pixels stored with a 90 degree rotation flag (displays as 270x480)
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=480x270:rate=30:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=330:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "_flat.mp4"))
    _ffmpeg("-display_rotation", "90", "-i", str(d / "_flat.mp4"), "-c", "copy", str(d / "phone_portrait.mp4"))
    # still image
    _ffmpeg("-f", "lavfi", "-i", "color=c=orange:size=800x600", "-frames:v", "1", str(d / "card.png"))
    # audio only
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=3", str(d / "tone.mp3"))
    return d
