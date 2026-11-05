"""
Editor persistence: projects, assets, timeline versions and jobs.

Lives in the same SQLite file as the legacy app (tables are prefixed `editor_`).
WAL mode lets the API process and the worker process share it safely.
"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS editor_projects (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS editor_assets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES editor_projects(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    kind TEXT,                       -- video | audio | image (known after probing)
    source TEXT NOT NULL,            -- upload | youtube
    source_url TEXT,
    filename TEXT,                   -- original name / video title, display only
    storage_key TEXT,
    status TEXT NOT NULL,            -- pending | processing | ready | failed
    error TEXT,
    duration REAL,
    width INTEGER,
    height INTEGER,
    fps REAL,
    has_video INTEGER,
    has_audio INTEGER,
    transcript_status TEXT NOT NULL DEFAULT 'none',   -- none | pending | done | skipped | failed
    transcript_error TEXT,
    transcript TEXT,                 -- JSON: segments, words, full_text, language, duration
    collection_name TEXT,            -- RAG collection for this asset
    role TEXT NOT NULL DEFAULT 'footage',   -- footage | reference | generated
    analysis_status TEXT NOT NULL DEFAULT 'none',   -- none | pending | done | failed
    analysis TEXT,                   -- JSON style profile (references)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS editor_timelines (
    project_id TEXT NOT NULL REFERENCES editor_projects(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    data TEXT NOT NULL,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, version)
);

CREATE TABLE IF NOT EXISTS editor_jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,              -- ingest | render
    user_id TEXT NOT NULL,
    project_id TEXT,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',   -- queued | running | succeeded | failed
    progress REAL NOT NULL DEFAULT 0,
    message TEXT,
    result TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    heartbeat_at TIMESTAMP,
    finished_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS editor_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES editor_projects(id) ON DELETE CASCADE,
    role TEXT NOT NULL,              -- user | assistant
    content TEXT NOT NULL,
    job_id TEXT,                     -- agent job that answers / answered this turn
    timeline_version INTEGER,        -- version the assistant produced, if any
    meta TEXT,                       -- JSON: tool trace summary, usage, preview job id
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_editor_messages_project ON editor_messages(project_id, id);
CREATE INDEX IF NOT EXISTS idx_editor_projects_user ON editor_projects(user_id);
CREATE INDEX IF NOT EXISTS idx_editor_assets_project ON editor_assets(project_id);
CREATE INDEX IF NOT EXISTS idx_editor_jobs_status ON editor_jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_editor_jobs_project ON editor_jobs(project_id);
"""

