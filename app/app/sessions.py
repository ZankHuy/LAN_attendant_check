"""Session-based authentication for admin endpoints.

Replaces the previous Basic-Auth-with-localStorage approach. Sessions are
opaque random tokens stored server-side (in-memory dict, sufficient for a
single-worker LAN kiosk). The token is delivered to the browser via a
Secure; HttpOnly; SameSite=Strict cookie so JS / XSS cannot read it.

For multi-worker deployments, swap `_SESSIONS` for a shared store (Redis).
"""
from __future__ import annotations

import secrets
import time
from typing import Optional

# Idle timeout: any admin session unused for this many seconds is dropped.
IDLE_TIMEOUT_SECONDS = 30 * 60

# Absolute timeout: even an active session dies after this many seconds.
ABSOLUTE_TIMEOUT_SECONDS = 12 * 3600

# Cookie name delivered to the client.
COOKIE_NAME = "checknv_session"


_SESSIONS: dict[str, dict] = {}
# session_id -> {"created_at": float, "last_seen": float}


def _now() -> float:
    return time.time()


def _purge_expired() -> None:
    now = _now()
    expired = [
        sid for sid, meta in _SESSIONS.items()
        if (now - meta["last_seen"] > IDLE_TIMEOUT_SECONDS)
        or (now - meta["created_at"] > ABSOLUTE_TIMEOUT_SECONDS)
    ]
    for sid in expired:
        _SESSIONS.pop(sid, None)


def create_session() -> str:
    """Issue a new opaque session id."""
    _purge_expired()
    sid = secrets.token_urlsafe(32)
    _SESSIONS[sid] = {
        "created_at": _now(),
        "last_seen": _now(),
    }
    return sid


def touch(sid: str) -> bool:
    """Update last_seen; return False if the session is unknown / expired."""
    meta = _SESSIONS.get(sid)
    if not meta:
        return False
    now = _now()
    if (now - meta["last_seen"] > IDLE_TIMEOUT_SECONDS) \
            or (now - meta["created_at"] > ABSOLUTE_TIMEOUT_SECONDS):
        _SESSIONS.pop(sid, None)
        return False
    meta["last_seen"] = now
    return True


def revoke(sid: str) -> None:
    _SESSIONS.pop(sid, None)


def get_session_id(cookie_value: Optional[str]) -> Optional[str]:
    return cookie_value if cookie_value else None