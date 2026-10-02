"""Hidden attendance management router.

Auth model:
  1. URL path is configurable via HIDDEN_URL_PATH (default /hidden).
  2. Access requires a single password set via HIDDEN_PASSWORD env var.
     - Frontend POSTs { password } to /api/hidden/login → gets a short-lived
       session token (opaque random string, kept in memory).
     - Subsequent /api/hidden/* calls must send `X-Hidden-Token: <token>`.
     - Tokens expire after 12h.
  3. If HIDDEN_PASSWORD is empty/unset, the entire /api/hidden/* surface
     refuses to start responding (server still boots but returns 404).

Public surface (post-refactor):
  GET    /api/hidden/attendance            — list ALL attendance rows,
                                              sorted by employee.id ASC,
                                              then date ASC.
  PATCH  /api/hidden/attendance/{id}/times — set checkin/checkout times for
                                              a row. Either field may be null
                                              (= "no time that day"); if both
                                              are null the attendance row is
                                              deleted entirely.
  POST   /api/hidden/login                 — password → token.

The timelog GET endpoint is kept for debugging; timelog POST is gone because
the new /times endpoint handles both insert and clear through one path.
"""
import hmac
import logging
import os
import secrets
import time
from datetime import date, datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status, Header, Request, Response
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app import crud, ratelimit
from app.models import Attendance, TimeLog
from app.audit import log_action
from sqlalchemy import asc

logger = logging.getLogger("checknv.hidden")

router = APIRouter(prefix="/api/hidden", tags=["hidden"])

# ── Auth config ────────────────────────────────────────────────────────────
HIDDEN_PASSWORD = os.environ.get("HIDDEN_PASSWORD", "")
TOKEN_TTL_SECONDS = 12 * 3600

# In-memory token store. For multi-worker uvicorn, swap to a shared store
# (Redis) — single-worker is fine for LAN kiosks.
_tokens: dict[str, float] = {}  # token -> expires_at_epoch


def _now() -> float:
    return time.time()


def _cleanup_tokens() -> None:
    """Drop expired tokens periodically."""
    now = _now()
    expired = [t for t, exp in _tokens.items() if exp <= now]
    for t in expired:
        _tokens.pop(t, None)


def _issue_token() -> str:
    _cleanup_tokens()
    token = secrets.token_urlsafe(32)
    _tokens[token] = _now() + TOKEN_TTL_SECONDS
    return token


def _check_token(token: str) -> bool:
    if not token:
        return False
    exp = _tokens.get(token)
    if exp is None:
        return False
    if exp <= _now():
        _tokens.pop(token, None)
        return False
    return True


def _require_password_configured() -> None:
    """The whole hidden surface must be disabled if password is unset."""
    if not HIDDEN_PASSWORD:
        raise HTTPException(status_code=404, detail="Not Found")


def require_hidden_token(
    x_hidden_token: Optional[str] = Header(default=None, alias="X-Hidden-Token"),
) -> str:
    """FastAPI dependency: verifies the X-Hidden-Token header."""
    _require_password_configured()
    if not x_hidden_token or not _check_token(x_hidden_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token không hợp lệ hoặc đã hết hạn. Vui lòng đăng nhập lại.",
        )
    return x_hidden_token


def _constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ── Login endpoint ─────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_in: int


@router.post("/login", response_model=LoginResponse)
def login(request: Request, payload: LoginRequest, response: Response):
    """Verify the shared password and issue a session token."""
    _require_password_configured()
    client_ip = request.client.host if request.client else "unknown"
    allowed, retry_after = ratelimit.check_ip(
        client_ip, max_requests=5, window_seconds=60
    )
    if not allowed:
        response.headers["Retry-After"] = str(retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Quá nhiều lần đăng nhập. Thử lại sau {retry_after}s.",
        )
    if not _constant_time_eq(payload.password, HIDDEN_PASSWORD):
        logger.warning("Failed hidden login attempt from %s",
                       request.client.host if request.client else "-")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sai mật khẩu",
        )
    token = _issue_token()
    logger.info("Hidden login OK (token issued)")
    return LoginResponse(token=token, expires_in=TOKEN_TTL_SECONDS)


