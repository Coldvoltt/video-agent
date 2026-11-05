"""
DB-backed job queue.

Jobs are rows in `editor_jobs`. Workers claim one atomically (BEGIN IMMEDIATE +
UPDATE ... RETURNING), send heartbeats while running, and record the result.
Jobs whose worker died (stale heartbeat) are requeued up to `job_max_attempts`.

Swap for Redis/Arq when more than one worker host is needed; the public
functions here are the whole interface.
"""

import json
from typing import Any, Optional

from . import store
from .config import settings

JSON_FIELDS = ("payload", "result")


def enqueue(kind: str, user_id: str, payload: dict, project_id: str | None = None) -> dict:
    job_id = store.new_id()
    with store.connect() as c:
        c.execute(
            "INSERT INTO editor_jobs (id, kind, user_id, project_id, payload) VALUES (?, ?, ?, ?, ?)",
            (job_id, kind, user_id, project_id, json.dumps(payload)),
        )
    return get(job_id)


def get(job_id: str, user_id: str | None = None) -> Optional[dict]:
    with store.connect() as c:
        if user_id is None:
            row = c.execute("SELECT * FROM editor_jobs WHERE id = ?", (job_id,)).fetchone()
        else:
            row = c.execute(
                "SELECT * FROM editor_jobs WHERE id = ? AND user_id = ?", (job_id, user_id)
            ).fetchone()
    return store._row(row, JSON_FIELDS)


def list_for_project(project_id: str, limit: int = 50) -> list[dict]:
    with store.connect() as c:
        rows = c.execute(
            "SELECT * FROM editor_jobs WHERE project_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
    return [store._row(r, JSON_FIELDS) for r in rows]


def claim(kinds: tuple[str, ...] | None = None) -> Optional[dict]:
    """Atomically take the oldest queued job (optionally of given kinds)."""
    kind_filter = ""
    params: tuple = ()
    if kinds:
        kind_filter = f" AND kind IN ({','.join('?' for _ in kinds)})"
        params = tuple(kinds)
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            f"""UPDATE editor_jobs
                SET status = 'running', attempts = attempts + 1,
                    started_at = CURRENT_TIMESTAMP, heartbeat_at = CURRENT_TIMESTAMP,
                    progress = 0, message = NULL, error = NULL
                WHERE id = (
                    SELECT id FROM editor_jobs WHERE status = 'queued'{kind_filter}
                    ORDER BY created_at, rowid LIMIT 1
                )
                RETURNING *""",
            params,
        ).fetchone()
    return store._row(row, JSON_FIELDS)


def heartbeat(job_id: str, progress: float | None = None, message: str | None = None) -> None:
    with store.connect() as c:
        if progress is None and message is None:
            c.execute(
                "UPDATE editor_jobs SET heartbeat_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'",
                (job_id,),
            )
        else:
            c.execute(
                """UPDATE editor_jobs
                   SET heartbeat_at = CURRENT_TIMESTAMP,
                       progress = COALESCE(?, progress), message = COALESCE(?, message)
                   WHERE id = ? AND status = 'running'""",
                (None if progress is None else max(0.0, min(1.0, progress)), message, job_id),
            )


def complete(job_id: str, result: dict[str, Any]) -> None:
    with store.connect() as c:
        c.execute(
            """UPDATE editor_jobs SET status = 'succeeded', progress = 1, result = ?,
               finished_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (json.dumps(result), job_id),
        )


def fail(job_id: str, error: str) -> None:
    with store.connect() as c:
        c.execute(
            "UPDATE editor_jobs SET status = 'failed', error = ?, finished_at = CURRENT_TIMESTAMP WHERE id = ?",
            (error[-4000:], job_id),
        )


def recover_stale() -> int:
    """Requeue (or fail, after max attempts) running jobs whose worker stopped heartbeating."""
    cutoff = f"-{int(settings.job_stale_seconds)} seconds"
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        failed = c.execute(
            """UPDATE editor_jobs SET status = 'failed', finished_at = CURRENT_TIMESTAMP,
                   error = 'Worker stopped responding; giving up after max attempts'
               WHERE status = 'running' AND heartbeat_at < datetime('now', ?) AND attempts >= ?""",
            (cutoff, settings.job_max_attempts),
        ).rowcount
        requeued = c.execute(
            """UPDATE editor_jobs SET status = 'queued', message = 'Requeued after worker loss'
               WHERE status = 'running' AND heartbeat_at < datetime('now', ?)""",
            (cutoff,),
        ).rowcount
    return failed + requeued


def public_view(job: dict) -> dict:
    """Job as returned by the API (no internal paths)."""
    result = dict(job.get("result") or {})
    result.pop("storage_key", None)
    return {
        "id": job["id"],
        "kind": job["kind"],
        "project_id": job["project_id"],
        "status": job["status"],
        # What the job works on (timeline version / asset / chat message); no secrets live in payloads.
        "input": {k: v for k, v in (job.get("payload") or {}).items()
                  if k in ("timeline_version", "preview", "asset_id", "message_id")},
        "progress": job["progress"],
        "message": job["message"],
        "error": job["error"],
        "result": result or None,
        "attempts": job["attempts"],
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
    }
