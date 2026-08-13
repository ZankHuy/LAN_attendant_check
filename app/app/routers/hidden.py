"""Hidden attendance management router.

Auth model (since the "hidden URL via env" refactor):
  1. URL path is configurable via HIDDEN_URL_PATH (default /hidden).
  2. Access requires a single password set via HIDDEN_PASSWORD env var.
     - Frontend POSTs { password } to /api/hidden/login → gets a short-lived
       session token (opaque random string, kept in memory + signed).
     - Subsequent /api/hidden/* calls must send `X-Hidden-Token: <token>`.
     - Tokens expire after 12h.
  3. If HIDDEN_PASSWORD is empty/unset, the entire /api/hidden/* surface
     refuses to start responding (server still boots but returns 404).

All business endpoints (list, get, put, patch, delete attendance, plus
the AM/PM deadline settings panel) are untouched. Logic is unchanged.
"""
import hashlib
import hmac
import logging
import os
import secrets
import time
from datetime import date, datetime, time
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status, Header, Query, Request, Response
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app import crud, ratelimit
from app.models import Attendance
from app.audit import log_action

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
    request: Request,
    x_hidden_token: Optional[str] = Header(default=None, alias="X-Hidden-Token"),
) -> str:
    """FastAPI dependency: verifies the X-Hidden-Token header.

    Use this on every /api/hidden/attendance/* endpoint. The token is
    issued by POST /api/hidden/login. Returned value is the token (for
    audit logging).
    """
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
        # Log failed attempts (don't log the password itself).
        logger.warning("Failed hidden login attempt from %s",
                       _client_ip_fallback())
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sai mật khẩu",
        )
    token = _issue_token()
    logger.info("Hidden login OK (token issued)")
    return LoginResponse(token=token, expires_in=TOKEN_TTL_SECONDS)


def _client_ip_fallback() -> str:
    """Best-effort client IP for log messages. Defined lazily because we
    don't have Request here in the login endpoint."""
    return "-"


# ── Pydantic schemas (unchanged) ───────────────────────────────────────────

class AttendanceFlagUpdate(BaseModel):
    """Update only the is_on_time / is_early_leave flags for an attendance row."""
    is_on_time: Optional[bool] = None
    is_early_leave: Optional[bool] = None


class AttendanceFlagRow(BaseModel):
    id: int
    employee_id: int
    date: date
    checkin_time: Optional[datetime]
    checkout_time: Optional[datetime]
    is_on_time: bool
    is_early_leave: bool
    shift: Optional[str]
    is_full_day: bool
    early_leave_minutes: Optional[int]
    checkin_device_id: Optional[str]
    checkout_device_id: Optional[str]
    employee_name: Optional[str]

    class Config:
        from_attributes = True


class AttendanceCreate(BaseModel):
    employee_code: str  # mã NV từ sheet admin, e.g. "NV001"
    date: date
    checkin_time: Optional[datetime] = None
    checkout_time: Optional[datetime] = None
    # Optional manual overrides. If left None, the backend computes them
    # from checkin/checkout times using shift-aware rules.
    is_on_time: Optional[bool] = None
    is_early_leave: Optional[bool] = None

    @field_validator('checkin_time', 'checkout_time', mode='before')
    @classmethod
    def _empty_to_none(cls, v):
        if v == '' or v is None:
            return None
        return v


class AttendanceUpdate(BaseModel):
    """Full update: create if not exists, or patch existing row."""
    employee_id: int
    date: date
    is_on_time: bool = False
    is_early_leave: bool = False


# ── Attendance router (mounted at /api/hidden/attendance) ─────────────────

att_router = APIRouter(prefix="/attendance", tags=["hidden"])


