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
  GET    /api/hidden/attendance            — list attendance rows, sorted by
                                              employee.id ASC, then date ASC.
                                              Optional `?from=&to=` inclusive
                                              date bounds (max span 366 days).
  POST   /api/hidden/attendance            — create a brand-new attendance
                                              record for an (employee, date)
                                              that has no record yet (for
                                              fixing days the employee forgot
                                              to check in/out at all). Only
                                              past/today dates; rejects
                                              duplicates with 409.
  PATCH  /api/hidden/attendance/{id}/times — set checkin/checkout times for
                                              a row. Either field may be null
                                              (= "no time that day"); if both
                                              are null the attendance row is
                                              deleted entirely.
  POST   /api/hidden/login                 — password → token.

The timelog GET endpoint is kept for debugging; timelog POST is gone because
the new /times endpoint handles both insert and clear through one path.

Attendance rule enforced by crud.compute_work_day: a day only earns work
value when BOTH checkin and checkout exist. Missing either one => 0 công.
"""
import hmac
import logging
import os
import secrets
import time
from datetime import date, datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status, Header, Query, Request, Response
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


class AttendanceCreate(BaseModel):
    """Body for POST /api/hidden/attendance (tạo bản ghi mới).

    Dùng khi nhân viên quên chấm công (không có bản ghi nào cho ngày đó)
    nhưng thực tế đã có đủ checkin + checkout — cho phép bổ sung lại.

    - `employee_id`: phải tồn tại.
    - `date`: bắt buộc là ngày ĐÃ QUA (không cho phép ngày tương lai).
    - `checkin` / `checkout`: "HH:MM" hoặc null. Được phép chỉ nhập một
      trong hai, nhưng không được để trống CẢ HAI (bản ghi rỗng vô nghĩa).
      Lưu ý: theo quy tắc tính công hiện tại, ngày thiếu một trong hai giờ
      sẽ ra 0 công — nên nhập đủ cả hai nếu muốn ngày đó được tính công.
    """
    employee_id: int
    date: date
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

# Cap on how wide a single date-range filter may be, so a careless request
# (e.g. from=1900-01-01) can't pull the whole table into memory.
MAX_RANGE_DAYS = 366


@att_router.get("", response_model=List[AttendanceFlagRow])
def list_attendance(
    response: Response,
    date_from: Optional[date] = Query(
        default=None, alias="from",
        description="Chỉ lấy bản ghi từ ngày này trở đi (YYYY-MM-DD)",
    ),
    date_to: Optional[date] = Query(
        default=None, alias="to",
        description="Chỉ lấy bản ghi tới ngày này (YYYY-MM-DD)",
    ),
    db: Session = Depends(get_db),
    token: str = Depends(require_hidden_token),
):
    """Return attendance rows for the /hidden page, optionally date-filtered.

    Query params:
      - `from` / `to`: optional inclusive date bounds. Either, both, or
        neither may be supplied. `from > to` is a 422, and the range may not
        span more than MAX_RANGE_DAYS days.

    Sort: by employee.id ascending, then date ascending.
    Safety: hard-capped at 5000 rows so a runaway DB can't OOM the worker.
    """
    if date_from and date_to:
        if date_from > date_to:
            raise HTTPException(
                status_code=422,
                detail=f"Ngày bắt đầu ({date_from}) không được sau ngày kết thúc ({date_to}).",
            )
        if (date_to - date_from).days > MAX_RANGE_DAYS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Khoảng lọc tối đa {MAX_RANGE_DAYS} ngày "
                    f"(nhận {((date_to - date_from).days) + 1} ngày). "
                    "Hãy chia nhỏ khoảng thời gian."
                ),
            )

    HARD_LIMIT = 5000
    q = db.query(Attendance).join(
        crud.Employee, crud.Employee.id == Attendance.employee_id
    )
    if date_from is not None:
        q = q.filter(Attendance.date >= date_from)
    if date_to is not None:
        q = q.filter(Attendance.date <= date_to)
    rows = q.order_by(asc(crud.Employee.id), asc(Attendance.date)).limit(HARD_LIMIT).all()

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


def _build_flag_row(att: Attendance, emp: Optional[crud.Employee]) -> AttendanceFlagRow:
    """Build the API response shape for one attendance row.

    Shared by PATCH (edit existing) and POST (create new) so both endpoints
    return an identical payload the frontend can render the same way.
    """
    return AttendanceFlagRow(
        id=att.id,
        employee_id=att.employee_id,
        employee_code=emp.code if emp else "",
        employee_name=emp.name if emp else None,
        date=att.date,
        checkin_time=att.checkin_time,
        checkout_time=att.checkout_time,
    )


@att_router.post("", response_model=AttendanceFlagRow, status_code=status.HTTP_201_CREATED)
def create_attendance(
    payload: AttendanceCreate,
    db: Session = Depends(get_db),
    token: str = Depends(require_hidden_token),
):
    """Create a brand-new attendance record for a (employee, date) with no record yet.

    Use case: nhân viên quên chấm công nên hệ thống không có bản ghi nào cho
    ngày đó, nhưng thực tế họ đã làm việc đủ buổi. Người quản lý tạo bản ghi
    thủ công ở đây để ngày đó được tính công.

    Rules enforced:
      - Chỉ tạo cho ngày ĐÃ QUA (date <= hôm nay), trả 422 nếu ngày tương lai.
      - employee_id phải tồn tại, trả 404 nếu không.
      - Phải có ít nhất MỘT trong hai giờ, trả 422 nếu cả hai đều trống
        (bản ghi rỗng sẽ bị xoá ngay lập tức, nên coi như lỗi đầu vào).
      - Nếu đã tồn tại bản ghi cho (employee, date) thì trả 409 và hướng dẫn
        dùng PATCH /{id}/times để sửa, tránh tạo trùng.
    """
    from app.log_timelog import _upsert_attendance  # local import to avoid cycle

    # 1. Ngày tương lai không được phép tạo.
    if payload.date > date.today():
        raise HTTPException(
            status_code=422,
            detail=(
                f"Không thể tạo bản ghi cho ngày tương lai ({payload.date}). "
                "Chỉ tạo được cho ngày đã qua hoặc hôm nay."
            ),
        )

    # 2. Nhân viên phải tồn tại.
    emp = db.query(crud.Employee).filter(crud.Employee.id == payload.employee_id).first()
    if not emp:
        raise HTTPException(
            status_code=404,
            detail=f"Không tìm thấy nhân viên id={payload.employee_id}",
        )

    # 3. Phải có ít nhất một giờ, nếu không bản ghi sẽ rỗng vô nghĩa.
    if payload.checkin is None and payload.checkout is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Phải nhập ít nhất một trong hai giờ (checkin hoặc checkout). "
                "Lưu ý: ngày thiếu một trong hai giờ sẽ không được tính công — "
                "nếu nhân viên đã làm đủ buổi thì nhập cả hai giờ."
            ),
        )

    # 4. Chặn trùng (employee, date) — dùng PATCH để sửa bản ghi đã có.
    existing_att = (
        db.query(Attendance)
        .filter(
            Attendance.employee_id == payload.employee_id,
            Attendance.date == payload.date,
        )
        .first()
    )
    if existing_att is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Nhân viên này đã có bản ghi chấm công ngày {payload.date} "
                f"(id={existing_att.id}). Hãy sửa giờ trực tiếp trên bảng "
                f"thay vì tạo mới."
            ),
        )

    # 5. Ghi time_log (nguồn sự thật) cho từng phía được cung cấp.
    _set_one_time(db, payload.employee_id, payload.date, "checkin", payload.checkin)
    _set_one_time(db, payload.employee_id, payload.date, "checkout", payload.checkout)

    # 6. Đồng bộ bản ghi attendance (tạo mới vì chưa có row nào cho ngày này).
    db.flush()
    _upsert_attendance(db, payload.employee_id, payload.date)
    db.flush()

    att = (
        db.query(Attendance)
        .filter(
            Attendance.employee_id == payload.employee_id,
            Attendance.date == payload.date,
        )
        .first()
    )
    if att is None:
        # Không xảy ra với đường hỗ trợ này (đã chặn empty ở bước 3), nhưng
        # để phòng vệ khỏi trả về 500 với lỗi DB khó hiểu.
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Không tạo được bản ghi chấm công. Vui lòng thử lại.",
        )

    db.commit()
    db.refresh(att)

    log_action(
        db, actor="hidden", action="create_attendance",
        entity_type="attendance", entity_id=att.id,
        detail={
            "employee_id": payload.employee_id,
            "employee_code": emp.code,
            "date": str(payload.date),
            "after": {
                "checkin": str(att.checkin_time),
                "checkout": str(att.checkout_time),
            },
        },
    )

    return _build_flag_row(att, emp)


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
    return _build_flag_row(att, emp)


# Mount the attendance router under /api/hidden.
# Endpoints become:
#   GET   /api/hidden/attendance[?from=&to=]
#   POST  /api/hidden/attendance
#   PATCH /api/hidden/attendance/{id}/times
router.include_router(att_router)