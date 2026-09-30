from fastapi import APIRouter, Depends, HTTPException, status, Query, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.database import get_db
from app import crud
from app.schemas import (
    CheckinRequest,
    CheckoutRequest,
    AttendanceResponse,
    AttendanceStatus,
    DeviceBanStatus,
    SettingUpdate,
    AttendanceSettingsResponse,
)
from datetime import date
from app import ratelimit

router = APIRouter(prefix="/api/attendance", tags=["attendance"])


def _build_response(db: Session, attendance) -> AttendanceResponse:
    employee = crud.get_employee(db, attendance.employee_id)
    shift = attendance.shift or ("AM" if (
        attendance.checkin_time.time() <= crud.AM_CHECKIN_LATEST
    ) else "PM")
    late_minutes = (
        crud.compute_late_minutes(attendance.checkin_time, shift, db, attendance.date)
        if attendance.checkin_time else 0
    )
    return AttendanceResponse(
        id=attendance.id,
        employee_id=attendance.employee_id,
        employee_name=employee.name if employee else "",
        employee_code=employee.code if employee else "",
        date=attendance.date,
        checkin_time=attendance.checkin_time,
        checkout_time=attendance.checkout_time,
        is_on_time=attendance.is_on_time,
        is_early_leave=attendance.is_early_leave,
        late_minutes=late_minutes,
        shift=attendance.shift,
        is_full_day=attendance.is_full_day,
        early_leave_minutes=attendance.early_leave_minutes,
    )


@router.post("/checkin", response_model=AttendanceResponse)
def checkin_endpoint(
    request: Request,
    response: Response,
    data: CheckinRequest,
    db: Session = Depends(get_db),
):
    """Checkin - identifier is employee_id, device_id only used for temporary ban."""
    client_ip = request.client.host if request.client else "unknown"
    allowed, retry_after = ratelimit.check_and_record(client_ip, data.employee_id)
    if not allowed:
        response.headers["Retry-After"] = str(retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Quá nhiều yêu cầu. Thử lại sau {retry_after}s.",
        )
    try:
        attendance = crud.checkin(
            db,
            data.employee_id,
            data.device_id,
            client_ip=client_ip,
        )
        return _build_response(db, attendance)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bản ghi chấm công cho nhân viên này hôm nay đã tồn tại.",
        )


@router.post("/checkout", response_model=AttendanceResponse)
def checkout_endpoint(
    request: Request,
    response: Response,
    data: CheckoutRequest,
    db: Session = Depends(get_db),
):
    """Checkout - the device used here does not have to match the checkin device.
    The checkout device will be banned for future checkouts only."""
    client_ip = request.client.host if request.client else "unknown"
    allowed, retry_after = ratelimit.check_and_record(client_ip, data.employee_id)
    if not allowed:
        response.headers["Retry-After"] = str(retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Quá nhiều yêu cầu. Thử lại sau {retry_after}s.",
        )
    try:
        attendance = crud.checkout(
            db,
            data.employee_id,
            data.device_id,
            client_ip=client_ip,
        )
        return _build_response(db, attendance)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Xung đột dữ liệu chấm công.",
        )


@router.get("/today/{device_id}", response_model=AttendanceStatus)
def get_today_status(device_id: str, db: Session = Depends(get_db)):
    """Get today's attendance for the device that performed the checkin.

    Note: only the device that did the checkin can read this status. Other
    devices (e.g. ones that performed checkout) won't see anything here.
    """
    return crud.get_attendance_status_for_device(db, device_id)


@router.get("/ban-status/{device_id}", response_model=DeviceBanStatus)
def get_ban_status(
    device_id: str,
    action_type: str = Query(..., pattern="^(checkin|checkout)$"),
    db: Session = Depends(get_db),
):
    """Whether the device is currently banned for the given action."""
    return crud.get_ban_status(db, device_id, action_type)


@router.get("/settings", response_model=AttendanceSettingsResponse)
def get_attendance_settings(db: Session = Depends(get_db)):
    """Get current shift deadline settings."""
    return AttendanceSettingsResponse(
        am_checkin_deadline=crud.get_setting(db, "am_checkin_deadline", "08:30"),
        pm_checkout_deadline=crud.get_setting(db, "pm_checkout_deadline", "18:00"),
        am_checkin_deadline_date=crud.get_setting(db, "am_checkin_deadline_date", ""),
        pm_checkout_deadline_date=crud.get_setting(db, "pm_checkout_deadline_date", ""),
    )


@router.post("/settings")
def update_attendance_settings(data: SettingUpdate, db: Session = Depends(get_db)):
    """Update shift deadline settings. They take effect from today onwards."""
    today_str = date.today().isoformat()
    crud.save_setting(db, "am_checkin_deadline", data.am_checkin_deadline)
    crud.save_setting(db, "am_checkin_deadline_date", today_str)
    crud.save_setting(db, "pm_checkout_deadline", data.pm_checkout_deadline)
    crud.save_setting(db, "pm_checkout_deadline_date", today_str)
    return {"ok": True}