"""
Cut-outs for background replacement: the subject's colours plus an alpha matte, per source range.

Two ways to get the matte:
- "ai": MODNet (Apache-2.0, github.com/ZHKKKe/MODNet) on the CPU through ONNX Runtime. The ONNX
  export comes from the Hugging Face repo Xenova/modnet (Apache-2.0), pinned to one revision and
  checked by SHA-256; downloaded once on first use (or set EDITOR_MODNET_PATH to a local copy).
  The model runs on keyframes (up to MATTE_FPS a second); frames in between get a matte
  interpolated from the keyframes either side, so the matte doesn't lag behind motion. Edge
  colours are then decontaminated (the old background's colour is taken out of see-through
  edge pixels, which otherwise show as a fringe).
- "chroma": a soft colour-difference keyer for green/blue screens. Partly see-through pixels
  (hair strands, motion blur) keep partial alpha, and spill is removed without darkening the
  edges (the green taken out is given back as neutral brightness).

Cut-outs are slow, so only the source ranges the timeline uses are processed, and each is cached
next to the asset as a foreground video (H.264 4:4:4) and an alpha video (lossless grey FFV1):

    assets/<asset_id>.matte/v2-<method>-<start_ms>-<end_ms>.fg.mp4 and .a.mkv

A later render reuses any cached segment that covers a clip, so small trims never redo the work.
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

MATTE_FPS = float(os.getenv("EDITOR_MATTE_FPS", "15"))      # model runs per second of footage
# Short side of the frame the model sees. 448 costs about the same as 384 on a CPU and follows
# outlines a little better; 512 (the model's native size) is ~1.6x slower.
MATTE_SIZE = int(os.getenv("EDITOR_MATTE_SIZE", "448"))
CUTOUT_SIZE = int(os.getenv("EDITOR_CUTOUT_SIZE", "1080"))  # short side of the stored cut-out, at most
MAX_FPS = 30.0
PAD = 0.25            # seconds processed either side of a clip, so small trims reuse the cache
LEVELS = (40 / 255, 210 / 255)   # matte values below/above count as fully background/subject
VERSION = "v2"

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


# ---------------------------------------------------------------- cut-out maths

def ai_alpha(coarse: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Model matte (work size) -> alpha at `size` (w, h), with unsure values firmed up."""
    import cv2

    a = cv2.resize(coarse.astype(np.float32), size, interpolation=cv2.INTER_LINEAR)
    lo, hi = LEVELS
    return np.clip((a - lo) / (hi - lo), 0.0, 1.0)


