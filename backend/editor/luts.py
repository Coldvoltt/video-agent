"""
Built-in colour looks as 3D LUTs.

Every preset is generated from a few grading operations (contrast curve, lift/gain,
saturation, split toning, fade, mono mix), so the library is reproducible, needs no
third-party files and carries no licence terms. Files are written once to the storage
cache as standard 33-point .cube LUTs, which other editors can use too.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from . import colormatch
from .config import settings

SIZE = 33
_LUMA = np.array([0.2126, 0.7152, 0.0722])


# ---------------------------------------------------------------- grading operations (RGB in 0..1)

def _luma(rgb):
    return (rgb @ _LUMA)[..., None]


def s_curve(rgb, amount):
    """Contrast around mid-grey with a smooth S (amount 0 = none, ~0.3 = strong)."""
    x = np.clip(rgb, 0, 1)
    curved = x - amount * np.sin(2 * np.pi * x) / (2 * np.pi)
    return curved


def saturation(rgb, amount):
    y = _luma(rgb)
    return y + (rgb - y) * amount


def lift_gain(rgb, lift=(0, 0, 0), gain=(1, 1, 1)):
    lift, gain = np.asarray(lift, float), np.asarray(gain, float)
    return lift + rgb * (gain - lift)


def split_tone(rgb, shadows=(0, 0, 0), highlights=(0, 0, 0), balance=0.5):
    """Tint shadows and highlights separately (tints are small RGB offsets)."""
    y = _luma(rgb)
    w_hi = np.clip((y - balance) * 2 + 0.5, 0, 1)
    return rgb + (1 - w_hi) * np.asarray(shadows) + w_hi * np.asarray(highlights)


def fade(rgb, black=0.06, white=0.97):
    return black + rgb * (white - black)


def mono(rgb, mix=(0.30, 0.59, 0.11)):
    y = rgb @ np.asarray(mix)
    return np.repeat(y[..., None], 3, axis=-1)


def skin_protect(original, graded, strength=0.6):
    """Pull orange/skin hues back toward the original so faces stay natural."""
    r, g, b = original[..., 0], original[..., 1], original[..., 2]
    skin = np.clip(1 - np.abs((r - g) - 0.12) * 6, 0, 1) * np.clip((g - b) * 5, 0, 1) * (r > g)
    w = (skin * strength)[..., None]
    return graded * (1 - w) + original * w


# ---------------------------------------------------------------- presets

@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    description: str
    css: str                      # rough CSS filter for the in-browser live preview
    build: Callable[[np.ndarray], np.ndarray]


def _teal_orange(x):
    y = split_tone(s_curve(x, 0.18), shadows=(-0.06, 0.02, 0.07), highlights=(0.07, 0.02, -0.07))
    return skin_protect(x, saturation(y, 1.12), 0.4)


def _warm_film(x):
    y = fade(s_curve(x, 0.10), 0.05, 0.97)
    y = split_tone(y, shadows=(0.02, 0.0, -0.02), highlights=(0.05, 0.02, -0.05))
    return saturation(y, 1.05)


def _cool_cinema(x):
    y = s_curve(x, 0.16)
    y = split_tone(y, shadows=(-0.04, 0.0, 0.06), highlights=(-0.01, 0.01, 0.03))
    return saturation(y, 0.85)


def _golden_hour(x):
    y = lift_gain(x, lift=(0.03, 0.015, 0.0), gain=(1.05, 0.98, 0.86))
    y = split_tone(y, highlights=(0.06, 0.03, -0.04))
    return saturation(s_curve(y, 0.08), 1.12)


def _bleach_bypass(x):
    y = s_curve(x, 0.32)
    return saturation(y, 0.45) * 0.98 + 0.01


def _moody_green(x):
    y = s_curve(x, 0.14) * 0.94
    y = split_tone(y, shadows=(-0.03, 0.03, 0.01), highlights=(0.02, 0.02, -0.02))
    return saturation(y, 0.75)


def _vintage_fade(x):
    y = fade(x, 0.09, 0.93)
    y = split_tone(y, shadows=(0.0, 0.02, 0.04), highlights=(0.05, 0.04, -0.04))
    return saturation(y, 0.72)


def _soft_pastel(x):
    y = fade(s_curve(x, -0.10), 0.07, 1.0)
    y = split_tone(y, shadows=(0.02, 0.0, 0.03), highlights=(0.02, 0.01, 0.0))
    return saturation(y, 0.85)


def _noir(x):
    return s_curve(mono(x, (0.35, 0.55, 0.10)), 0.30)


def _clean_bright(x):
    y = lift_gain(x, lift=(0.01, 0.01, 0.01), gain=(1.06, 1.06, 1.06))
    return saturation(s_curve(y, 0.06), 1.1)


def _neon_night(x):
    y = s_curve(x, 0.20)
    y = split_tone(y, shadows=(0.03, -0.03, 0.08), highlights=(0.06, -0.02, 0.05), balance=0.45)
    return saturation(y, 1.35)


def _rosy(x):
    y = fade(x, 0.03, 0.99)
    y = split_tone(y, shadows=(0.02, 0.0, 0.02), highlights=(0.05, 0.0, 0.03))
    return saturation(s_curve(y, 0.05), 1.05)


PRESETS: dict[str, Preset] = {p.id: p for p in [
    Preset("teal_orange", "Teal & Orange", "Blockbuster look: teal shadows, warm skin and highlights, punchy contrast.",
           "contrast(1.12) saturate(1.15) sepia(0.12) hue-rotate(-6deg)", _teal_orange),
    Preset("warm_film", "Warm Film", "Soft film stock: gentle contrast, lifted blacks, warm midtones.",
           "contrast(0.97) sepia(0.18) saturate(1.05) brightness(1.02)", _warm_film),
    Preset("cool_cinema", "Cool Cinema", "Cool blue shadows, restrained colour, firm contrast.",
           "contrast(1.1) saturate(0.85) hue-rotate(8deg)", _cool_cinema),
    Preset("golden_hour", "Golden Hour", "Late-afternoon sun: warm, glowing highlights.",
           "sepia(0.3) saturate(1.2) brightness(1.03)", _golden_hour),
    Preset("bleach_bypass", "Bleach Bypass", "Gritty, silvery, high contrast with muted colour.",
           "contrast(1.3) saturate(0.45)", _bleach_bypass),
    Preset("moody_green", "Moody Green", "Dark and cool with a green-teal cast, muted colour.",
           "contrast(1.1) saturate(0.75) brightness(0.94) hue-rotate(12deg)", _moody_green),
    Preset("vintage_fade", "Vintage Fade", "Faded blacks, creamy highlights, washed-out colour.",
           "contrast(0.85) saturate(0.72) sepia(0.2) brightness(1.03)", _vintage_fade),
    Preset("soft_pastel", "Soft Pastel", "Low contrast, airy and light, soft colour.",
           "contrast(0.85) saturate(0.85) brightness(1.06)", _soft_pastel),
    Preset("noir", "Noir", "High-contrast black and white.",
           "grayscale(1) contrast(1.3)", _noir),
    Preset("clean_bright", "Clean & Bright", "Brighter and crisper with livelier colour; stays natural.",
           "brightness(1.06) saturate(1.1) contrast(1.04)", _clean_bright),
    Preset("neon_night", "Neon Night", "Night-life colour: magenta and cyan, very saturated.",
           "contrast(1.12) saturate(1.35) hue-rotate(-12deg)", _neon_night),
    Preset("rosy", "Rosy", "Soft pink highlights for lifestyle and beauty content.",
           "sepia(0.08) saturate(1.05) hue-rotate(-8deg) brightness(1.02)", _rosy),
]}


# ---------------------------------------------------------------- LUT files

def identity(size: int = SIZE) -> np.ndarray:
    axis = np.linspace(0.0, 1.0, size)
    b, g, r = np.meshgrid(axis, axis, axis, indexing="ij")
    return np.stack([r, g, b], axis=-1)            # [b][g][r] -> rgb, the .cube order


def build(preset_id: str, size: int = SIZE) -> np.ndarray:
    return np.clip(PRESETS[preset_id].build(identity(size)), 0.0, 1.0)


def preset_path(preset_id: str) -> Path:
    """Path of the preset's .cube file, generated on first use."""
    if preset_id not in PRESETS:
        raise KeyError(preset_id)
    path = settings.storage_root / "luts" / f"{preset_id}.cube"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        colormatch.write_cube(tmp, build(preset_id), title=PRESETS[preset_id].name)
        tmp.replace(path)
    return path


