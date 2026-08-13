"""Simple in-process rate limiter backed by SQLite.

Used to throttle /api/attendance/checkin + /checkout to N requests per
window per (client_ip, employee_id) tuple. The point isn't to be
bulletproof — it's to stop a malicious kiosk script from spamming the
endpoint and filling up the device_bans table.

For multi-worker uvicorn, replace _engine with a shared one (Redis).
Single-worker is fine for LAN kiosks.
"""
from __future__ import annotations

import logging
import time
from typing import Tuple

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.database import engine

logger = logging.getLogger("checknv.ratelimit")


def _bucket_key(client_ip: str, employee_id: int, window_seconds: int) -> int:
    """Compute which time bucket the request falls into."""
    return int(time.time() // window_seconds)


def check_and_record(
    client_ip: str,
    employee_id: int,
    max_requests: int = 10,
    window_seconds: int = 60,
) -> Tuple[bool, int]:
    """Return (allowed, retry_after_seconds).

    Allowed is True if the request can proceed; otherwise the caller should
    return HTTP 429 with retry_after_seconds as the Retry-After header.
    """
    bucket = _bucket_key(client_ip, employee_id, window_seconds)
    try:
        with engine.begin() as conn:
            # Upsert: increment count for this bucket; if missing, insert 1.
            conn.execute(text(
                "INSERT INTO rate_limit_buckets (bucket_key, employee_id, count, updated_at) "
                "VALUES (:b, :e, 1, :t) "
                "ON CONFLICT(bucket_key, employee_id) DO UPDATE SET "
                "count = count + 1, updated_at = :t"
            ), {"b": bucket, "e": employee_id, "t": time.time()})

            row = conn.execute(text(
                "SELECT count FROM rate_limit_buckets "
                "WHERE bucket_key = :b AND employee_id = :e"
            ), {"b": bucket, "e": employee_id}).fetchone()
            count = row[0] if row else 0
    except OperationalError as exc:
        # If the rate_limit_buckets table doesn't exist yet (first run
        # before create_all finishes), allow the request — better than
        # blocking legitimate users.
        logger.warning("rate_limit_buckets unavailable: %s", exc)
        return True, 0

    if count > max_requests:
        # Seconds left until this bucket rolls over.
        retry_after = int((bucket + 1) * window_seconds - time.time())
        return False, max(retry_after, 1)
    return True, 0


def check_ip(
    client_ip: str,
    max_requests: int = 5,
    window_seconds: int = 60,
) -> Tuple[bool, int]:
    """Per-IP rate limit (no employee_id dimension). Use for endpoints like
    /api/auth/login where the requester isn't yet identified.

    Bucket key uses employee_id = 0 as a sentinel for "anonymous". We can't
    reuse `check_and_record(employee_id=0, ...)` cleanly because that would
    conflate anonymous traffic across different IPs; instead we hash the IP
    into a stable int and use that as a unique employee_id bucket.

    Returns (allowed, retry_after_seconds).
    """
    import hashlib
    ip_hash = int.from_bytes(
        hashlib.sha1(client_ip.encode("utf-8")).digest()[:4], "big"
    )
    return check_and_record(
        client_ip=f"ip:{client_ip}",
        employee_id=ip_hash,
        max_requests=max_requests,
        window_seconds=window_seconds,
    )


def purge_old_buckets(retention_seconds: int = 3600) -> int:
    """Drop buckets older than retention. Call periodically (e.g. daily)."""
    cutoff = time.time() - retention_seconds
    with engine.begin() as conn:
        result = conn.execute(text(
            "DELETE FROM rate_limit_buckets WHERE updated_at < :c"
        ), {"c": cutoff})
    return result.rowcount or 0
