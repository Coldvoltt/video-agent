"""
Person mattes for background replacement, with MODNet (Apache-2.0, github.com/ZHKKKe/MODNet).

The model runs on the CPU through ONNX Runtime. The ONNX export comes from the
Hugging Face repo Xenova/modnet (Apache-2.0), pinned to one revision and checked by
SHA-256; it is downloaded once on first use (or set EDITOR_MODNET_PATH to a local copy).

Mattes are slow to compute (about 0.2-0.5 s a frame), so only the source ranges the
timeline actually uses are matted, at up to MATTE_FPS, and each range is cached as a
small lossless grey video next to the asset:

    assets/<asset_id>.matte/<start_ms>-<end_ms>.mkv

White is the subject, black is background. A later render reuses any cached segment
that covers a clip, so trimming a clip a little never re-runs the model.
"""

from __future__ import annotations

import functools
import hashlib
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from . import storage
from .config import require_ffmpeg, settings

MODEL_REVISION = "fa2fa546052fba4c08921230a26cc69a333fca12"
MODEL_URL = f"https://huggingface.co/Xenova/modnet/resolve/{MODEL_REVISION}/onnx/model.onnx"
MODEL_SHA256 = "07c308cf0fc7e6e8b2065a12ed7fc07e1de8febb7dc7839d7b7f15dd66584df9"
CREDIT = "Background removal by MODNet (Apache-2.0)"

MATTE_FPS = float(os.getenv("EDITOR_MATTE_FPS", "15"))
# Short side of the frame the model sees. 384 is a good balance on a CPU; 512 is the
# model's native size and a little sharper at about twice the time.
MATTE_SIZE = int(os.getenv("EDITOR_MATTE_SIZE", "384"))
PAD = 0.25            # seconds matted either side of a clip, so small trims reuse the cache
SMOOTH = 0.25         # weight of the previous frame's matte: cuts flicker, adds little lag

Progress = Callable[[float, str], None]


class MatteError(RuntimeError):
    pass


# ---------------------------------------------------------------- model

def model_path() -> Path:
    explicit = os.getenv("EDITOR_MODNET_PATH")
    if explicit:
        return Path(explicit)
    return settings.storage_root / "models" / "modnet.onnx"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


_download_lock = threading.Lock()


def ensure_model() -> Path:
    path = model_path()
    if path.exists():
        return path
    if os.getenv("EDITOR_MODNET_PATH"):
        raise MatteError(f"EDITOR_MODNET_PATH points to a missing file: {path}")
    with _download_lock:
        if path.exists():
            return path
        import httpx

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".download")
        try:
            with httpx.stream("GET", MODEL_URL, follow_redirects=True, timeout=120) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
        except httpx.HTTPError as e:
            tmp.unlink(missing_ok=True)
            raise MatteError(f"Couldn't download the background-removal model: {e}") from e
        if _sha256(tmp) != MODEL_SHA256:
            tmp.unlink(missing_ok=True)
            raise MatteError("The downloaded background-removal model failed its checksum")
        tmp.replace(path)
    return path


@functools.lru_cache(maxsize=1)
def _session():
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = max(1, (os.cpu_count() or 2) - 1)
    return ort.InferenceSession(str(ensure_model()), opts, providers=["CPUExecutionProvider"])


def predict(rgb: np.ndarray) -> np.ndarray:
    """Matte for one RGB frame (H x W x 3 uint8, sides multiples of 32): float32 H x W in 0..1."""
    x = rgb.astype(np.float32) / 127.5 - 1.0
    out = _session().run(None, {"input": x.transpose(2, 0, 1)[None]})[0]
    return out[0, 0]


def work_size(width: int, height: int, short: int = MATTE_SIZE) -> tuple[int, int]:
    """Frame size the model sees: short side about `short`, both sides multiples of 32."""
    k = short / max(1, min(width, height))
    return max(32, round(width * k / 32) * 32), max(32, round(height * k / 32) * 32)


# ---------------------------------------------------------------- cache

def _cache_dir(asset: dict) -> Path:
    base = storage.path(storage.asset_key(asset["user_id"], asset["project_id"], asset["id"], ".mp4")).parent
    return base / f"{asset['id']}.matte"


def _segments(asset: dict) -> list[tuple[float, float, Path]]:
    d = _cache_dir(asset)
    if not d.exists():
        return []
    out = []
    for p in d.glob("*.mkv"):
        try:
            a, b = p.stem.split("-")
            out.append((int(a) / 1000, int(b) / 1000, p))
        except ValueError:
            continue
    return out


def find(asset: dict, start: float, end: float) -> Optional[tuple[Path, float]]:
    """A cached matte covering [start, end] of the source: (file, the source time it starts at)."""
    for a, b, p in _segments(asset):
        if a <= start + 1e-3 and b >= end - 1e-3:
            return p, a
    return None


