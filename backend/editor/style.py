"""
Style profiles of reference videos ("make mine like this one").

A profile combines measurements (pacing from cut detection, colour statistics,
loudness, tempo, speaking pace) with a vision model's read of sample frames
(caption look, on-screen text, framing, transitions, energy), and derives
suggestions the agent can apply with its existing tools.
"""

import logging
from pathlib import Path
from typing import Callable, Optional

from . import analysis
from .edits import ASPECT_PRESETS

log = logging.getLogger("editor.style")

ANALYSIS_LIMIT_SECONDS = 600     # analyse at most the first 10 minutes
VISION_FRAMES = 6

VISION_SYSTEM = "You are a video editor describing another creator's editing style from still frames. Answer in JSON."
VISION_PROMPT = """These are frames sampled evenly from one video. Describe its editing style as JSON with exactly these keys:
{
  "captions": {"present": bool, "position": "top"|"middle"|"bottom"|null, "uppercase": bool,
               "words_per_line": int|null, "text_color": "#RRGGBB"|null, "highlight_color": "#RRGGBB"|null,
               "boxed": bool, "bold": bool},
  "on_screen_text": "none"|"occasional_titles"|"frequent",
  "framing": "talking_head"|"b_roll"|"mixed"|"screen_recording"|"other",
  "punch_in_zooms": bool,
  "transitions": "hard_cuts"|"soft_fades"|"mixed"|"flashy",
  "look": "a few words on colour and lighting",
  "energy": "calm"|"moderate"|"high",
  "summary": "one sentence describing the editing style"
}
Captions means burned-in subtitles of speech, not logos or titles. A highlight_color is a different colour on the
currently spoken word. punch_in_zooms means the same shot appears at different zoom levels. Use null when unsure."""

Progress = Callable[[float, str], None]


def nearest_aspect(width: Optional[int], height: Optional[int]) -> Optional[str]:
    if not width or not height:
        return None
    ratio = width / height
    return min(ASPECT_PRESETS, key=lambda k: abs(ASPECT_PRESETS[k][0] / ASPECT_PRESETS[k][1] - ratio))


def caption_style_for(visual: Optional[dict]) -> Optional[dict]:
    """Map the vision model's caption description onto our caption settings."""
    cap = (visual or {}).get("captions") or {}
    if not cap.get("present"):
        return None
    words = cap.get("words_per_line")
    if cap.get("boxed"):
        style = "boxed"
    elif cap.get("highlight_color"):
        style = "karaoke"
    elif words is not None and words <= 2:
        style = "pop"
    else:
        style = "karaoke" if cap.get("bold") else "minimal"
    out: dict = {"style": style}
    if cap.get("position") in ("top", "middle", "bottom"):
        out["position"] = cap["position"]
    if cap.get("uppercase") is not None:
        out["uppercase"] = bool(cap["uppercase"])
    if isinstance(words, int) and 1 <= words <= 10:
        out["max_words"] = words
    for key in ("text_color", "highlight_color"):
        val = cap.get(key)
        if isinstance(val, str) and len(val) == 7 and val.startswith("#"):
            out["color" if key == "text_color" else "highlight_color"] = val.upper()
    return out


def transition_for(visual: Optional[dict]) -> Optional[dict]:
    kind = (visual or {}).get("transitions")
    if kind == "soft_fades":
        return {"type": "fade", "duration": 0.4}
    if kind == "flashy":
        return {"type": "slideleft", "duration": 0.3}
    if kind == "mixed":
        return {"type": "dissolve", "duration": 0.3}
    return None   # hard cuts: no transitions


def build_profile(path: Path, info: dict, transcript: Optional[dict] = None,
                  vision: Optional[Callable[[list[bytes]], dict]] = None,
                  progress: Optional[Progress] = None) -> dict:
    """Analyse a reference video. `vision` gets JPEG frames and returns the JSON described above."""
    def step(f: float, msg: str) -> None:
        if progress:
            progress(f, msg)

    duration = float(info["duration"])
    span = min(duration, ANALYSIS_LIMIT_SECONDS)

    step(0.1, "Finding cuts")
    cuts = analysis.detect_cuts(path, span)
    pacing = analysis.pacing_stats(cuts, span)

    step(0.35, "Measuring colour")
    frames = analysis.sample_frames(path, duration, 24, src_size=(info.get("width"), info.get("height")),
                                    limit_seconds=span)
    color = analysis.color_stats(frames)

    audio: dict = {}
    if info.get("has_audio"):
        step(0.5, "Listening to the audio")
        audio["mean_volume_db"] = analysis.loudness(path, span)
        tempo = analysis.estimate_tempo(path, span)
        audio["tempo_bpm"] = tempo["bpm"]
        audio["beat_confidence"] = tempo["confidence"]
    words = (transcript or {}).get("words") or []
    if words:
        in_span = [w for w in words if w["start"] < span]
        audio["speech_wpm"] = round(len(in_span) / span * 60) if span else None
        audio["speech_coverage"] = round(sum(w["end"] - w["start"] for w in in_span) / span, 2) if span else None

    visual, visual_error = None, None
    if vision:
        step(0.65, "Looking at frames")
        times = [span * (i + 0.5) / VISION_FRAMES for i in range(VISION_FRAMES)]
        images = analysis.jpeg_frames(path, times)
        try:
            visual = vision(images) if images else None
        except Exception as exc:   # the measurements are still useful without it
            visual_error = str(exc)[:300]
            log.warning("vision analysis failed: %s", exc)

    aspect = nearest_aspect(info.get("width"), info.get("height"))
    suggestions = {
        "aspect": aspect,
        "target_shot_seconds": pacing["median_shot_seconds"] if pacing["cuts"] >= 3 else None,
        "punch_ins": bool((visual or {}).get("punch_in_zooms")),
        "captions": caption_style_for(visual),
        "transition": transition_for(visual),
        "color_match": True,
    }
    step(0.95, "Writing the style profile")
    return {
        "version": 1,
        "analyzed_seconds": round(span, 2),
        "format": {"width": info.get("width"), "height": info.get("height"), "aspect": aspect},
        "pacing": {**pacing, "cut_times": cuts[:200]},
        "color": color,
        "audio": audio,
        "visual": visual,
        **({"visual_error": visual_error} if visual_error else {}),
        "suggestions": suggestions,
    }


def summarize(profile: dict) -> str:
    """One short plain-language line for the UI and the agent."""
    parts = []
    p = profile.get("pacing", {})
    if p.get("cuts"):
        parts.append(f"a cut every {p['median_shot_seconds']:.1f}s")
    else:
        parts.append("one continuous shot")
    s = profile.get("suggestions", {})
    if s.get("aspect"):
        parts.append(s["aspect"])
    cap = s.get("captions")
    if cap:
        parts.append(f"{cap['style']} captions")
    if profile.get("audio", {}).get("tempo_bpm"):
        parts.append(f"music around {profile['audio']['tempo_bpm']:.0f} BPM")
    look = (profile.get("visual") or {}).get("look")
    if look:
        parts.append(look)
    return ", ".join(parts)
