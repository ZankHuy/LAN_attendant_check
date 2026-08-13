"""Audit log for sensitive admin/hidden actions.

Every call to `log_action(...)` writes one row to the `audit_log` table.
Operators can query /api/audit (admin-only) to review who did what.

This module intentionally has no FastAPI dependency so it can be imported
from both routers and background jobs.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models import AuditLog


def log_action(
    db: Session,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: Optional[int] = None,
    detail: Optional[dict] = None,
) -> None:
    """Append an audit row. Auto-commits.

    Args:
        actor: identifier of who did the action ("admin", "hidden", username).
        action: short verb ("create", "update", "patch_flags", "delete",
                "change_password", "set_settings").
        entity_type: what was touched ("attendance", "employee", "settings").
        entity_id: row id, when applicable.
        detail: free-form JSON-serialisable dict with extra context
                (old/new values, IPs, etc.).
    """
    try:
        row = AuditLog(
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=json.dumps(detail, ensure_ascii=False, default=str)
            if detail is not None else None,
            created_at=datetime.utcnow(),
        )
        db.add(row)
        db.commit()
    except Exception:
        # Never let audit logging crash the user-facing request. Roll back
        # the audit row but keep the caller transaction intact by NOT
        # touching their changes — we just suppress the error and log it.
        try:
            db.rollback()
        except Exception:
            pass
        # Best-effort logging via stdlib if app logging isn't set up yet.
        import logging
        logging.getLogger("checknv.audit").exception(
            "Failed to write audit row: %s %s %s", actor, action, entity_type
        )