@att_router.get("", response_model=List[AttendanceFlagRow])
def list_attendance(
    response: Response,
    employee_id: Optional[int] = None,
    date: Optional[date] = None,
    page: int = Query(default=0, ge=0),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    token: str = Depends(require_hidden_token),
):
    """List attendance rows. Supports filtering by employee_id and/or a single date."""
    q = db.query(Attendance)
    if employee_id is not None:
        q = q.filter(Attendance.employee_id == employee_id)
    if date is not None:
        q = q.filter(Attendance.date == date)
    total = q.count()
    rows = (
        q.order_by(Attendance.date.desc())
        .offset(page * page_size)
        .limit(page_size)
        .all()
    )
    response.headers["X-Total-Count"] = str(total)

    result = []
    for att in rows:
        emp = db.query(crud.Employee).filter(crud.Employee.id == att.employee_id).first()
        result.append(AttendanceFlagRow(
            id=att.id,
            employee_id=att.employee_id,
            date=att.date,
            checkin_time=att.checkin_time,
            checkout_time=att.checkout_time,
            is_on_time=att.is_on_time,
            is_early_leave=att.is_early_leave,
            shift=att.shift,
            is_full_day=att.is_full_day,
            early_leave_minutes=att.early_leave_minutes,
            checkin_device_id=att.checkin_device_id,
            checkout_device_id=att.checkout_device_id,
            employee_name=emp.name if emp else None,
        ))
    return result


@att_router.get("/{attendance_id}", response_model=AttendanceFlagRow)
def get_attendance(
    attendance_id: int,
    db: Session = Depends(get_db),
    token: str = Depends(require_hidden_token),
):
    att = db.query(Attendance).filter(Attendance.id == attendance_id).first()
    if not att:
        raise HTTPException(status_code=404, detail="Không tìm thấy bản ghi chấm công")
    emp = db.query(crud.Employee).filter(crud.Employee.id == att.employee_id).first()
    return AttendanceFlagRow(
        id=att.id,
        employee_id=att.employee_id,
        date=att.date,
        checkin_time=att.checkin_time,
        checkout_time=att.checkout_time,
        is_on_time=att.is_on_time,
        is_early_leave=att.is_early_leave,
        shift=att.shift,
        is_full_day=att.is_full_day,
        early_leave_minutes=att.early_leave_minutes,
        checkin_device_id=att.checkin_device_id,
        checkout_device_id=att.checkout_device_id,
        employee_name=emp.name if emp else None,
    )


@att_router.put("", response_model=AttendanceFlagRow)
def put_attendance(
    payload: AttendanceCreate,
    db: Session = Depends(get_db),
    token: str = Depends(require_hidden_token),
):
    """Create a new attendance row. Fails if a row already exists for
    employee_id + date combination (use PATCH /{id} to update existing).
    """
    # 1. Resolve employee by code
    emp_code = payload.employee_code.strip()
    emp = db.query(crud.Employee).filter(crud.Employee.code == emp_code).first()
    if not emp:
        raise HTTPException(
            status_code=404,
            detail=f"Không tìm thấy nhân viên mã '{emp_code}'. "
                   f"Hãy dùng đúng mã NV trong sheet admin (VD: NV001)."
        )

    # 2. Guard duplicate
    existing = db.query(Attendance).filter(
        Attendance.employee_id == emp.id,
        Attendance.date == payload.date,
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Đã có bản ghi cho {payload.employee_code} ngày {payload.date}. "
                   f"Dùng PATCH /{{id}} để cập nhật.",
        )

    # 3. Compute flags from times using shift-aware logic
    dow = payload.date.weekday()  # 0=Mon, 5=Sat, 6=Sun
    AM_CHECKIN_LATEST = crud.AM_CHECKIN_LATEST

    if payload.checkin_time:
        ci = payload.checkin_time.time()
        shift = "AM" if ci <= AM_CHECKIN_LATEST else "PM"
    else:
        ci = None
        shift = None

    if payload.checkout_time:
        co = payload.checkout_time.time()
    else:
        co = None

    am_deadline = crud.get_am_deadline(db, payload.date)
    if dow == 6:  # Sunday → no flags
        is_on_time = None
        is_early_leave = None
        is_full_day = False
        early_leave_minutes = None
    elif dow == 5:  # Saturday
        is_on_time = (ci is not None and ci <= time(8, 30)) if ci else None
        is_early_leave = (co is not None and co < time(12, 0)) if co else None
        is_full_day = False
        early_leave_minutes = None
        if is_early_leave and co:
            early_leave_minutes = (12 * 60) - (co.hour * 60 + co.minute)
    else:  # Mon-Fri
        is_on_time = (ci is not None and ci <= am_deadline) if ci else None
        pm_deadline = crud.get_pm_deadline(db, payload.date)
        is_early_leave = (co is not None and co < pm_deadline) if co else None
        early_leave_minutes = None
        if is_early_leave and co:
            early_leave_minutes = (pm_deadline.hour * 60 + pm_deadline.minute) - (co.hour * 60 + co.minute)
        is_full_day = (
            ci is not None and co is not None
            and ci <= am_deadline
            and co >= pm_deadline
        )

    # 4. Persist
    att = Attendance(
        employee_id=emp.id,
        date=payload.date,
        checkin_time=payload.checkin_time,
        checkout_time=payload.checkout_time,
        # Use the auto-computed value unless the caller explicitly passed an
        # override (is_on_time / is_early_leave can be True, False, or None).
        is_on_time=(payload.is_on_time if payload.is_on_time is not None else is_on_time),
        is_early_leave=(payload.is_early_leave if payload.is_early_leave is not None else is_early_leave),
        shift=shift,
        is_full_day=is_full_day,
        early_leave_minutes=early_leave_minutes,
    )
    db.add(att)
    db.commit()
    db.refresh(att)

    log_action(db, actor="hidden", action="create", entity_type="attendance",
               entity_id=att.id, detail={"employee_id": emp.id, "date": str(payload.date)})

    return AttendanceFlagRow(
        id=att.id, employee_id=att.employee_id, date=att.date,
        checkin_time=att.checkin_time, checkout_time=att.checkout_time,
        is_on_time=att.is_on_time, is_early_leave=att.is_early_leave,
        shift=att.shift, is_full_day=att.is_full_day,
        early_leave_minutes=att.early_leave_minutes,
        checkin_device_id=att.checkin_device_id, checkout_device_id=att.checkout_device_id,
        employee_name=emp.name,
    )


