"""
SQLite database module for user and session management.
"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

DATABASE_PATH = Path("output/data.db")


def init_db():
    """Initialize database tables."""
    DATABASE_PATH.parent.mkdir(exist_ok=True)

    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                source TEXT NOT NULL,
                video_url TEXT,
                video_path TEXT,
                audio_path TEXT,
                title TEXT NOT NULL,
                duration REAL NOT NULL,
                collection_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS transcripts (
                session_id TEXT PRIMARY KEY,
                segments TEXT NOT NULL,
                full_text TEXT NOT NULL,
                language TEXT,
                duration REAL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_conversations_session_id ON conversations(session_id);
            CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id);
            CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
        """)


@contextmanager
def get_connection():
    """Get database connection with context manager."""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================
# SESSION OPERATIONS
# ============================================================

def create_session(
    user_id: str,
    source: str,
    title: str,
    duration: float,
    collection_name: str,
    video_url: str = None,
    video_path: str = None,
    audio_path: str = None,
) -> str:
    """Create a new session."""
    session_id = str(uuid.uuid4())[:12]

    with get_connection() as conn:
        conn.execute(
            """INSERT INTO sessions
               (id, user_id, source, video_url, video_path, audio_path, title, duration, collection_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, user_id, source, video_url, video_path, audio_path, title, duration, collection_name)
        )

    return session_id


def get_session(session_id: str, user_id: str = None) -> Optional[dict]:
    """Get session by ID, optionally filtered by user."""
    with get_connection() as conn:
        if user_id:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()

    if row:
        session = dict(row)
        # Load transcript
        transcript = get_transcript(session_id)
        if transcript:
            session["transcript"] = transcript
        return session
    return None


def list_sessions(user_id: str) -> list[dict]:
    """List all sessions for a user."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, source, title, duration, created_at FROM sessions WHERE user_id = ?",
            (user_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def delete_session(session_id: str, user_id: str) -> bool:
    """Delete a session (cascades to transcript)."""
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id)
        )
    return cursor.rowcount > 0


# ============================================================
# TRANSCRIPT OPERATIONS
# ============================================================

def save_transcript(session_id: str, transcript: dict):
    """Save transcript for a session."""
    segments_json = json.dumps(transcript.get("segments", []), ensure_ascii=False)

    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO transcripts
               (session_id, segments, full_text, language, duration)
               VALUES (?, ?, ?, ?, ?)""",
            (
                session_id,
                segments_json,
                transcript.get("full_text", ""),
                transcript.get("language"),
                transcript.get("duration"),
            )
        )


def get_transcript(session_id: str) -> Optional[dict]:
    """Get transcript for a session."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM transcripts WHERE session_id = ?", (session_id,)
        ).fetchone()

    if row:
        return {
            "segments": json.loads(row["segments"]),
            "full_text": row["full_text"],
            "language": row["language"],
            "duration": row["duration"],
        }
    return None


# ============================================================
# CONVERSATION OPERATIONS
# ============================================================

def create_conversation(session_id: str, user_id: str) -> str:
    """Create a new conversation for a session."""
    conversation_id = str(uuid.uuid4())[:12]

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO conversations (id, session_id, user_id) VALUES (?, ?, ?)",
            (conversation_id, session_id, user_id)
        )

    return conversation_id


def create_conversation_with_id(conversation_id: str, session_id: str, user_id: str):
    """Create a conversation with a specific ID (for auto-creation)."""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO conversations (id, session_id, user_id) VALUES (?, ?, ?)",
            (conversation_id, session_id, user_id)
        )


def get_conversation(conversation_id: str, user_id: str = None) -> Optional[dict]:
    """Get conversation by ID, optionally filtered by user."""
    with get_connection() as conn:
        if user_id:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
                (conversation_id, user_id)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()

    if row:
        return dict(row)
    return None


def list_conversations(session_id: str, user_id: str) -> list[dict]:
    """List all conversations for a session."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, created_at FROM conversations WHERE session_id = ? AND user_id = ?",
            (session_id, user_id)
        ).fetchall()
    return [dict(row) for row in rows]


def delete_conversation(conversation_id: str, user_id: str) -> bool:
    """Delete a conversation and its messages."""
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id)
        )
    return cursor.rowcount > 0


def add_message(conversation_id: str, role: str, content: str) -> int:
    """Add a message to a conversation."""
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, role, content)
        )
    return cursor.lastrowid


def get_messages(conversation_id: str, limit: int = 50) -> list[dict]:
    """Get messages for a conversation."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY id ASC LIMIT ?",
            (conversation_id, limit)
        ).fetchall()
    return [dict(row) for row in rows]


# ============================================================
# USER STORAGE PATHS
# ============================================================

def get_user_storage_path(user_id: str, subdir: str = None) -> Path:
    """Get user-specific storage directory."""
    base = Path("output/users") / user_id
    if subdir:
        path = base / subdir
    else:
        path = base
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_user_video_path(user_id: str, filename: str) -> Path:
    """Get path for user's video file."""
    return get_user_storage_path(user_id, "videos") / filename


def get_user_audio_path(user_id: str, video_name: str) -> Path:
    """Get path for user's audio file."""
    return get_user_storage_path(user_id, "audio") / f"{video_name}_audio.mp3"


def get_user_snippet_path(user_id: str, filename: str) -> Path:
    """Get path for user's snippet file."""
    return get_user_storage_path(user_id, "snippets") / filename


def get_user_screenshot_path(user_id: str, filename: str) -> Path:
    """Get path for user's screenshot file."""
    return get_user_storage_path(user_id, "screenshots") / filename


# Initialize database on module import
init_db()
