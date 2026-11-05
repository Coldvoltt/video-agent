"""
Audio/visual analysis with ffmpeg + numpy (no API cost).

Used by the agent on demand (silences) and to build style profiles of reference videos
(cuts, colour, tempo, loudness, sample frames for a vision model).
"""

import re
import subprocess
from pathlib import Path

import numpy as np

from . import colormatch, media
from .config import require_ffmpeg

_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")
_PTS_TIME = re.compile(r"pts_time:\s*([\d.]+)")
_MEAN_VOL = re.compile(r"mean_volume:\s*(-?[\d.]+) dB")


def detect_silences(path: Path, duration: float | None, min_silence: float = 0.5,
                    noise_db: float = -35.0) -> list[tuple[float, float]]:
    """Ranges (seconds) where the audio stays below `noise_db` for at least `min_silence`."""
    proc = media._run([
        require_ffmpeg(), "-hide_banner", "-nostats", "-i", str(path), "-vn",
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-",
    ])
    out: list[tuple[float, float]] = []
    start = None
    for line in proc.stderr.splitlines():
        if (m := _SIL_START.search(line)):
            start = max(0.0, float(m.group(1)))
        elif (m := _SIL_END.search(line)) and start is not None:
            out.append((round(start, 3), round(float(m.group(1)), 3)))
            start = None
    if start is not None and duration:  # silent until the end
        out.append((round(start, 3), round(duration, 3)))
    return out


# ---------------------------------------------------------------- pacing

CUT_FPS = 15
CUT_WIDTH = 96


def _small_frames(path: Path, limit_seconds: float, fps: int = CUT_FPS, width: int = CUT_WIDTH) -> np.ndarray:
    """Small RGB frames (n, h, w, 3) in 0..1. Colour matters: two shots can share a brightness."""
    first = subprocess.run([
        require_ffmpeg(), "-v", "error", "-i", str(path), "-frames:v", "1", "-vf", f"scale={width}:-2",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ], capture_output=True, timeout=120).stdout
    height = len(first) // (width * 3)
    if height < 8:
        raise media.MediaError("Could not read video frames")
    raw = subprocess.run([
        require_ffmpeg(), "-v", "error", "-t", f"{limit_seconds:.3f}", "-i", str(path), "-an",
        "-vf", f"fps={fps},scale={width}:-2", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ], capture_output=True, timeout=900).stdout
    n = len(raw) // (width * height * 3)
    return np.frombuffer(raw[: n * width * height * 3], np.uint8).reshape(n, height, width, 3).astype(np.float32) / 255


def _zoom(img: np.ndarray, z: float) -> np.ndarray:
    h, w = img.shape[:2]
    ys = ((h - h / z) / 2 + np.arange(h) / z).astype(int)
    xs = ((w - w / z) / 2 + np.arange(w) / z).astype(int)
    return img[ys][:, xs]