@att_router.patch("/{attendance_id}", response_model=AttendanceFlagRow)
def patch_attendance_flags(
    attendance_id: int,
    payload: AttendanceFlagUpdate,
    db: Session = Depends(get_db),
    token: str = Depends(require_hidden_token),
):
    """Update is_on_time and/or is_early_leave on an existing attendance row."""
    att = db.query(Attendance).filter(Attendance.id == attendance_id).first()
    if not att:
        raise HTTPException(status_code=404, detail="Không tìm thấy bản ghi chấm công")

    before = {
        "is_on_time": att.is_on_time,
        "is_early_leave": att.is_early_leave,
    }
    if payload.is_on_time is not None:
        att.is_on_time = payload.is_on_time
    if payload.is_early_leave is not None:
        att.is_early_leave = payload.is_early_leave
    db.commit()
    db.refresh(att)

    after = {
        "is_on_time": att.is_on_time,
        "is_early_leave": att.is_early_leave,
    }
    log_action(db, actor="hidden", action="patch_flags", entity_type="attendance",
               entity_id=att.id, detail={"before": before, "after": after})

    emp = db.query(crud.Employee).filter(crud.Employee.id == att.employee_id).first()
    return AttendanceFlagRow(
        id=att.id, employee_id=att.employee_id, date=att.date,
        checkin_time=att.checkin_time, checkout_time=att.checkout_time,
        is_on_time=att.is_on_time, is_early_leave=att.is_early_leave,
        shift=att.shift, is_full_day=att.is_full_day,
        early_leave_minutes=att.early_leave_minutes,
        checkin_device_id=att.checkin_device_id, checkout_device_id=att.checkout_device_id,
        employee_name=emp.name if emp else None,
    )


@att_router.delete("/{attendance_id}")
def delete_attendance(
    attendance_id: int,
    db: Session = Depends(get_db),
    token: str = Depends(require_hidden_token),
):
    """Delete an attendance row."""
    att = db.query(Attendance).filter(Attendance.id == attendance_id).first()
    if not att:
        raise HTTPException(status_code=404, detail="Không tìm thấy bản ghi chấm công")

    snapshot = {
        "employee_id": att.employee_id,
        "date": str(att.date),
    }
    db.delete(att)
    db.commit()

    log_action(db, actor="hidden", action="delete", entity_type="attendance",
               entity_id=attendance_id, detail=snapshot)

    return {"message": f"Đã xóa bản ghi chấm công id={attendance_id}"}


# Mount the attendance router under /api/hidden. Endpoints become
# /api/hidden/attendance, /api/hidden/attendance/{id} etc.
router.include_router(att_router)
