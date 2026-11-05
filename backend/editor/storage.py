"""
Media storage. Local disk today; the same interface will front S3 later, so the
rest of the editor only deals in storage keys, never in hand-built paths.
"""

import re
from pathlib import Path

from .config import settings

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")


def _check(segment: str) -> str:
    if not segment or segment in (".", "..") or not _SAFE_SEGMENT.match(segment):
        raise ValueError(f"Unsafe storage path segment: {segment!r}")
    return segment


def asset_key(user_id: str, project_id: str, asset_id: str, ext: str) -> str:
    ext = ext.lower()
    if ext and not re.fullmatch(r"\.[a-z0-9]{1,8}", ext):
        raise ValueError(f"Bad extension: {ext!r}")
    return f"users/{_check(user_id)}/projects/{_check(project_id)}/assets/{_check(asset_id)}{ext}"


def asset_dir_key(user_id: str, project_id: str) -> str:
    return f"users/{_check(user_id)}/projects/{_check(project_id)}/assets"


def render_key(user_id: str, project_id: str, job_id: str) -> str:
    return f"users/{_check(user_id)}/projects/{_check(project_id)}/renders/{_check(job_id)}.mp4"


def work_dir_key(job_id: str) -> str:
    return f"work/{_check(job_id)}"


def path(key: str) -> Path:
    """Local filesystem path for a key; creates the parent directory."""
    p = settings.storage_root / key
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def dir_path(key: str) -> Path:
    p = settings.storage_root / key
    p.mkdir(parents=True, exist_ok=True)
    return p