def merge_ranges(ranges: list[tuple[float, float]], gap: float = 1.0) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def ensure(asset: dict, source: Path, ranges: list[tuple[float, float]],
           progress: Optional[Progress] = None) -> dict[tuple[float, float], tuple[Path, float]]:
    """Make sure every (start, end) source range has a cached matte; returns where each one is."""
    duration = float(asset.get("duration") or 0) or None
    missing = [r for r in ranges if find(asset, *r) is None]
    todo = []
    for a, b in merge_ranges(missing):
        a = max(0.0, a - PAD)
        b = b + PAD if duration is None else min(duration, b + PAD)
        todo.append((a, b))
    total = sum(b - a for a, b in todo) or 1.0
    done = 0.0
    for a, b in todo:
        def sub(f: float, _a=a, _b=b, _done=done) -> None:
            if progress:
                progress((_done + f * (_b - _a)) / total, "Separating you from the background")
        compute(asset, source, a, b, on_progress=sub)
        done += b - a
    result = {}
    for r in ranges:
        hit = find(asset, *r)
        if hit is None:
            raise MatteError(f"No matte for {asset['id']} {r[0]:.2f}-{r[1]:.2f}s")
        result[r] = hit
    return result


def compute(asset: dict, source: Path, start: float, end: float,
            on_progress: Optional[Callable[[float], None]] = None) -> Path:
    """Run the model over source [start, end] and save the matte as a grey FFV1 video."""
    ffmpeg = require_ffmpeg()
    width, height = int(asset.get("width") or 0), int(asset.get("height") or 0)
    if not width or not height:
        raise MatteError(f"Asset {asset['id']} has no frame size")
    src_fps = float(asset.get("fps") or MATTE_FPS)
    fps = min(MATTE_FPS, src_fps) if src_fps > 0 else MATTE_FPS
    ww, wh = work_size(width, height)
    length = end - start
    frames_expected = max(1, int(length * fps))

    d = _cache_dir(asset)
    d.mkdir(parents=True, exist_ok=True)
    final = d / f"{int(round(start * 1000))}-{int(round(end * 1000))}.mkv"
    tmp = final.with_suffix(".part.mkv")

    dec = subprocess.Popen(
        [ffmpeg, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(source),
         "-an", "-vf", f"fps={fps:g},scale={ww}:{wh}:flags=bilinear", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    enc = subprocess.Popen(
        [ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{ww}x{wh}", "-r", f"{fps:g}",
         "-i", "-", "-c:v", "ffv1", "-pix_fmt", "gray", str(tmp)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert dec.stdout is not None and enc.stdin is not None
    frame_bytes = ww * wh * 3
    prev = None
    n = 0
    try:
        while True:
            buf = dec.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            m = predict(np.frombuffer(buf, np.uint8).reshape(wh, ww, 3))
            if prev is not None:
                m = (1 - SMOOTH) * m + SMOOTH * prev
            prev = m
            enc.stdin.write((np.clip(m, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes())
            n += 1
            if on_progress and n % 5 == 0:
                on_progress(min(0.99, n / frames_expected))
    finally:
        enc.stdin.close()
        dec.stdout.close()
        dec_err = dec.stderr.read().decode("utf-8", "replace") if dec.stderr else ""
        dec.wait()
        enc_err = enc.stderr.read().decode("utf-8", "replace") if enc.stderr else ""
        enc.wait()
    if n == 0 or enc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise MatteError(f"Matting failed: {(dec_err + enc_err).strip()[-500:] or 'no frames decoded'}")
    tmp.replace(final)
    if on_progress:
        on_progress(1.0)
    return final


def clear(asset: dict) -> None:
    shutil.rmtree(_cache_dir(asset), ignore_errors=True)


def screen_color(source: Path, at: float = 0.0) -> Optional[str]:
    """Colour of a green/blue screen: the median of the frame's edges, if it looks like one."""
    ffmpeg = require_ffmpeg()
    raw = subprocess.run(
        [ffmpeg, "-v", "error", "-ss", f"{max(0.0, at):.3f}", "-i", str(source), "-frames:v", "1",
         "-vf", "scale=160:90", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
    ).stdout
    if len(raw) < 160 * 90 * 3:
        return None
    img = np.frombuffer(raw[:160 * 90 * 3], np.uint8).reshape(90, 160, 3).astype(int)
    edge = np.concatenate([img[:6].reshape(-1, 3), img[-6:].reshape(-1, 3),
                           img[:, :8].reshape(-1, 3), img[:, -8:].reshape(-1, 3)])
    r, g, b = (int(v) for v in np.median(edge, axis=0))
    dominant = max(g, b)
    if dominant < 80 or dominant - max(r, min(g, b)) < 40:
        return None   # edges aren't a saturated green or blue
    return f"#{r:02X}{g:02X}{b:02X}"