def decontaminate(rgb: np.ndarray, alpha: np.ndarray, work_short: int = 200) -> np.ndarray:
    """Foreground colours for see-through edge pixels (Forte & Pitie's blur-fusion estimate).

    Runs at a small size for speed; the correction is added back only where 0 < alpha < 1."""
    import cv2

    h, w = alpha.shape
    k = work_short / min(h, w)
    sw, sh = max(8, int(w * k)), max(8, int(h * k))
    I = cv2.resize(rgb, (sw, sh), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    a = cv2.resize(alpha, (sw, sh), interpolation=cv2.INTER_AREA)
    A = a[..., None]

    def step(F, B, r):
        size = (2 * r + 1, 2 * r + 1)
        ba = cv2.blur(a, size)[..., None]
        bFA = cv2.blur(F * A, size) / (ba + 1e-5)
        bB1A = cv2.blur(B * (1 - A), size) / ((1 - ba) + 1e-5)
        F = bFA + A * (I - A * bFA - (1 - A) * bB1A)
        return np.clip(F, 0, 1), np.clip(bB1A, 0, 1)

    F, B = step(I, I, max(3, int(90 * k)))
    F, _ = step(F, B, max(1, int(6 * k)))
    ys, xs = np.nonzero((alpha > 0.01) & (alpha < 0.99))      # only see-through edge pixels change
    out = rgb.copy()
    if len(ys):
        delta = cv2.resize(F - I, (w, h), interpolation=cv2.INTER_LINEAR)
        out[ys, xs] = np.clip(rgb[ys, xs].astype(np.float32) + delta[ys, xs] * 255, 0, 255).astype(np.uint8)
    return out


def chroma_key(rgb: np.ndarray, key_color: str, tolerance: float) -> tuple[np.ndarray, np.ndarray]:
    """Soft colour-difference key: (despilled RGB uint8, alpha float32 0..1)."""
    I = rgb.astype(np.float32)
    kr, kg, kb = (int(key_color[i:i + 2], 16) for i in (1, 3, 5))
    green = kg >= kb
    ch = 1 if green else 2                                    # the screen channel
    c = I[..., ch]
    o = np.maximum(I[..., 0], I[..., 2] if green else I[..., 1])   # strongest other channel
    key_diff = max(1.0, (kg if green else kb) - max(kr, kb if green else kg))
    screen = (c - o) / key_diff                               # 1 = pure screen colour, <= 0 = none
    hi = 1.0 - 2.0 * tolerance                                # at or above: fully keyed out
    lo = hi - 0.55                                            # at or below: fully kept
    alpha = np.clip((hi - screen) / (hi - lo), 0.0, 1.0)
    alpha[alpha < 0.02] = 0.0
    alpha[alpha > 0.98] = 1.0
    # spill: limit the screen channel to the others and give the removed light back as grey,
    # so edges lose the green tint without going dark
    excess = np.clip(c - o, 0, None)
    out = I.copy()
    out[..., ch] -= excess
    out += (excess * (0.7152 if green else 0.0722) * 0.8)[..., None]
    return np.clip(out, 0, 255).astype(np.uint8), alpha.astype(np.float32)


# ---------------------------------------------------------------- cache

def method_tag(method: str, key_color: Optional[str] = None, tolerance: Optional[float] = None) -> str:
    if method == "chroma":
        return f"chroma{(key_color or '#00FF00')[1:].lower()}t{int(round((tolerance or 0.12) * 1000))}"
    return "ai"


def _cache_dir(asset: dict) -> Path:
    base = storage.path(storage.asset_key(asset["user_id"], asset["project_id"], asset["id"], ".mp4")).parent
    return base / f"{asset['id']}.matte"


def _segments(asset: dict, tag: str) -> list[tuple[float, float, Path, Path]]:
    d = _cache_dir(asset)
    if not d.exists():
        return []
    prefix = f"{VERSION}-{tag}-"
    out = []
    for a_path in d.glob(f"{prefix}*.a.mkv"):
        if ".part." in a_path.name:
            continue
        fg = a_path.with_name(a_path.name.replace(".a.mkv", ".fg.mp4"))
        try:
            a, b = a_path.name[len(prefix):-len(".a.mkv")].split("-")
            if fg.exists():
                out.append((int(a) / 1000, int(b) / 1000, fg, a_path))
        except ValueError:
            continue
    return out


def find(asset: dict, start: float, end: float, tag: str = "ai") -> Optional[tuple[Path, Path, float]]:
    """A cached cut-out covering [start, end] of the source: (foreground, alpha, source time it starts at)."""
    for a, b, fg, al in _segments(asset, tag):
        if a <= start + 1e-3 and b >= end - 1e-3:
            return fg, al, a
    return None


def merge_ranges(ranges: list[tuple[float, float]], gap: float = 1.0) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def ensure(asset: dict, source: Path, ranges: list[tuple[float, float]], method: str = "ai",
           key_color: Optional[str] = None, tolerance: Optional[float] = None,
           progress: Optional[Progress] = None) -> dict[tuple[float, float], tuple[Path, Path, float]]:
    """Make sure every (start, end) source range has a cached cut-out; returns where each one is."""
    tag = method_tag(method, key_color, tolerance)
    duration = float(asset.get("duration") or 0) or None
    missing = [r for r in ranges if find(asset, *r, tag=tag) is None]
    todo = []
    for a, b in merge_ranges(missing):
        a = max(0.0, a - PAD)
        b = b + PAD if duration is None else min(duration, b + PAD)
        todo.append((a, b))
    total = sum(b - a for a, b in todo) or 1.0
    done = 0.0
    label = "Separating you from the background" if method == "ai" else "Keying out the green screen"
    for a, b in todo:
        def sub(f: float, _a=a, _b=b, _done=done) -> None:
            if progress:
                progress((_done + f * (_b - _a)) / total, label)
        compute(asset, source, a, b, method, key_color, tolerance, on_progress=sub)
        done += b - a
    result = {}
    for r in ranges:
        hit = find(asset, *r, tag=tag)
        if hit is None:
            raise MatteError(f"No cut-out for {asset['id']} {r[0]:.2f}-{r[1]:.2f}s")
        result[r] = hit
    return result


def _out_size(width: int, height: int) -> tuple[int, int]:
    k = min(1.0, CUTOUT_SIZE / max(1, min(width, height)))
    return max(2, int(width * k / 2) * 2), max(2, int(height * k / 2) * 2)


def compute(asset: dict, source: Path, start: float, end: float, method: str = "ai",
            key_color: Optional[str] = None, tolerance: Optional[float] = None,
            on_progress: Optional[Callable[[float], None]] = None) -> tuple[Path, Path]:
    """Cut the subject out of source [start, end]; saves foreground + alpha videos."""
    import cv2

    ffmpeg = require_ffmpeg()
    width, height = int(asset.get("width") or 0), int(asset.get("height") or 0)
    if not width or not height:
        raise MatteError(f"Asset {asset['id']} has no frame size")
    src_fps = float(asset.get("fps") or 25) or 25.0
    fps = min(src_fps, MAX_FPS)
    ow, oh = _out_size(width, height)
    ww, wh = work_size(width, height)
    step = max(1, round(fps / MATTE_FPS)) if method == "ai" else 1
    length = end - start
    frames_expected = max(1, int(length * fps))

    d = _cache_dir(asset)
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{VERSION}-{method_tag(method, key_color, tolerance)}-{int(round(start * 1000))}-{int(round(end * 1000))}"
    fg_final, a_final = d / f"{stem}.fg.mp4", d / f"{stem}.a.mkv"
    fg_tmp, a_tmp = d / f"{stem}.part.fg.mp4", d / f"{stem}.part.a.mkv"

    dec = subprocess.Popen(
        [ffmpeg, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(source),
         "-an", "-vf", f"fps={fps:g},scale={ow}:{oh}:flags=bicubic", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    enc_fg = subprocess.Popen(
        [ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{ow}x{oh}", "-r", f"{fps:g}",
         "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv444p", str(fg_tmp)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    enc_a = subprocess.Popen(
        [ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{ow}x{oh}", "-r", f"{fps:g}",
         "-i", "-", "-c:v", "ffv1", "-pix_fmt", "gray", str(a_tmp)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert dec.stdout is not None and enc_fg.stdin is not None and enc_a.stdin is not None
    frame_bytes = ow * oh * 3
    n_out = 0

    def emit(rgb: np.ndarray, alpha: np.ndarray) -> None:
        nonlocal n_out
        enc_fg.stdin.write(np.ascontiguousarray(rgb).tobytes())
        enc_a.stdin.write((alpha * 255 + 0.5).astype(np.uint8).tobytes())
        n_out += 1
        if on_progress and n_out % 10 == 0:
            on_progress(min(0.99, n_out / frames_expected))

    def frames():
        while True:
            buf = dec.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                return
            yield np.frombuffer(buf, np.uint8).reshape(oh, ow, 3)

    def ai_finish(rgb: np.ndarray, coarse: np.ndarray) -> None:
        alpha = ai_alpha(coarse, (ow, oh))
        emit(decontaminate(rgb, alpha), alpha)

    try:
        if method == "chroma":
            for rgb in frames():
                fg, alpha = chroma_key(rgb, key_color or "#00FF00", tolerance or 0.12)
                emit(fg, alpha)
        else:
            # The model runs on keyframes every `step` frames; frames in between get a matte
            # interpolated from the keyframes either side (no lag behind motion).
            pending: list[np.ndarray] = []
            prev: Optional[np.ndarray] = None
            for i, rgb in enumerate(frames()):
                if i % step:
                    pending.append(rgb)
                    continue
                cur = predict(cv2.resize(rgb, (ww, wh), interpolation=cv2.INTER_AREA))
                if prev is not None:
                    for j, mid in enumerate(pending, start=1):
                        t = j / (len(pending) + 1)
                        ai_finish(mid, (1 - t) * prev + t * cur)
                pending = []
                ai_finish(rgb, cur)
                prev = cur
            for mid in pending:          # frames after the last keyframe
                ai_finish(mid, prev)
    finally:
        for proc in (enc_fg, enc_a):
            proc.stdin.close()
        dec.stdout.close()
        errs = dec.stderr.read().decode("utf-8", "replace") if dec.stderr else ""
        dec.wait()
        for proc in (enc_fg, enc_a):
            errs += proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
            proc.wait()
    if n_out == 0 or enc_fg.returncode != 0 or enc_a.returncode != 0:
        fg_tmp.unlink(missing_ok=True)
        a_tmp.unlink(missing_ok=True)
        raise MatteError(f"Cut-out failed: {errs.strip()[-500:] or 'no frames decoded'}")
    fg_tmp.replace(fg_final)
    a_tmp.replace(a_final)
    if on_progress:
        on_progress(1.0)
    return fg_final, a_final


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