def read_cube(path: Path) -> np.ndarray:
    """Parse a 3D .cube file into a [b][g][r] -> rgb array (domain normalised to 0..1)."""
    size, rows, lo, hi = None, [], np.zeros(3), np.ones(3)
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        head, *rest = line.split()
        key = head.upper()
        if key == "LUT_3D_SIZE":
            size = int(rest[0])
        elif key == "DOMAIN_MIN":
            lo = np.array([float(v) for v in rest[:3]])
        elif key == "DOMAIN_MAX":
            hi = np.array([float(v) for v in rest[:3]])
        elif key in ("TITLE", "LUT_3D_INPUT_RANGE"):
            continue
        else:
            rows.append([float(head), *map(float, rest[:2])])
    lut = np.asarray(rows, float).reshape(size, size, size, 3)
    return (lut - lo) / np.where(hi - lo == 0, 1, hi - lo)


def blend_with_identity(lut: np.ndarray, strength: float) -> np.ndarray:
    """Partial-strength look: mix the LUT with 'no change'."""
    strength = float(np.clip(strength, 0.0, 1.0))
    return identity(lut.shape[0]) * (1 - strength) + lut * strength


def apply(rgb: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Trilinear LUT lookup for images (uint8 or float RGB) -> float 0..1."""
    x = np.asarray(rgb, float)
    if x.max() > 1.0:
        x = x / 255.0
    n = lut.shape[0] - 1
    p = np.clip(x, 0, 1) * n
    i0 = np.floor(p).astype(int)
    i1 = np.minimum(i0 + 1, n)
    f = p - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    r1, g1, b1 = i1[..., 0], i1[..., 1], i1[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]

    def at(b, g, r):
        return lut[b, g, r]

    c00 = at(b0, g0, r0) * (1 - fr) + at(b0, g0, r1) * fr
    c01 = at(b0, g1, r0) * (1 - fr) + at(b0, g1, r1) * fr
    c10 = at(b1, g0, r0) * (1 - fr) + at(b1, g0, r1) * fr
    c11 = at(b1, g1, r0) * (1 - fr) + at(b1, g1, r1) * fr
    c0 = c00 * (1 - fg) + c01 * fg
    c1 = c10 * (1 - fg) + c11 * fg
    return np.clip(c0 * (1 - fb) + c1 * fb, 0, 1)