# ── Pydantic schemas ───────────────────────────────────────────────────────

class AttendanceFlagRow(BaseModel):
    """One row of the /hidden list. Just enough for the simple table UI."""
    id: int
    employee_id: int
    employee_code: str
    employee_name: Optional[str]
    date: date
    checkin_time: Optional[datetime]
    checkout_time: Optional[datetime]

    class Config:
        from_attributes = True


class AttendanceTimesUpdate(BaseModel):
    """Body for PATCH /api/hidden/attendance/{id}/times.

    Each field accepts either an "HH:MM" string or null.
      - null → clear that time (remove from time_log, no attendance cell).
      - "HH:MM" → set that time to the given hour/minute on `attendance.date`.
    Either or both fields may be null. If both end up null the attendance
    row is deleted entirely.
    """
    checkin: Optional[str] = None
    checkout: Optional[str] = None

    @field_validator("checkin", "checkout", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        if v == "" or v is None:
            return None
        return v


# ── Attendance router (mounted at /api/hidden) ────────────────────────────

att_router = APIRouter(prefix="/attendance", tags=["hidden"])


@att_router.get("", response_model=List[AttendanceFlagRow])
def list_attendance(
    response: Response,
    db: Session = Depends(get_db),
    token: str = Depends(require_hidden_token),
):
    """Return ALL attendance rows for the /hidden page.

    Sort: by employee.id ascending, then date ascending.
    Safety: hard-capped at 5000 rows so a runaway DB can't OOM the worker.
    """
    HARD_LIMIT = 5000
    rows = (
        db.query(Attendance)
        .join(crud.Employee, crud.Employee.id == Attendance.employee_id)
        .order_by(asc(crud.Employee.id), asc(Attendance.date))
        .limit(HARD_LIMIT)
        .all()
    )
    response.headers["X-Total-Count"] = str(len(rows))
    response.headers["X-Hard-Limit"] = str(HARD_LIMIT)

    # Bulk-load employees to avoid N+1.
    emp_ids = {att.employee_id for att in rows}
    emps = (
        db.query(crud.Employee).filter(crud.Employee.id.in_(emp_ids)).all()
        if emp_ids else []
    )
    emp_by_id = {e.id: e for e in emps}

    return [
        AttendanceFlagRow(
            id=att.id,
            employee_id=att.employee_id,
            employee_code=(
                emp_by_id[att.employee_id].code
                if att.employee_id in emp_by_id else ""
            ),
            employee_name=(
                emp_by_id[att.employee_id].name
                if att.employee_id in emp_by_id else None
            ),
            date=att.date,
            checkin_time=att.checkin_time,
            checkout_time=att.checkout_time,
        )
        for att in rows
    ]


def _parse_hhmm(s: str, on_date: date, field: str) -> datetime:
    """Parse "HH:MM" → datetime on `on_date`. Raises 422 on bad input."""
    try:
        h, m = s.split(":", 1)
        return datetime(on_date.year, on_date.month, on_date.day,
                        int(h), int(m), 0)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=422,
            detail=f"{field} phải có dạng HH:MM (ví dụ '08:30'), nhận '{s}'",
        )