_initialized_for: Optional[str] = None


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# Columns added after the first release; existing databases get them on startup.
_ADDED_COLUMNS = {
    "editor_assets": [
        ("role", "TEXT NOT NULL DEFAULT 'footage'"),
        ("analysis_status", "TEXT NOT NULL DEFAULT 'none'"),
        ("analysis", "TEXT"),
        ("proxy_key", "TEXT"),       # small H.264 copy for in-browser preview
    ],
    "editor_projects": [
        ("history", "TEXT"),         # JSON {"undo": [versions], "redo": [versions]}
    ],
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    conn.commit()


def _connect() -> sqlite3.Connection:
    global _initialized_for
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    if _initialized_for != str(settings.db_path):
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
        _initialized_for = str(settings.db_path)
    return conn


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row(row: Optional[sqlite3.Row], json_fields: tuple[str, ...] = ()) -> Optional[dict]:
    if row is None:
        return None
    d = dict(row)
    for f in json_fields:
        if d.get(f) is not None:
            d[f] = json.loads(d[f])
    return d


# ---------------------------------------------------------------- projects

def create_project(user_id: str, name: str) -> dict:
    pid = new_id()
    with connect() as c:
        c.execute("INSERT INTO editor_projects (id, user_id, name) VALUES (?, ?, ?)", (pid, user_id, name))
    return get_project(pid, user_id)


def get_project(project_id: str, user_id: str) -> Optional[dict]:
    with connect() as c:
        row = c.execute(
            "SELECT * FROM editor_projects WHERE id = ? AND user_id = ?", (project_id, user_id)
        ).fetchone()
    return _row(row)


def list_projects(user_id: str) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM editor_projects WHERE user_id = ? ORDER BY created_at DESC, rowid DESC", (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def delete_project(project_id: str, user_id: str) -> bool:
    with connect() as c:
        cur = c.execute("DELETE FROM editor_projects WHERE id = ? AND user_id = ?", (project_id, user_id))
    return cur.rowcount > 0


# ---------------------------------------------------------------- assets

ASSET_SUMMARY_COLUMNS = (
    "id, project_id, user_id, kind, source, source_url, filename, status, error, duration, width, height, "
    "fps, has_video, has_audio, transcript_status, transcript_error, role, analysis_status, created_at"
)


def create_asset(project_id: str, user_id: str, source: str, *, asset_id: str | None = None,
                 filename: str | None = None, source_url: str | None = None,
                 storage_key: str | None = None, transcribe: bool = True, role: str = "footage",
                 status: str = "pending") -> dict:
    aid = asset_id or new_id()
    with connect() as c:
        c.execute(
            """INSERT INTO editor_assets
               (id, project_id, user_id, source, source_url, filename, storage_key, status, transcript_status,
                role, analysis_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (aid, project_id, user_id, source, source_url, filename, storage_key, status,
             "pending" if transcribe else "none", role, "pending" if role in ("reference", "broll") else "none"),
        )
    return get_asset(aid)


def get_asset(asset_id: str, *, with_transcript: bool = False) -> Optional[dict]:
    cols = "*" if with_transcript else ASSET_SUMMARY_COLUMNS + ", storage_key, collection_name, proxy_key"
    with connect() as c:
        row = c.execute(f"SELECT {cols} FROM editor_assets WHERE id = ?", (asset_id,)).fetchone()
    return _row(row, ("transcript", "analysis") if with_transcript else ())


def get_analysis(asset_id: str) -> Optional[dict]:
    with connect() as c:
        row = c.execute("SELECT analysis FROM editor_assets WHERE id = ?", (asset_id,)).fetchone()
    return json.loads(row[0]) if row and row[0] else None


def list_assets(project_id: str) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            f"SELECT {ASSET_SUMMARY_COLUMNS}, storage_key, collection_name, proxy_key FROM editor_assets "
            "WHERE project_id = ? ORDER BY created_at, rowid",
            (project_id,),
        ).fetchall()
    return [dict(r) for r in rows]


_ASSET_UPDATABLE = {
    "kind", "filename", "storage_key", "status", "error", "duration", "width", "height", "fps",
    "has_video", "has_audio", "transcript_status", "transcript_error", "transcript", "collection_name",
    "analysis_status", "analysis", "proxy_key",
}


def update_asset(asset_id: str, **fields: Any) -> None:
    unknown = set(fields) - _ASSET_UPDATABLE
    if unknown:
        raise ValueError(f"Unknown asset fields: {unknown}")
    for key in ("transcript", "analysis"):
        if key in fields and fields[key] is not None:
            fields[key] = json.dumps(fields[key])
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE editor_assets SET {sets} WHERE id = ?", (*fields.values(), asset_id))


# ---------------------------------------------------------------- timelines

HISTORY_LIMIT = 200


def _history(c: sqlite3.Connection, project_id: str) -> dict:
    row = c.execute("SELECT history FROM editor_projects WHERE id = ?", (project_id,)).fetchone()
    hist = json.loads(row[0]) if row and row[0] else {}
    return {"undo": hist.get("undo", []), "redo": hist.get("redo", [])}


def _save_history(c: sqlite3.Connection, project_id: str, hist: dict) -> None:
    hist = {k: v[-HISTORY_LIMIT:] for k, v in hist.items()}
    c.execute("UPDATE editor_projects SET history = ? WHERE id = ?", (json.dumps(hist), project_id))


def _insert_version(c: sqlite3.Connection, project_id: str, data: dict | str, note: str | None) -> tuple[int, int]:
    (latest,) = c.execute(
        "SELECT COALESCE(MAX(version), 0) FROM editor_timelines WHERE project_id = ?", (project_id,)
    ).fetchone()
    c.execute(
        "INSERT INTO editor_timelines (project_id, version, data, note) VALUES (?, ?, ?, ?)",
        (project_id, latest + 1, data if isinstance(data, str) else json.dumps(data), note),
    )
    return latest + 1, latest


def save_timeline(project_id: str, data: dict, note: str | None = None) -> int:
    """Store a new timeline version (an undoable edit) and return its number."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        version, previous = _insert_version(c, project_id, data, note)
        hist = _history(c, project_id)
        if previous:
            hist["undo"].append(previous)
        hist["redo"] = []
        _save_history(c, project_id, hist)
    return version


def _step_history(project_id: str, take: str, give: str, label: str) -> Optional[dict]:
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        hist = _history(c, project_id)
        if not hist[take]:
            return None
        target = hist[take].pop()
        row = c.execute("SELECT data, note FROM editor_timelines WHERE project_id = ? AND version = ?",
                        (project_id, target)).fetchone()
        if row is None:
            _save_history(c, project_id, hist)
            return None
        version, previous = _insert_version(c, project_id, row[0], f"{label}: back to version {target}")
        hist[give].append(previous)
        _save_history(c, project_id, hist)
    return {"version": version, "restored_from": target}


def undo_timeline(project_id: str) -> Optional[dict]:
    """Go back one edit. The undone state is saved as a new version, so nothing is ever lost."""
    return _step_history(project_id, "undo", "redo", "Undo")


def redo_timeline(project_id: str) -> Optional[dict]:
    return _step_history(project_id, "redo", "undo", "Redo")


def history_state(project_id: str) -> dict:
    with connect() as c:
        hist = _history(c, project_id)
    return {"can_undo": bool(hist["undo"]), "can_redo": bool(hist["redo"])}


def get_timeline(project_id: str, version: int | None = None) -> Optional[dict]:
    with connect() as c:
        if version is None:
            row = c.execute(
                "SELECT * FROM editor_timelines WHERE project_id = ? ORDER BY version DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        else:
            row = c.execute(
                "SELECT * FROM editor_timelines WHERE project_id = ? AND version = ?", (project_id, version)
            ).fetchone()
    return _row(row, ("data",))


def list_timeline_versions(project_id: str) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT version, note, created_at FROM editor_timelines WHERE project_id = ? ORDER BY version DESC",
            (project_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- chat messages

def add_message(project_id: str, role: str, content: str, *, job_id: str | None = None,
                timeline_version: int | None = None, meta: dict | None = None) -> int:
    with connect() as c:
        cur = c.execute(
            """INSERT INTO editor_messages (project_id, role, content, job_id, timeline_version, meta)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (project_id, role, content, job_id, timeline_version, json.dumps(meta) if meta else None),
        )
    return cur.lastrowid


def set_message_job(message_id: int, job_id: str) -> None:
    with connect() as c:
        c.execute("UPDATE editor_messages SET job_id = ? WHERE id = ?", (job_id, message_id))


def list_messages(project_id: str, limit: int = 100) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM (SELECT * FROM editor_messages WHERE project_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
            (project_id, limit),
        ).fetchall()
    return [_row(r, ("meta",)) for r in rows]
