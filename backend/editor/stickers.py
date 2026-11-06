"""
Animated stickers and callouts (Lottie).

Sources, all addressed by a sticker string:
- "circle", "arrow", ...      built-in callouts, generated here as Lottie JSON (colourable, no licence terms)
- "emoji:1f525" / "emoji:🔥"  Noto Animated Emoji by Google (CC BY 4.0, attribution shown in the app),
                               fetched once from Google's font CDN and cached
- "asset:<id>"                a Lottie .json the user uploaded

Rendering: rlottie (via rlottie-python, LGPL, used unmodified) draws transparent frames that
the renderer overlays with ffmpeg. The browser's live preview plays the same JSON with lottie-web.
"""

import io
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

import httpx

from .config import settings

NOTO_URL = "https://fonts.gstatic.com/s/e/notoemoji/latest/{code}/lottie.json"
NOTO_CREDIT = "Animated emoji: Noto Animated Emoji by Google, CC BY 4.0"
FPS = 30


class StickerError(RuntimeError):
    pass


# ---------------------------------------------------------------- built-in callouts (generated Lottie)

def _rgb(hex_color: str) -> list[float]:
    h = hex_color.lstrip("#")
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)] + [1]


def _static(v):
    return {"a": 0, "k": v}


def _ease(frames: list[tuple[int, list[float]]]):
    """Animated property with smooth ease-out between keyframes."""
    keys = []
    for i, (t, v) in enumerate(frames):
        k = {"t": t, "s": v}
        if i < len(frames) - 1:
            k["o"] = {"x": [0.3] * len(v), "y": [0] * len(v)}
            k["i"] = {"x": [0.2] * len(v), "y": [1] * len(v)}
        keys.append(k)
    return {"a": 1, "k": keys}


def _path(points: list[tuple[float, float]], closed: bool = False) -> dict:
    zero = [[0, 0] for _ in points]
    return {"ty": "sh", "ks": _static({"i": zero, "o": zero, "v": [list(p) for p in points], "c": closed})}


def _stroke_group(shapes: list[dict], color: str, width: float, draw_frames: int, delay: int = 0) -> dict:
    """Shapes drawn on with a trim path (the 'being drawn' look of hand-made callouts)."""
    return {"ty": "gr", "it": [
        *shapes,
        {"ty": "tm", "s": _static(0), "e": _ease([(delay, [0]), (delay + draw_frames, [100])]), "o": _static(0), "m": 1},
        {"ty": "st", "c": _static(_rgb(color)), "o": _static(100), "w": _static(width), "lc": 2, "lj": 2},
        {"ty": "tr", "p": _static([0, 0]), "a": _static([0, 0]), "s": _static([100, 100]), "r": _static(0), "o": _static(100)},
    ]}


def _layer(groups: list[dict], frames: int, rotation: float = 0, position=None, scale=None) -> dict:
    return {
        "ddd": 0, "ind": 1, "ty": 4, "nm": "callout", "sr": 1, "ao": 0, "ip": 0, "op": frames, "st": 0, "bm": 0,
        "ks": {"o": _static(100), "r": _static(rotation), "a": _static([0, 0, 0]),
               "p": position or _static([256, 256, 0]), "s": scale or _static([100, 100, 100])},
        "shapes": groups,
    }


def _doc(name: str, layers: list[dict], frames: int) -> dict:
    return {"v": "5.7.0", "fr": FPS, "ip": 0, "op": frames, "w": 512, "h": 512, "nm": name, "ddd": 0,
            "assets": [], "layers": layers}