def _set_one_time(
    db: Session,
    employee_id: int,
    on_date: date,
    action: str,
    hhmm_or_none: Optional[str],
) -> Optional[TimeLog]:
    """Upsert (or delete) a single time_log row for (employee, date, action).

    - hhmm_or_none is None  → delete the existing row (if any). Returns None.
    - hhmm_or_none is "HH:MM" → insert or update the row with that time on
      `on_date`. Returns the TimeLog row.

    All writes go through session.flush(); caller is responsible for commit.
    """
    if hhmm_or_none is None:
        # Clear: delete the existing row(s) for (employee, date, action).
        existing = (
            db.query(TimeLog)
            .filter(
                TimeLog.employee_id == employee_id,
                TimeLog.date == on_date,
                TimeLog.action == action,
            )
            .all()
        )
        for row in existing:
            db.delete(row)
        db.flush()
        return None

    # Upsert.
    parsed = _parse_hhmm(hhmm_or_none, on_date, action)
    existing = (
        db.query(TimeLog)
        .filter(
            TimeLog.employee_id == employee_id,
            TimeLog.date == on_date,
            TimeLog.action == action,
        )
        .first()
    )
    if existing is not None:
        existing.time_value = parsed
        existing.is_manual = True
        existing.actor = "hidden"
        db.flush()
        return existing

    row = TimeLog(
        employee_id=employee_id,
        date=on_date,
        action=action,
        time_value=parsed,
        is_manual=True,
        actor="hidden",
    )
    db.add(row)
    db.flush()
    return row


@att_router.patch("/{attendance_id}/times", response_model=AttendanceFlagRow)
def set_attendance_times(
    attendance_id: int,
    payload: AttendanceTimesUpdate,
    db: Session = Depends(get_db),
    token: str = Depends(require_hidden_token),
):
    """Set (or clear) checkin/checkout times for one attendance row.

    Either field accepts "HH:MM" (set) or null (clear).
    If both fields end up null after the operation, the attendance row
    itself is deleted — the day simply has no attendance record.
    """
    att = db.query(Attendance).filter(Attendance.id == attendance_id).first()
    if not att:
        raise HTTPException(status_code=404, detail="Không tìm thấy bản ghi chấm công")

    # Capture before-state for the audit log.
    before_ci = att.checkin_time
    before_co = att.checkout_time

    _set_one_time(db, att.employee_id, att.date, "checkin", payload.checkin)
    _set_one_time(db, att.employee_id, att.date, "checkout", payload.checkout)

    # Re-read derived times. If both are None, delete the attendance row.
    db.flush()
    from app.log_timelog import get_action_times  # local import to avoid cycle
    new_ci, new_co = get_action_times(db, att.employee_id, att.date)

    if new_ci is None and new_co is None:
        # Nothing left — remove the attendance row entirely.
        db.delete(att)
        db.commit()
        log_action(
            db, actor="hidden", action="clear_attendance",
            entity_type="attendance", entity_id=attendance_id,
            detail={
                "employee_id": att.employee_id,
                "date": str(att.date),
                "before": {"checkin": str(before_ci), "checkout": str(before_co)},
                "after": {"checkin": None, "checkout": None},
            },
        )
        # 204 No Content would be RESTful, but our spec returns the (now empty)
        # row with a deleted marker. The client will reload the list.
        # We signal "deleted" by returning the row with a flag:
        return AttendanceFlagRow(
            id=att.id, employee_id=att.employee_id,
            employee_code="", employee_name=None,
            date=att.date, checkin_time=None, checkout_time=None,
        )

    # Update the summary row in-place.
    att.checkin_time = new_ci
    att.checkout_time = new_co
    db.commit()
    db.refresh(att)

    log_action(
        db, actor="hidden", action="set_times",
        entity_type="attendance", entity_id=attendance_id,
        detail={
            "before": {"checkin": str(before_ci), "checkout": str(before_co)},
            "after": {"checkin": str(new_ci), "checkout": str(new_co)},
        },
    )

    emp = db.query(crud.Employee).filter(crud.Employee.id == att.employee_id).first()
    return AttendanceFlagRow(
        id=att.id, employee_id=att.employee_id,
        employee_code=emp.code if emp else "",
        employee_name=emp.name if emp else None,
        date=att.date, checkin_time=att.checkin_time, checkout_time=att.checkout_time,
    )


# Mount the attendance router under /api/hidden.
# Endpoints become:
#   GET   /api/hidden/attendance
#   PATCH /api/hidden/attendance/{id}/times
router.include_router(att_router)