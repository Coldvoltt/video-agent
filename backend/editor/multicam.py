"""
Multicam: up to three camera angles of the same take, synced, trimmed to the same length, with the
best audio.

Sync, per angle (and the optional separate audio recording), against the first angle:
1. Coarse: transcripts. Runs of three words said in both recordings give the time difference
   (median over all matches), when both have a transcript.
2. Fine: audio. The onset envelopes (how the loudness rises, 100 per second) are cross-correlated,
   within +-1.5 s of the transcript estimate when there is one, otherwise over the whole range; then
   the waveforms are cross-correlated within +-40 ms of that for sample accuracy. Onsets work
   across very different microphones, since every mic hears the same syllables start.

The group runs only where every angle (and the audio recording) has material, so all angles are
trimmed to the same length. Its audio is the separate recording if one was given, otherwise the
angle whose sound is clearest: the biggest gap between speech level and background noise, with a
penalty for clipped (distorted) samples.

The result is an asset of kind "multicam": its file is that audio (for silence detection), its
transcript is the chosen audio's transcript moved to group time, and its analysis holds the angles:

    {"angles": [{"asset_id", "offset", "label", "description"}], "audio": {"asset_id", "offset", "reason"},
     "sync": [{"asset_id", "offset", "method", "confidence"}], "duration"}

`offset` is the time in that recording at group time 0. Clips on the multicam asset have an `angle`
(0, 1, 2): the picture comes from that angle at clip time + its offset, the sound always from the
group audio.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from . import edits, media, storage, store
from .config import require_ffmpeg

log = logging.getLogger(__name__)

SR = 8000                 # sample rate for sync (speech onsets live well below 4 kHz)
HOP = 0.01                # envelope step (s)
MAX_ANGLES = 3
MIN_OVERLAP = 2.0         # seconds all recordings must share

Progress = Callable[[float, str], None]


class MulticamError(ValueError):
    """A problem the user can fix (shown as is)."""


# ---------------------------------------------------------------- audio features

def load_audio(path: Path, sr: int = SR) -> np.ndarray:
    raw = subprocess.run(
        [require_ffmpeg(), "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True, timeout=1800,
    ).stdout
    return np.frombuffer(raw, np.float32).copy()


def envelope(x: np.ndarray, sr: int = SR) -> np.ndarray:
    """Onset strength, one value per HOP: how fast the loudness rises (mic-independent)."""
    n = int(sr * HOP)
    frames = len(x) // n
    if frames < 3:
        return np.zeros(max(frames, 1), np.float32)
    rms = np.sqrt((x[: frames * n].reshape(frames, n) ** 2).mean(axis=1) + 1e-10)
    loud = 20 * np.log10(rms + 1e-5)
    onset = np.maximum(0.0, np.diff(loud, prepend=loud[0]))
    onset -= onset.mean()
    std = onset.std()
    return (onset / std if std > 0 else onset).astype(np.float32)


def xcorr(ref: np.ndarray, src: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Cross-correlation c[k] = sum ref[n] * src[n + k] for every lag k (lags returned alongside)."""
    n = len(ref) + len(src) - 1
    size = 1 << (n - 1).bit_length()
    c = np.fft.irfft(np.conj(np.fft.rfft(ref, size)) * np.fft.rfft(src, size), size)
    lags = np.concatenate([np.arange(0, len(src)), np.arange(-(len(ref) - 1), 0)])
    vals = np.concatenate([c[: len(src)], c[size - (len(ref) - 1):]])
    return lags, vals


def envelope_offset(ref: np.ndarray, src: np.ndarray, guess: Optional[float] = None,
                    window: float = 1.5) -> tuple[float, float]:
    """Seconds into `src` of what is at second 0 of `ref`, and a confidence (peak / runner-up)."""
    lags, vals = xcorr(envelope(ref), envelope(src))
    secs = lags * HOP
    if guess is not None:
        keep = np.abs(secs - guess) <= window
        if keep.any():
            lags, vals, secs = lags[keep], vals[keep], secs[keep]
    best = int(np.argmax(vals))
    away = np.abs(secs - secs[best]) > 0.25
    runner = float(vals[away].max()) if away.any() else 0.0
    conf = float(vals[best] / runner) if runner > 0 else 10.0
    return float(secs[best]), conf


