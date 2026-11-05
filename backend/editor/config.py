"""
Editor settings. Read from the environment once, but kept on a mutable object so
tests (and future multi-tenant setups) can point them elsewhere at runtime.
"""

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _find_binary(env_name: str, name: str) -> str | None:
    explicit = os.getenv(env_name)
    if explicit:
        return explicit
    return shutil.which(name)


@dataclass
class Settings:
    # Database shared with the legacy app (separate, editor_-prefixed tables).
    db_path: Path = field(default_factory=lambda: Path(os.getenv("EDITOR_DB_PATH", "output/data.db")))
    # Root of local media storage (uploads, downloads, renders).
    storage_root: Path = field(default_factory=lambda: Path(os.getenv("EDITOR_STORAGE_ROOT", "output/editor")))
    ffmpeg: str | None = field(default_factory=lambda: _find_binary("FFMPEG_BINARY", "ffmpeg"))
    ffprobe: str | None = field(default_factory=lambda: _find_binary("FFPROBE_BINARY", "ffprobe"))
    # yt-dlp session cookies live outside the repo; fall back to the legacy location.
    ytdlp_cookies: Path = field(
        default_factory=lambda: Path(os.getenv("YTDLP_COOKIES_FILE") or Path(__file__).parent.parent / "cookies.txt")
    )
    max_upload_bytes: int = field(default_factory=lambda: int(os.getenv("EDITOR_MAX_UPLOAD_BYTES", str(4 * 1024**3))))
    max_youtube_height: int = 1080
    # A running job whose heartbeat is older than this is considered dead and requeued.
    job_stale_seconds: int = 120
    job_max_attempts: int = 2

    @property
    def openai_api_key(self) -> str | None:
        return os.getenv("OPENAI_API_KEY") or None

    @property
    def pexels_api_key(self) -> str | None:
        return os.getenv("PEXELS_API_KEY") or None

    @property
    def pixabay_api_key(self) -> str | None:
        return os.getenv("PIXABAY_API_KEY") or None

    @property
    def image_model(self) -> str:
        return os.getenv("EDITOR_IMAGE_MODEL") or "gpt-image-2.5-flare"


settings = Settings()


def require_ffmpeg() -> str:
    if not settings.ffmpeg:
        # imageio-ffmpeg (a moviepy dependency) ships an ffmpeg binary; use it as a last resort.
        try:
            import imageio_ffmpeg

            settings.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:  # pragma: no cover - environment specific
            raise RuntimeError("ffmpeg not found. Install it or set FFMPEG_BINARY.") from exc
    return settings.ffmpeg


def require_ffprobe() -> str:
    if not settings.ffprobe:
        raise RuntimeError("ffprobe not found. Install ffmpeg or set FFPROBE_BINARY.")
    return settings.ffprobe