def _callout(kind: str, color: str) -> dict:
    W = 26      # stroke width on the 512 canvas
    if kind == "circle":
        frames = 24
        ellipse = {"ty": "el", "p": _static([0, 0]), "s": _static([400, 300]), "d": 1}
        return _doc(kind, [_layer([_stroke_group([ellipse], color, W, 14)], frames, rotation=-20)], frames)
    if kind == "underline":
        frames = 18
        wave = _path([(-200, 10), (-100, -6), (0, 8), (100, -6), (200, 6)])
        return _doc(kind, [_layer([_stroke_group([wave], color, W, 10)], frames)], frames)
    if kind == "box":
        frames = 22
        rect = {"ty": "rc", "p": _static([0, 0]), "s": _static([420, 260]), "r": _static(36), "d": 1}
        return _doc(kind, [_layer([_stroke_group([rect], color, W, 14)], frames)], frames)
    if kind == "check":
        frames = 18
        tick = _path([(-150, 10), (-50, 110), (160, -120)])
        return _doc(kind, [_layer([_stroke_group([tick], color, W * 1.5, 12)], frames)], frames)
    if kind == "cross":
        frames = 20
        a = _stroke_group([_path([(-130, -130), (130, 130)])], color, W * 1.5, 8)
        b = _stroke_group([_path([(130, -130), (-130, 130)])], color, W * 1.5, 8, delay=7)
        return _doc(kind, [_layer([a, b], frames)], frames)
    if kind == "sparkle":
        frames = 20
        rays = []
        for i in range(8):
            import math
            ang = i * math.pi / 4
            r1, r2 = (70, 200) if i % 2 == 0 else (90, 160)
            rays.append(_stroke_group([_path([(r1 * math.cos(ang), r1 * math.sin(ang)),
                                              (r2 * math.cos(ang), r2 * math.sin(ang))])], color, W * 0.8, 8))
        pop = _ease([(0, [40, 40, 100]), (8, [112, 112, 100]), (14, [100, 100, 100])])
        return _doc(kind, [_layer(rays, frames, scale=pop)], frames)
    if kind == "arrow":
        frames = 45          # draw, then a few nudges toward the target
        shaft = _path([(-190, 0), (150, 0)])
        head = _path([(70, -90), (170, 0), (70, 90)])
        group = _stroke_group([shaft, head], color, W * 1.2, 10)
        nudge = _ease([(10, [256, 256, 0]), (18, [286, 256, 0]), (26, [256, 256, 0]), (34, [286, 256, 0]),
                       (42, [256, 256, 0])])
        return _doc(kind, [_layer([group], frames, position=nudge)], frames)
    raise StickerError(f"Unknown callout {kind!r}")


CALLOUTS = {
    "circle": "Hand-drawn circle around something",
    "underline": "Wavy underline drawn under a word or object",
    "box": "Rounded highlight box drawn around something",
    "arrow": "Arrow that draws on and nudges toward a target (rotate to point any way)",
    "check": "Tick mark drawn on (yes, correct, done)",
    "cross": "X drawn on (no, wrong, avoid)",
    "sparkle": "Burst of rays popping out (wow, new, highlight)",
}
DEFAULT_COLOR = "#FFD23F"


# ---------------------------------------------------------------- emoji

def emoji_code(value: str) -> str:
    """'🔥', '1f525', 'U+1F525' or '1F44D 1F3FB' -> Noto file code like '1f525' / '1f44d_1f3fb'."""
    v = value.strip()
    if re.fullmatch(r"(?i)(u\+)?[0-9a-f]{4,6}([ _-](u\+)?[0-9a-f]{4,6})*", v):
        parts = re.split(r"[ _-]", v.lower().replace("u+", ""))
    else:
        if not v or any(ch.isascii() for ch in v):       # words like "fire" are not emoji
            raise StickerError("Not an emoji")
        parts = [f"{ord(ch):x}" for ch in v]
    if not parts or len(parts) > 8:
        raise StickerError("Not an emoji")
    return "_".join(parts)


