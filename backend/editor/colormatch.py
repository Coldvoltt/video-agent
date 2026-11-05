"""
"Grade it like the reference": colour transfer baked into a 3D LUT.

Reinhard-style statistics transfer in CIE Lab: shift and scale each channel of the
footage so its mean and spread match the reference, then sample that mapping over an
RGB grid and write it as a .cube file the renderer applies with `lut3d`.
"""

from pathlib import Path

import numpy as np

# sRGB (D65) <-> XYZ
_M = np.array([[0.4124564, 0.3575761, 0.1804375],
               [0.2126729, 0.7151522, 0.0721750],
               [0.0193339, 0.1191920, 0.9503041]])
_M_INV = np.linalg.inv(_M)
_WHITE = np.array([0.95047, 1.0, 1.08883])
_EPS, _KAPPA = 216 / 24389, 24389 / 27


def _to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """(..., 3) sRGB in 0..1 -> Lab (L 0..100)."""
    xyz = _to_linear(np.asarray(rgb, dtype=np.float64)) @ _M.T / _WHITE
    f = np.where(xyz > _EPS, np.cbrt(xyz), (_KAPPA * xyz + 16) / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def lab_to_srgb(lab: np.ndarray) -> np.ndarray:
    lab = np.asarray(lab, dtype=np.float64)
    fy = (lab[..., 0] + 16) / 116
    fx, fz = fy + lab[..., 1] / 500, fy - lab[..., 2] / 200
    f = np.stack([fx, fy, fz], axis=-1)
    xyz = np.where(f ** 3 > _EPS, f ** 3, (116 * f - 16) / _KAPPA) * _WHITE
    return np.clip(_to_srgb(xyz @ _M_INV.T), 0.0, 1.0)


def transfer_lut(src_mean, src_std, ref_mean, ref_std, strength: float = 0.75, size: int = 33) -> np.ndarray:
    """LUT (size, size, size, 3) indexed [b][g][r] mapping footage colours toward the reference look."""
    src_mean, ref_mean = np.asarray(src_mean, float), np.asarray(ref_mean, float)
    # Limit how far spreads are stretched so odd footage can't produce extreme grades.
    scale = np.clip(np.asarray(ref_std, float) / np.maximum(np.asarray(src_std, float), 1e-3), 0.6, 1.6)
    axis = np.linspace(0.0, 1.0, size)
    b, g, r = np.meshgrid(axis, axis, axis, indexing="ij")
    rgb = np.stack([r, g, b], axis=-1)
    lab = srgb_to_lab(rgb)
    moved = (lab - src_mean) * scale + ref_mean
    out = lab_to_srgb(lab + strength * (moved - lab))
    return out


def write_cube(path: Path, lut: np.ndarray, title: str = "Colour match") -> Path:
    path.write_text(cube_text(lut, title), encoding="ascii")
    return path


def cube_text(lut: np.ndarray, title: str = "Colour match") -> str:
    size = lut.shape[0]
    lines = [f'TITLE "{title.replace(chr(34), "")[:60]}"', f"LUT_3D_SIZE {size}",
             "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    flat = lut.reshape(-1, 3)   # [b][g][r] order = red changes fastest, as .cube requires
    lines += [f"{r:.6f} {g:.6f} {bb:.6f}" for r, g, bb in flat]
    return "\n".join(lines) + "\n"


def apply_lut(rgb: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Nearest-neighbour LUT lookup (for tests and previews of the mapping)."""
    size = lut.shape[0]
    idx = np.clip(np.rint(np.asarray(rgb, float) * (size - 1)).astype(int), 0, size - 1)
    return lut[idx[..., 2], idx[..., 1], idx[..., 0]]