def refine_offset(ref: np.ndarray, src: np.ndarray, coarse: float, sr: int = SR, search: float = 0.04) -> float:
    """Sample-accurate offset near `coarse`, from the waveforms of the loudest stretch."""
    win = int(sr * 8)
    if len(ref) < sr:
        return coarse
    # loudest 8 s of the reference that the source also covers
    step = sr
    best, start = -1.0, None
    for a in range(0, max(1, len(ref) - win), step):
        b = a + int(coarse * sr)
        if b - int(search * sr) < 0 or b + win + int(search * sr) > len(src):
            continue
        e = float((ref[a:a + win] ** 2).sum())
        if e > best:
            best, start = e, a
    if start is None:
        return coarse
    r = ref[start:start + win]
    m = int(search * sr)
    b0 = start + int(round(coarse * sr)) - m
    s = src[b0:b0 + win + 2 * m]
    if len(s) < win + 2 * m:
        return coarse
    lags, vals = xcorr(r - r.mean(), s - s.mean())
    keep = (lags >= 0) & (lags <= 2 * m)
    if not keep.any():
        return coarse
    k = int(lags[keep][np.argmax(vals[keep])])
    return (b0 + k - start) / sr


def transcript_offset(ref: Optional[dict], src: Optional[dict]) -> Optional[float]:
    """Seconds into `src` of what is at second 0 of `ref`, from three-word runs said in both."""
    def runs(t):
        words = [(edits._norm(w.get("word") or w.get("text") or ""), float(w["start"])) for w in (t or {}).get("words") or []]
        words = [(w, s) for w, s in words if w]
        out: dict[tuple, list[float]] = {}
        for i in range(len(words) - 2):
            out.setdefault(tuple(w for w, _ in words[i:i + 3]), []).append(words[i][1])
        return {k: v[0] for k, v in out.items() if len(v) == 1}    # unique runs only
    a, b = runs(ref), runs(src)
    deltas = [b[k] - a[k] for k in a.keys() & b.keys()]
    if len(deltas) < 3:
        return None
    med = float(np.median(deltas))
    close = [d for d in deltas if abs(d - med) < 0.5]
    return float(np.median(close)) if len(close) >= 3 else None