def _emoji_json(code: str, client: Optional[httpx.Client] = None) -> bytes:
    cache = settings.storage_root / "stickers" / "emoji"
    for candidate in dict.fromkeys([code, code.replace("_fe0f", ""), f"{code}_fe0f"]):
        path = cache / f"{candidate}.json"
        if path.is_file():
            return path.read_bytes()
        if (cache / f"{candidate}.missing").is_file():
            continue
        own = client is None
        c = client or httpx.Client(timeout=20)
        try:
            r = c.get(NOTO_URL.format(code=candidate))
        except httpx.HTTPError as exc:
            raise StickerError(f"Could not fetch the animated emoji: {exc}") from exc
        finally:
            if own:
                c.close()
        cache.mkdir(parents=True, exist_ok=True)
        if r.status_code == 200 and r.content.lstrip()[:1] == b"{":
            path.write_bytes(r.content)
            return r.content
        (cache / f"{candidate}.missing").write_text("")
    raise StickerError(f"No animated version of that emoji ({code})")


# ---------------------------------------------------------------- user uploads

def inspect_lottie(path: Path) -> dict:
    """Validate an uploaded Lottie JSON and return asset info."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise StickerError("Not a Lottie animation (invalid JSON)") from exc
    if not isinstance(doc, dict) or not all(k in doc for k in ("fr", "ip", "op", "w", "h", "layers")):
        raise StickerError("Not a Lottie animation (missing fr/ip/op/w/h/layers)")
    fr = float(doc["fr"]) or 30
    return {"kind": "lottie", "duration": round((float(doc["op"]) - float(doc["ip"])) / fr, 3),
            "width": int(doc["w"]), "height": int(doc["h"]), "fps": fr, "has_video": True, "has_audio": False}


# ---------------------------------------------------------------- resolve + render

def resolve(sticker: str, color: Optional[str] = None, asset_paths: Optional[dict[str, Path]] = None,
            client: Optional[httpx.Client] = None) -> bytes:
    """Lottie JSON bytes for a sticker string."""
    if sticker.startswith("emoji:"):
        return _emoji_json(emoji_code(sticker[6:]), client)
    if sticker.startswith("asset:"):
        aid = sticker[6:]
        if not asset_paths or aid not in asset_paths:
            raise StickerError(f"Animation asset {aid} not available")
        return Path(asset_paths[aid]).read_bytes()
    if sticker in CALLOUTS:
        return json.dumps(_callout(sticker, color or DEFAULT_COLOR)).encode()
    raise StickerError(f"Unknown sticker {sticker!r}")


def default_loop(sticker: str) -> bool:
    """Emoji and uploaded animations loop; callouts draw once and stay."""
    return not (sticker in CALLOUTS)


@lru_cache(maxsize=32)
def _animation(data: bytes):
    from rlottie_python import LottieAnimation

    anim = LottieAnimation.from_data(data=data.decode("utf-8"))
    return anim, anim.lottie_animation_get_totalframe(), anim.lottie_animation_get_framerate() or FPS, \
        anim.lottie_animation_get_size()


def render_frames(data: bytes, duration: float, fps: float, width_px: int, loop: bool,
                  rotation: float = 0.0) -> list[bytes]:
    """PNG frames (RGBA) covering `duration` seconds at `fps`."""
    from PIL import Image

    anim, total, lfps, (lw, lh) = _animation(data)
    if total <= 0:
        raise StickerError("The animation has no frames")
    w = max(8, int(width_px))
    h = max(8, int(round(w * lh / lw))) if lw else w
    frames, cache = [], {}
    for k in range(max(1, int(round(duration * fps)))):
        idx = int((k / fps) * lfps)
        idx = idx % total if loop else min(idx, total - 1)
        if idx not in cache:
            img = anim.render_pillow_frame(frame_num=idx, width=w, height=h)
            if rotation:
                img = img.rotate(-rotation, expand=True, resample=Image.BICUBIC)
            buf = io.BytesIO()
            img.save(buf, format="PNG", compress_level=1)
            cache[idx] = buf.getvalue()
        frames.append(cache[idx])
    return frames
