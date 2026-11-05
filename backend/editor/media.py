"""
Media helpers: probing, YouTube download, audio extraction and word-level transcription.
All heavy lifting is done by ffmpeg/ffprobe subprocesses (no frame processing in Python).
"""

import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from .config import require_ffmpeg, require_ffprobe, settings

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
LUT_EXTS = {".cube"}
ALLOWED_UPLOAD_EXTS = VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS | LUT_EXTS

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be", "music.youtube.com"}

ProgressFn = Callable[[float, str], None]


class MediaError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        raise MediaError(f"{Path(cmd[0]).name} failed: {proc.stderr.strip()[-1500:]}")
    return proc


# ---------------------------------------------------------------- probing

def _parse_rate(rate: str | None) -> float | None:
    if not rate or rate in ("0/0", "0"):
        return None
    if "/" in rate:
        num, den = rate.split("/", 1)
        try:
            return float(num) / float(den) if float(den) else None
        except ValueError:
            return None
    try:
        return float(rate)
    except ValueError:
        return None


def _rotation(stream: dict) -> int:
    """Display rotation in degrees (0, 90, 180, 270) from side data or the legacy `rotate` tag."""
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return int(round(float(side["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    try:
        return int(stream.get("tags", {}).get("rotate", 0)) % 360
    except (TypeError, ValueError):
        return 0


def probe(path: Path) -> dict:
    """Return kind, duration, size, fps and stream flags for a media file."""
    proc = _run([
        require_ffprobe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path),
    ], timeout=120)
    info = json.loads(proc.stdout or "{}")
    streams = info.get("streams", [])
    video = next(
        (s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = None
    for candidate in (info.get("format", {}).get("duration"), (video or {}).get("duration"), (audio or {}).get("duration")):
        try:
            if candidate is not None and math.isfinite(float(candidate)):
                duration = float(candidate)
                break
        except ValueError:
            continue

    ext = path.suffix.lower()
    if video and (ext in IMAGE_EXTS or (duration is None or duration == 0) and not audio):
        kind = "image"
    elif video:
        kind = "video"
    elif audio:
        kind = "audio"
    else:
        raise MediaError("File has no audio or video streams")

    if kind != "image" and not duration:
        raise MediaError("Could not determine media duration")

    width, height = (video.get("width"), video.get("height")) if video else (None, None)
    if video and _rotation(video) % 180 == 90:
        # Phones store portrait video as landscape pixels plus a rotation flag;
        # ffmpeg applies the rotation when decoding, so report the displayed shape.
        width, height = height, width

    return {
        "kind": kind,
        "duration": None if kind == "image" else duration,
        "width": width,
        "height": height,
        "fps": (_parse_rate(video.get("avg_frame_rate")) or _parse_rate(video.get("r_frame_rate"))) if video and kind == "video" else None,
        "has_video": bool(video),
        "has_audio": bool(audio) and kind != "image",
    }


def inspect_lut(path: Path) -> dict:
    """Validate a .cube 3D LUT (the format editors and colourists export)."""
    size, rows = None, 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            head = line.split()[0].upper()
            if head == "LUT_3D_SIZE":
                size = int(line.split()[1])
            elif head == "LUT_1D_SIZE":
                raise MediaError("1D LUTs are not supported; export a 3D .cube LUT")
            elif head in ("TITLE", "DOMAIN_MIN", "DOMAIN_MAX", "LUT_3D_INPUT_RANGE"):
                continue
            else:
                parts = line.split()
                if len(parts) != 3:
                    raise MediaError(f"Unexpected line in LUT: {line[:40]}")
                rows += 1
    if not size or not 2 <= size <= 65:
        raise MediaError("Not a valid 3D LUT (missing or unusual LUT_3D_SIZE)")
    if rows != size ** 3:
        raise MediaError(f"LUT has {rows} entries; expected {size ** 3}")
    return {"kind": "lut", "duration": None, "width": None, "height": None, "fps": None,
            "has_video": False, "has_audio": False}


# ---------------------------------------------------------------- YouTube

def is_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and parsed.hostname in YOUTUBE_HOSTS


def _ytdlp_base_opts(cookie_copy_dir: Path) -> dict:
    opts: dict = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True, "js_runtimes": {"node": {}}}
    if settings.ytdlp_cookies and Path(settings.ytdlp_cookies).is_file():
        # yt-dlp writes cookies back to the file it was given; hand it a throwaway
        # copy so the shared cookie file is never modified (or emptied) by a download.
        copy = cookie_copy_dir / "cookies.txt"
        shutil.copyfile(settings.ytdlp_cookies, copy)
        opts["cookiefile"] = str(copy)
    ffmpeg = require_ffmpeg()
    opts["ffmpeg_location"] = ffmpeg
    return opts


def download_youtube(url: str, out_dir: Path, basename: str, on_progress: Optional[ProgressFn] = None) -> dict:
    """Download the full video (<= max height) as mp4. Returns path and title."""
    if not is_youtube_url(url):
        raise MediaError("Only YouTube links are supported")
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    h = settings.max_youtube_height

    def hook(d: dict) -> None:
        if on_progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                on_progress(min(0.99, d.get("downloaded_bytes", 0) / total), "Downloading video")

    cookie_dir = Path(tempfile.mkdtemp(prefix="ytdlp_"))
    opts = {
        **_ytdlp_base_opts(cookie_dir),
        "format": f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / f"{basename}.%(ext)s"),
        "progress_hooks": [hook],
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info.get("_type") == "playlist":
                raise MediaError("Playlists are not supported; share a single video link")
    finally:
        shutil.rmtree(cookie_dir, ignore_errors=True)

    matches = sorted(p for p in out_dir.glob(f"{basename}.*") if p.suffix.lower() in VIDEO_EXTS)
    if not matches:
        raise MediaError("Download finished but no video file was produced")
    return {"path": matches[0], "title": info.get("title") or "YouTube video"}


# ---------------------------------------------------------------- transcription

WHISPER_MAX_BYTES = 24 * 1024 * 1024  # API limit is 25 MB; keep headroom
CHUNK_SECONDS = 20 * 60
FILLER_PROMPT = "Umm, so, uh, let me think... Hmm. Okay, so I was, like, um, saying that we should, uh, start."


def extract_audio(src: Path, dst: Path, start: float | None = None, duration: float | None = None) -> Path:
    """Mono 16 kHz 48 kbps mp3: small enough for ~70 minutes per Whisper request."""
    cmd = [require_ffmpeg(), "-y", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(dst)]
    _run(cmd)
    return dst


def _transcribe_file(client, audio_path: Path, language: str | None) -> dict:
    kwargs = {
        "model": "whisper-1",
        "response_format": "verbose_json",
        "timestamp_granularities": ["word", "segment"],
        # Whisper tends to drop disfluencies; a prompt written with them keeps
        # "um"/"uh" in the transcript so filler removal can find them.
        "prompt": FILLER_PROMPT,
    }
    if language:
        kwargs["language"] = language
    with open(audio_path, "rb") as f:
        resp = client.audio.transcriptions.create(file=f, **kwargs)
    return {
        "segments": [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in (resp.segments or [])],
        "words": [{"word": w.word, "start": w.start, "end": w.end} for w in (resp.words or [])],
        "language": resp.language,
        "duration": resp.duration,
    }


def transcribe(media_path: Path, work_dir: Path, duration: float, language: str | None = None,
               on_progress: Optional[ProgressFn] = None) -> dict:
    """Word + segment level transcript of a media file's audio."""
    if not settings.openai_api_key:
        raise MediaError("OPENAI_API_KEY is not set")
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    work_dir.mkdir(parents=True, exist_ok=True)

    full = extract_audio(media_path, work_dir / "audio.mp3")
    if full.stat().st_size <= WHISPER_MAX_BYTES:
        windows = [(0.0, None, full)]
    else:
        windows = []
        n = math.ceil(duration / CHUNK_SECONDS)
        for i in range(n):
            start = i * CHUNK_SECONDS
            windows.append((start, CHUNK_SECONDS, extract_audio(media_path, work_dir / f"audio_{i}.mp3", start, CHUNK_SECONDS)))

    segments, words, language_found = [], [], None
    for i, (offset, _, path) in enumerate(windows):
        if on_progress:
            on_progress(i / len(windows), "Transcribing" if len(windows) == 1 else f"Transcribing part {i + 1} of {len(windows)}")
        part = _transcribe_file(client, path, language)
        language_found = language_found or part["language"]
        segments += [{**s, "start": s["start"] + offset, "end": s["end"] + offset} for s in part["segments"]]
        words += [{**w, "start": w["start"] + offset, "end": w["end"] + offset} for w in part["words"]]

    for _, _, path in windows:
        try:
            os.remove(path)
        except OSError:
            pass
    if full.exists():
        os.remove(full)

    return {
        "segments": segments,
        "words": words,
        "full_text": " ".join(s["text"] for s in segments),
        "language": language_found,
        "duration": duration,
    }


def make_proxy(src: Path, dst: Path, max_height: int = 720) -> Path:
    """Small H.264/AAC copy that any browser can play (for the live preview). Rotation is applied."""
    _run([
        require_ffmpeg(), "-y", "-v", "error", "-i", str(src),
        "-vf", f"scale=-2:'min({max_height},ih)'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(dst),
    ], timeout=3600)
    return dst
