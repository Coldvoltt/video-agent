"""
Accounts: email + password sign-in with server-side sessions.

- Passwords are hashed with scrypt (Python's hashlib; no extra dependency), each with its own salt.
- A session is a random token sent to the browser as an HttpOnly, SameSite=Lax cookie; only its SHA-256
  is stored, so the database never holds a usable token.
- Work done before signing in belongs to an anonymous id kept in the browser. Signing up or in with that
  id "claims" it: its projects (rows and files) move into the account. Anonymous ids can never be used
  to reach an account's projects; those need the session.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import shutil
import threading
import time
from typing import Optional

from . import store
from .config import settings

SESSION_COOKIE = "vid_session"
SESSION_DAYS = 30
MIN_PASSWORD = 8
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,24}$")
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}

# Settings people can change, and their defaults. Anything else sent is ignored.
DEFAULT_SETTINGS = {"integrations": {"telegram": {"notify": False}}}


class AuthError(ValueError):
    """A problem the person can fix; the message is safe to show."""


# ---------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), dklen=32, n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


def _clean_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address.")
    return email


# ---------------------------------------------------------------- users

def _public(row: dict) -> dict:
    return {"id": row["id"], "email": row["email"], "name": row.get("name") or "", "created_at": row.get("created_at")}


def create_user(email: str, password: str, name: str = "") -> dict:
    email = _clean_email(email)
    if len(password or "") < MIN_PASSWORD:
        raise AuthError(f"Use a password of at least {MIN_PASSWORD} characters.")
    name = " ".join((name or "").split())[:80]
    uid = "acct_" + secrets.token_hex(8)
    with store.connect() as c:
        if c.execute("SELECT 1 FROM editor_users WHERE email = ?", (email,)).fetchone():
            raise AuthError("There's already an account with that email. Sign in instead.")
        c.execute("INSERT INTO editor_users (id, email, name, password_hash, settings) VALUES (?, ?, ?, ?, ?)",
                  (uid, email, name, hash_password(password), json.dumps(DEFAULT_SETTINGS)))
    return get_user(uid)


def get_user(user_id: str) -> Optional[dict]:
    with store.connect() as c:
        row = c.execute("SELECT * FROM editor_users WHERE id = ?", (user_id,)).fetchone()
    return _public(dict(row)) if row else None


def authenticate(email: str, password: str) -> Optional[dict]:
    try:
        email = _clean_email(email)
    except AuthError:
        return None
    with store.connect() as c:
        row = c.execute("SELECT * FROM editor_users WHERE email = ?", (email,)).fetchone()
    if row is None:
        hash_password(password or "x")          # same cost either way: don't reveal which emails exist
        return None
    return _public(dict(row)) if verify_password(password or "", row["password_hash"]) else None


def is_account(user_id: str) -> bool:
    with store.connect() as c:
        return c.execute("SELECT 1 FROM editor_users WHERE id = ?", (user_id,)).fetchone() is not None


def update_profile(user_id: str, name: Optional[str] = None) -> dict:
    if name is not None:
        with store.connect() as c:
            c.execute("UPDATE editor_users SET name = ? WHERE id = ?", (" ".join(name.split())[:80], user_id))
    return get_user(user_id)


def change_password(user_id: str, current: str, new: str) -> None:
    with store.connect() as c:
        row = c.execute("SELECT password_hash FROM editor_users WHERE id = ?", (user_id,)).fetchone()
    if row is None or not verify_password(current or "", row["password_hash"]):
        raise AuthError("Your current password isn't right.")
    if len(new or "") < MIN_PASSWORD:
        raise AuthError(f"Use a password of at least {MIN_PASSWORD} characters.")
    with store.connect() as c:
        c.execute("UPDATE editor_users SET password_hash = ? WHERE id = ?", (hash_password(new), user_id))


# ---------------------------------------------------------------- settings

def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            out[k] = _merge(base[k], v)
        elif k in base and not isinstance(base[k], dict) and type(v) is type(base[k]):
            out[k] = v                                   # only known keys, with the right type
    return out


def get_settings(user_id: str) -> dict:
    with store.connect() as c:
        row = c.execute("SELECT settings FROM editor_users WHERE id = ?", (user_id,)).fetchone()
    saved = json.loads(row["settings"]) if row and row["settings"] else {}
    return _merge(DEFAULT_SETTINGS, saved)


def update_settings(user_id: str, changes: dict) -> dict:
    merged = _merge(get_settings(user_id), changes or {})
    with store.connect() as c:
        c.execute("UPDATE editor_users SET settings = ? WHERE id = ?", (json.dumps(merged), user_id))
    return merged


# ---------------------------------------------------------------- sessions

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = int(time.time()) + SESSION_DAYS * 86400
    with store.connect() as c:
        c.execute("INSERT INTO editor_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                  (_token_hash(token), user_id, expires))
    return token


def user_for_token(token: Optional[str]) -> Optional[dict]:
    if not token or len(token) > 200:
        return None
    with store.connect() as c:
        row = c.execute("SELECT user_id, expires_at FROM editor_sessions WHERE token_hash = ?",
                        (_token_hash(token),)).fetchone()
    if row is None or row["expires_at"] < time.time():
        return None
    return get_user(row["user_id"])


def delete_session(token: Optional[str]) -> None:
    if token:
        with store.connect() as c:
            c.execute("DELETE FROM editor_sessions WHERE token_hash = ?", (_token_hash(token),))


# ---------------------------------------------------------------- moving anonymous work into an account

def claim(anon_id: Optional[str], account_id: str) -> int:
    """Move an anonymous id's projects (rows and stored files) into an account; returns how many moved."""
    if not anon_id or not ID_RE.match(anon_id) or anon_id == account_id or is_account(anon_id):
        return 0
    with store.connect() as c:
        moved = c.execute("SELECT COUNT(*) FROM editor_projects WHERE user_id = ?", (anon_id,)).fetchone()[0]
        if not moved:
            return 0
        old, new = f"users/{anon_id}/", f"users/{account_id}/"
        for table in ("editor_projects", "editor_assets", "editor_jobs"):
            c.execute(f"UPDATE {table} SET user_id = ? WHERE user_id = ?", (account_id, anon_id))
        for col in ("storage_key", "proxy_key"):
            c.execute(f"UPDATE editor_assets SET {col} = ? || substr({col}, ?) WHERE {col} LIKE ? || '%'",
                      (new, len(old) + 1, old))
        c.execute("UPDATE editor_jobs SET result = replace(result, ?, ?) WHERE result LIKE ?",
                  (f'"{old}', f'"{new}', f'%"{old}%'))
    src, dst = settings.storage_root / "users" / anon_id, settings.storage_root / "users" / account_id
    if src.exists():
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():                      # projects/ (and anything else) into the account
            target = dst / child.name
            if target.exists():
                for sub in child.iterdir():
                    shutil.move(str(sub), str(target / sub.name))
                child.rmdir()
            else:
                shutil.move(str(child), str(target))
        src.rmdir()
    return moved


# ---------------------------------------------------------------- brute-force guard

_failures: dict[str, list[float]] = {}
_lock = threading.Lock()
WINDOW, LIMIT = 15 * 60, 10


def too_many_attempts(key: str) -> bool:
    now = time.time()
    with _lock:
        recent = [t for t in _failures.get(key, []) if now - t < WINDOW]
        _failures[key] = recent
        return len(recent) >= LIMIT


def note_failure(key: str) -> None:
    with _lock:
        _failures.setdefault(key, []).append(time.time())