def speech_band(x: np.ndarray, sr: int = SR, lo: float = 300.0, hi: float = 3400.0) -> np.ndarray:
    """Only the frequencies speech is understood by (rumble and hiss outside them don't count)."""
    if len(x) < 16:
        return x
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / sr)
    spec[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(spec, len(x)).astype(np.float32)


def audio_quality(x: np.ndarray, sr: int = SR) -> dict:
    """Speech level, noise floor, their gap (dB, in the speech band) and clipping; `score` is higher for
    clearer sound."""
    clipped = float((np.abs(x) > 0.98).mean())
    x = speech_band(x, sr)
    n = int(sr * 0.05)
    frames = len(x) // n
    if frames < 10:
        return {"score": -999.0, "level_db": -99.0, "noise_db": -99.0, "snr_db": 0.0, "clipped": 0.0}
    rms = np.sqrt((x[: frames * n].reshape(frames, n) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    level, noise = float(np.percentile(db, 95)), float(np.percentile(db, 10))
    score = (level - noise) - 400 * clipped - (30 if level < -45 else 0)
    return {"score": round(score, 2), "level_db": round(level, 1), "noise_db": round(noise, 1),
            "snr_db": round(level - noise, 1), "clipped": round(clipped, 5)}


# ---------------------------------------------------------------- transcripts

def shift_transcript(t: dict, offset: float, duration: float) -> dict:
    """A transcript moved to group time (source time - offset) and cut to the group."""
    def inside(item):
        return item["end"] - offset > 0 and item["start"] - offset < duration

    def moved(item):
        return {**item, "start": round(max(0.0, item["start"] - offset), 3),
                "end": round(min(duration, item["end"] - offset), 3)}
    words = [moved(w) for w in t.get("words") or [] if inside(w)]
    segments = [moved(s) for s in t.get("segments") or [] if inside(s)]
    return {**t, "duration": round(duration, 3), "words": words, "segments": segments,
            "full_text": " ".join((s.get("text") or "").strip() for s in segments)}


# ---------------------------------------------------------------- build

LABEL_SYSTEM = "You label camera angles for a video editor. Answer only with JSON."
LABEL_PROMPT = """These {n} images are the same moment filmed by {n} cameras, in order.
For each camera give a short label of the framing (for example "wide", "medium", "close-up", "side angle",
"overhead", "screen") and a one-sentence description of what it shows.
Answer as {{"angles": [{{"label": "...", "description": "..."}}, ...]}} in image order."""


def label_angles(paths: list[Path], offsets: list[float], duration: float,
                 vision: Optional[Callable[[str, str, list[bytes]], dict]]) -> list[dict]:
    names = [{"label": f"Camera {i + 1}", "description": ""} for i in range(len(paths))]
    if vision is None:
        return names
    from .analysis import jpeg_frames

    try:
        images = [jpeg_frames(p, [off + duration / 2], width=384)[0] for p, off in zip(paths, offsets)]
        answer = vision(LABEL_SYSTEM, LABEL_PROMPT.format(n=len(paths)), images)
        got = answer.get("angles") or []
        for i, a in enumerate(got[: len(names)]):
            label = str(a.get("label") or "").strip()[:30]
            if label:
                names[i] = {"label": label, "description": str(a.get("description") or "")[:200]}
    except Exception as exc:  # noqa: BLE001 - labels are a nicety; never fail the sync over them
        log.warning("Angle labelling failed: %s", exc)
    return names


def sync(paths: list[Path], transcripts: list[Optional[dict]], progress: Optional[Progress] = None) -> list[dict]:
    """Offset of each recording relative to the first: [{"offset", "method", "confidence"}], first is 0."""
    audio = []
    for i, p in enumerate(paths):
        if progress:
            progress(0.05 + 0.25 * i / len(paths), "Reading the audio of each camera")
        audio.append(load_audio(p))
    ref = audio[0]
    if len(ref) < SR:
        raise MulticamError("The first camera has no usable audio to sync by.")
    out = [{"offset": 0.0, "method": "reference", "confidence": None}]
    for i in range(1, len(paths)):
        if progress:
            progress(0.3 + 0.3 * i / len(paths), "Lining up the cameras")
        if len(audio[i]) < SR:
            raise MulticamError(f"Recording {i + 1} has no usable audio to sync by.")
        guess = transcript_offset(transcripts[0], transcripts[i])
        coarse, conf = envelope_offset(ref, audio[i], guess)
        fine = refine_offset(ref, audio[i], coarse)
        method = "transcript + audio" if guess is not None else "audio"
        if guess is None and conf < 1.15:
            raise MulticamError(f"Couldn't line up recording {i + 1} with the first: their sound doesn't match "
                                "well enough. Are they the same take?")
        out.append({"offset": round(fine, 4), "method": method, "confidence": round(conf, 2),
                    **({"transcript_offset": round(guess, 3)} if guess is not None else {})})
    return out


def create(project_id: str, angle_ids: list[str], audio_id: Optional[str] = None,
           vision: Optional[Callable[[str, str, list[bytes]], dict]] = None,
           progress: Optional[Progress] = None) -> dict:
    """Sync the angles (and optional separate audio) into a new multicam asset; returns it."""
    angle_ids = list(dict.fromkeys(angle_ids))
    if not 2 <= len(angle_ids) <= MAX_ANGLES:
        raise MulticamError(f"Choose 2 to {MAX_ANGLES} camera angles.")
    assets = {a["id"]: a for a in store.list_assets(project_id)}
    for aid in angle_ids:
        a = assets.get(aid)
        if not a or a["status"] != "ready" or a.get("kind") != "video" or a.get("role") != "footage":
            raise MulticamError(f"{aid} isn't a ready footage video in this project.")
        if not a.get("has_audio"):
            raise MulticamError(f"{a.get('filename') or aid} has no sound, so it can't be synced.")
    if audio_id:
        a = assets.get(audio_id)
        if not a or a["status"] != "ready" or a.get("kind") not in ("audio", "video") or audio_id in angle_ids:
            raise MulticamError("The separate audio must be a ready audio file in this project.")

    ids = angle_ids + ([audio_id] if audio_id else [])
    rows = {aid: store.get_asset(aid, with_transcript=True) for aid in ids}
    paths = [storage.path(rows[aid]["storage_key"]) for aid in ids]
    offsets = sync(paths, [rows[aid].get("transcript") for aid in ids], progress)

    # overlap, in the first angle's time: tau with 0 <= tau + off_i <= dur_i for every recording
    durs = [float(rows[aid].get("duration") or 0) for aid in ids]
    start = max(-o["offset"] for o in offsets)
    start = max(start, 0.0)
    end = min(d - o["offset"] for d, o in zip(durs, offsets))
    length = round(end - start, 3)
    if length < MIN_OVERLAP:
        raise MulticamError("The recordings barely overlap once lined up; they don't seem to be the same take.")
    group_off = {aid: round(start + o["offset"], 4) for aid, o in zip(ids, offsets)}

    # audio: the separate recording, or the clearest angle
    if progress:
        progress(0.65, "Choosing the clearest audio")
    quality = {}
    for aid in angle_ids:
        x = load_audio(storage.path(rows[aid]["storage_key"]))
        a0 = int(group_off[aid] * SR)
        quality[aid] = audio_quality(x[a0:a0 + int(length * SR)])
    if audio_id:
        audio_src, reason = audio_id, "the separate audio recording"
    else:
        audio_src = max(angle_ids, key=lambda aid: quality[aid]["score"])
        q = quality[audio_src]
        others = [quality[a]["snr_db"] for a in angle_ids if a != audio_src]
        if others and q["snr_db"] - max(others) < 1.0:
            reason = (f"the cameras sound about the same ({q['snr_db']:.1f} vs {max(others):.1f} dB above the "
                      "background noise); this one is slightly cleaner")
        else:
            reason = (f"clearest sound of the cameras ({q['snr_db']:.0f} dB above the background noise"
                      + (f" vs {max(others):.0f} dB" if others else "") + ")")
        if q["clipped"] > 0.001:
            reason += "; some distortion"

    first = rows[angle_ids[0]]
    name = "Multicam: " + " + ".join((rows[a].get("filename") or a)[:24] for a in angle_ids)
    mc = store.create_asset(project_id, first["user_id"], "multicam", filename=name[:120], transcribe=False,
                            role="footage", status="processing")
    try:
        key = storage.asset_key(first["user_id"], project_id, mc["id"], ".m4a")
        if progress:
            progress(0.72, "Cutting the cameras to the same length")
        subprocess.run([require_ffmpeg(), "-y", "-v", "error", "-ss", f"{group_off[audio_src]:.4f}", "-t", f"{length:.3f}",
                        "-i", str(storage.path(rows[audio_src]["storage_key"])), "-vn", "-ac", "2", "-ar", "48000",
                        "-c:a", "aac", "-b:a", "192k", str(storage.path(key))], check=True, capture_output=True)

        if progress:
            progress(0.8, "Naming the camera angles")
        labels = label_angles([storage.path(rows[a]["storage_key"]) for a in angle_ids],
                              [group_off[a] for a in angle_ids], length, vision)

        transcript, t_status = None, "none"
        src_t = rows[audio_src].get("transcript")
        if not src_t:   # the chosen sound wasn't transcribed: borrow the best-sounding angle that was
            for aid in sorted(angle_ids, key=lambda a: -quality[a]["score"]):
                if rows[aid].get("transcript"):
                    src_t, audio_for_t = rows[aid]["transcript"], aid
                    break
            else:
                audio_for_t = None
        else:
            audio_for_t = audio_src
        if src_t and audio_for_t:
            transcript, t_status = shift_transcript(src_t, group_off[audio_for_t], length), "done"

        analysis = {
            "version": 1, "duration": length,
            "angles": [{"asset_id": a, "offset": group_off[a], **labels[i], "audio": quality[a]}
                       for i, a in enumerate(angle_ids)],
            "audio": {"asset_id": audio_src, "offset": group_off[audio_src], "reason": reason},
            "sync": [{"asset_id": aid, **o} for aid, o in zip(ids, offsets)],
        }
        store.update_asset(mc["id"], kind="multicam", storage_key=key, status="ready", duration=length,
                           width=first.get("width"), height=first.get("height"), fps=first.get("fps"),
                           has_video=1, has_audio=1, analysis=analysis, analysis_status="done",
                           transcript=transcript, transcript_status=t_status)
        if transcript and transcript.get("segments"):
            try:
                import rag_engine  # legacy module; heavy import

                collection = rag_engine.index_transcript(transcript, video_id=f"asset_{mc['id']}")
                store.update_asset(mc["id"], collection_name=collection)
            except Exception as exc:  # noqa: BLE001 - search is optional
                log.warning("Indexing the multicam transcript failed: %s", exc)
    except Exception as exc:
        store.update_asset(mc["id"], status="failed", error=str(exc)[:500])
        raise
    if progress:
        progress(1.0, "Done")
    return store.get_asset(mc["id"])


def layout(asset: dict) -> dict:
    """The angles and audio of a multicam asset (its stored analysis)."""
    data = asset.get("multicam") or store.get_analysis(asset["id"]) or {}
    if isinstance(data, str):
        data = json.loads(data)
    return data


def angle_source(asset: dict, angle: Optional[int]) -> tuple[str, float]:
    """(asset id, offset) of the picture for a clip on this multicam asset."""
    angles = layout(asset).get("angles") or []
    if not angles:
        raise MulticamError(f"Multicam {asset['id']} has no angles")
    a = angles[min(max(angle or 0, 0), len(angles) - 1)]
    return a["asset_id"], float(a["offset"])
