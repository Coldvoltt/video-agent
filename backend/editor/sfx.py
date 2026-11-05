"""
Built-in sound effects, synthesised with ffmpeg (no third-party files, no licence terms).

Generated once into the storage cache as 48 kHz stereo WAV and reused. The set covers what
short-form edits use most: whooshes for cuts and cutaways, pops for text, a ding for key
points, a riser before a reveal, a click, and an impact.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import require_ffmpeg, settings


@dataclass(frozen=True)
class Sound:
    id: str
    name: str
    description: str
    duration: float
    source: str          # lavfi graph producing mono audio
    post: str = ""       # extra filters


SOUNDS: dict[str, Sound] = {s.id: s for s in [
    Sound("whoosh", "Whoosh", "Air rush for cuts, cutaways and transitions.", 0.6,
          "anoisesrc=c=pink:a=0.9:d=0.6:r=48000",
          "highpass=f=350,lowpass=f=5200,afade=t=in:d=0.28:curve=qsin,afade=t=out:st=0.3:d=0.3:curve=qsin"),
    Sound("swipe", "Swipe", "Short, quick swish for fast cuts and text sliding in.", 0.28,
          "anoisesrc=c=white:a=0.7:d=0.28:r=48000",
          "highpass=f=1200,lowpass=f=9000,afade=t=in:d=0.08,afade=t=out:st=0.1:d=0.18"),
    Sound("riser", "Riser", "Building tension before a reveal (about 1.5 s).", 1.6,
          "aevalsrc='0.35*sin(2*PI*(180*t+260*t*t))*min(1,t/1.4)':d=1.6:s=48000",
          "afade=t=out:st=1.45:d=0.15"),
    Sound("pop", "Pop", "Bubble pop for text and keyword pops.", 0.14,
          "aevalsrc='0.9*sin(2*PI*(900*t-2600*t*t))*exp(-t*28)':d=0.14:s=48000", ""),
    Sound("ding", "Ding", "Bright bell for a key point or a list item.", 1.3,
          "aevalsrc='(0.55*sin(2*PI*1318*t)+0.25*sin(2*PI*2636*t)+0.12*sin(2*PI*3954*t))*exp(-t*3.5)'"
          ":d=1.3:s=48000", ""),
    Sound("click", "Click", "Crisp click for UI moments and counters.", 0.05,
          "aevalsrc='0.9*sin(2*PI*2400*t)*exp(-t*350)':d=0.05:s=48000", ""),
    Sound("impact", "Impact", "Deep hit for big statements and title slams.", 1.1,
          "aevalsrc='0.9*sin(2*PI*(48+70*exp(-t*9))*t)*exp(-t*4)':d=1.1:s=48000", "lowpass=f=900"),
]}


def path(sound_id: str) -> Path:
    """WAV file for a built-in sound, generated on first use."""
    if sound_id not in SOUNDS:
        raise KeyError(sound_id)
    out = settings.storage_root / "sfx" / f"{sound_id}.wav"
    if out.is_file():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    s = SOUNDS[sound_id]
    chain = ",".join(x for x in (s.post, "alimiter=limit=0.89", "aformat=sample_fmts=s16:channel_layouts=stereo") if x)
    raw = out.with_suffix(".raw.wav")
    tmp = out.with_suffix(".tmp.wav")
    subprocess.run([require_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", s.source, "-af", chain,
                    "-ar", "48000", "-t", f"{s.duration:.3f}", str(raw)], check=True)
    # Same peak (-3 dB) for every sound, so volumes behave the same across effects.
    probe = subprocess.run([require_ffmpeg(), "-hide_banner", "-nostats", "-i", str(raw), "-af", "volumedetect",
                            "-f", "null", "-"], capture_output=True, text=True).stderr
    peak = float(probe.split("max_volume:")[1].split("dB")[0]) if "max_volume:" in probe else -3.0
    subprocess.run([require_ffmpeg(), "-y", "-v", "error", "-i", str(raw), "-af", f"volume={-3.0 - peak:.2f}dB",
                    str(tmp)], check=True)
    raw.unlink(missing_ok=True)
    tmp.replace(out)
    return out


def info(sound_id: str) -> dict:
    s = SOUNDS[sound_id]
    return {"id": s.id, "name": s.name, "description": s.description, "duration": s.duration}