def detect_cuts(path: Path, limit_seconds: float) -> list[float]:
    """
    Timestamps of cuts, including jump cuts / punch-ins within one continuous take.

    ffmpeg's scene score can't tell a punch-in (same shot, closer) from a caption change or
    camera motion, so frames are compared directly at low resolution, 15 fps:
    - a different shot changes most of the frame at once, as a spike against the frames around it;
    - a punch-in is a strong change that mostly disappears when the previous frame is zoomed.
    Captions change only a band of the frame; motion raises the difference for many frames in a row.
    """
    f = _small_frames(path, limit_seconds)
    if len(f) < 3:
        return []
    m = 4                                                   # ignore borders (zoom sampling edges)
    core = f[:, m:-m, m:-m]
    d = np.abs(np.diff(core, axis=0)).mean(axis=3)          # (n-1, h, w), averaged over R, G, B
    d0 = d.mean(axis=(1, 2))
    gh = gw = 6
    hh, ww = d.shape[1] // gh * gh, d.shape[2] // gw * gw
    cells = d[:, :hh, :ww].reshape(len(d), gh, hh // gh, gw, ww // gw).mean(axis=(2, 4)).reshape(len(d), -1)

    cuts: list[float] = []
    for i in range(len(d0)):
        lo, hi = max(0, i - 8), min(len(d0), i + 9)
        neighbours = np.r_[d0[lo:i], d0[i + 1:hi]]
        local = float(np.median(neighbours)) if len(neighbours) else 0.0
        is_peak = d0[i] >= d0[max(0, i - 2):i + 3].max()
        if not is_peak or d0[i] < 0.015:
            continue
        cell_floor = max(0.02, 2.5 * float(np.median(cells[lo:hi], axis=0).mean()))
        changed = float((cells[i] > cell_floor).mean())
        new_shot = d0[i] / (local + 0.004) > 3 and changed >= 0.5
        punch_in = False
        if not new_shot and d0[i] > 0.04:
            a, b = f[i], f[i + 1]
            dz = min(
                min(np.abs(_zoom(a, z) - b)[m:-m, m:-m].mean(), np.abs(a - _zoom(b, z))[m:-m, m:-m].mean())
                for z in (1.08, 1.12, 1.16, 1.2, 1.25, 1.3)
            )
            punch_in = dz < 0.7 * d0[i]
        if new_shot or punch_in:
            t = round((i + 1) / CUT_FPS, 3)
            if not cuts or t - cuts[-1] >= 0.25:
                cuts.append(t)
    return cuts


def pacing_stats(cuts: list[float], duration: float) -> dict:
    bounds = [0.0, *[c for c in cuts if 0 < c < duration], duration]
    shots = [b - a for a, b in zip(bounds, bounds[1:]) if b - a > 0.04]
    return {
        "cuts": len(bounds) - 2,
        "cuts_per_minute": round((len(bounds) - 2) / duration * 60, 1) if duration else 0.0,
        "avg_shot_seconds": round(float(np.mean(shots)), 2) if shots else round(duration, 2),
        "median_shot_seconds": round(float(np.median(shots)), 2) if shots else round(duration, 2),
        "shortest_shot_seconds": round(min(shots), 2) if shots else None,
        "longest_shot_seconds": round(max(shots), 2) if shots else None,
    }


# ---------------------------------------------------------------- frames & colour

def sample_frames(path: Path, duration: float, count: int = 24, width: int = 160,
                  src_size: tuple[int, int] | None = None, limit_seconds: float | None = None) -> np.ndarray:
    """`count` frames spread evenly over the video, as uint8 RGB (n, h, w, 3)."""
    span = min(duration, limit_seconds or duration)
    if src_size and src_size[0] and src_size[1]:
        height = max(2, int(round(width * src_size[1] / src_size[0] / 2)) * 2)
    else:
        height = width * 9 // 16 // 2 * 2
    rate = max(count / max(span, 0.1), 0.01)
    proc = subprocess.run([
        require_ffmpeg(), "-v", "error", "-t", f"{span:.3f}", "-i", str(path), "-an",
        "-vf", f"fps={rate:.6f},scale={width}:{height}", "-frames:v", str(count),
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ], capture_output=True, timeout=900)
    if proc.returncode != 0:
        raise media.MediaError(f"ffmpeg failed sampling frames: {proc.stderr.decode(errors='replace')[-500:]}")
    frame_bytes = width * height * 3
    n = len(proc.stdout) // frame_bytes
    if n == 0:
        raise media.MediaError("No frames could be read from the video")
    return np.frombuffer(proc.stdout[: n * frame_bytes], np.uint8).reshape(n, height, width, 3)


def frames_at(path: Path, times: list[float], width: int = 160, height: int = 90) -> np.ndarray:
    """Frames at specific timestamps (uint8 RGB), all scaled to width x height."""
    out = []
    for t in times:
        proc = subprocess.run([
            require_ffmpeg(), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-frames:v", "1",
            "-vf", f"scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ], capture_output=True, timeout=120)
        if proc.returncode == 0 and len(proc.stdout) >= width * height * 3:
            out.append(np.frombuffer(proc.stdout[: width * height * 3], np.uint8).reshape(height, width, 3))
    if not out:
        raise media.MediaError("No frames could be read")
    return np.stack(out)


def jpeg_frames(path: Path, times: list[float], width: int = 512) -> list[bytes]:
    """JPEG stills for a vision model."""
    out = []
    for t in times:
        proc = subprocess.run([
            require_ffmpeg(), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-frames:v", "1",
            "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "-",
        ], capture_output=True, timeout=120)
        if proc.returncode == 0 and proc.stdout:
            out.append(proc.stdout)
    return out


def color_stats(frames: np.ndarray) -> dict:
    """Overall look of a set of frames: brightness, contrast, saturation, warmth, Lab statistics."""
    rgb = frames.reshape(-1, 3).astype(np.float64) / 255.0
    luma = rgb @ np.array([0.2126, 0.7152, 0.0722])
    mx, mn = rgb.max(axis=1), rgb.min(axis=1)
    sat = np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    lab = colormatch.srgb_to_lab(rgb)
    return {
        "brightness": round(float(luma.mean()), 3),          # 0 black .. 1 white
        "contrast": round(float(luma.std()), 3),             # spread of brightness
        "saturation": round(float(sat.mean()), 3),           # 0 grey .. 1 vivid
        "warmth": round(float((rgb[:, 0] - rgb[:, 2]).mean()), 3),   # >0 warm, <0 cool
        "lab_mean": [round(float(v), 3) for v in lab.mean(axis=0)],
        "lab_std": [round(float(v), 3) for v in lab.std(axis=0)],
    }


# ---------------------------------------------------------------- audio

def loudness(path: Path, limit_seconds: float) -> float | None:
    proc = subprocess.run([
        require_ffmpeg(), "-hide_banner", "-nostats", "-t", f"{limit_seconds:.3f}", "-i", str(path), "-vn",
        "-af", "volumedetect", "-f", "null", "-",
    ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    m = _MEAN_VOL.search(proc.stderr)
    return float(m.group(1)) if m else None


def estimate_tempo(path: Path, limit_seconds: float, sample_rate: int = 11025, hop: int = 512) -> dict:
    """
    Rough beat tempo from an onset-strength envelope and its autocorrelation.
    Returns {"bpm": float | None, "confidence": float}; bpm is None when there is no clear pulse
    (speech-only audio usually has none).
    """
    proc = subprocess.run([
        require_ffmpeg(), "-v", "error", "-t", f"{limit_seconds:.3f}", "-i", str(path), "-vn", "-ac", "1",
        "-ar", str(sample_rate), "-f", "f32le", "-",
    ], capture_output=True, timeout=900)
    x = np.frombuffer(proc.stdout, np.float32)
    if len(x) < sample_rate * 4:
        return {"bpm": None, "confidence": 0.0}
    n = len(x) // hop
    energy = (x[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1)
    onset = np.maximum(0.0, np.diff(np.log(energy + 1e-10)))
    onset -= onset.mean()
    if not onset.any():
        return {"bpm": None, "confidence": 0.0}
    ac = np.correlate(onset, onset, mode="full")[len(onset) - 1:]
    ac /= ac[0] if ac[0] else 1.0
    hop_s = hop / sample_rate
    lo, hi = int(60 / 180 / hop_s), int(60 / 60 / hop_s) + 1     # 60..180 BPM
    if hi >= len(ac):
        return {"bpm": None, "confidence": 0.0}
    window = ac[lo:hi]
    # A beat also correlates at 2x, 3x its period; prefer the shortest lag whose peak is
    # nearly as strong as the best one, so 120 BPM isn't reported as 60.
    best = float(window.max())
    peaks = [i for i in range(1, len(window) - 1)
             if window[i] >= window[i - 1] and window[i] >= window[i + 1] and window[i] >= 0.85 * best]
    lag = lo + (peaks[0] if peaks else int(np.argmax(window)))
    peak = float(ac[lag])
    confidence = round(peak / (float(np.abs(window).mean()) + 1e-9), 2)
    bpm = 60 / (lag * hop_s)
    return {"bpm": round(bpm, 1) if peak > 0.15 and confidence > 2.5 else None, "confidence": confidence}
