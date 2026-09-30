"""Helpers for writing to `time_log` and keeping `attendance` in sync.

The new model is:
  - `time_log` is the source of truth (append-only, one row per action).
  - `attendance` is a denormalised summary (MIN(checkin), MAX(checkout))
    computed from `time_log` and updated on every checkin/checkout.

All callers (kiosk checkin/checkout, admin edits via /hidden) must go
through these helpers instead of writing to `time_log` or `attendance`
directly so the two tables stay consistent.
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from sqlalchemy.orm import Session

from app.models import TimeLog, Attendance


def get_action_times(db: Session, employee_id: int, target_date: date) -> tuple[Optional[datetime], Optional[datetime]]:
    """Return (checkin_time, checkout_time) for (employee, date).

    Source of truth = time_log. Returns (None, None) if no rows exist.
    """
    rows = (
        db.query(TimeLog)
        .filter(TimeLog.employee_id == employee_id, TimeLog.date == target_date)
        .all()
    )
    checkin = None
    checkout = None
    for r in rows:
        if r.action == "checkin":
            # Use the EARLIEST checkin (employee might checkin again by mistake
            # after forgetting; we keep the original entry).
            if checkin is None or r.time_value < checkin:
                checkin = r.time_value
        elif r.action == "checkout":
            # Use the LATEST checkout.
            if checkout is None or r.time_value > checkout:
                checkout = r.time_value
    return checkin, checkout


def record_checkin(
    db: Session,
    employee_id: int,
    target_date: date,
    time_value: datetime,
    device_id: Optional[str] = None,
    client_ip: Optional[str] = None,
    actor: str = "kiosk",
) -> TimeLog:
    """Insert a checkin row into time_log.

    Uses UPSERT semantics keyed on (employee_id, date, action='checkin')
    so a retry / double-tap never creates duplicate rows.
    """
    existing = (
        db.query(TimeLog)
        .filter(
            TimeLog.employee_id == employee_id,
            TimeLog.date == target_date,
            TimeLog.action == "checkin",
        )
        .first()
    )
    if existing:
        # Update the existing checkin row (e.g. admin retried with a new device).
        existing.time_value = time_value
        existing.device_id = device_id or existing.device_id
        existing.client_ip = client_ip or existing.client_ip
        existing.is_manual = (actor != "kiosk")
        existing.actor = actor
        db.flush()
        _upsert_attendance(db, employee_id, target_date)
        return existing

    row = TimeLog(
        employee_id=employee_id,
        date=target_date,
        action="checkin",
        time_value=time_value,
        device_id=device_id,
        client_ip=client_ip,
        is_manual=(actor != "kiosk"),
        actor=actor,
    )
    db.add(row)
    db.flush()
    _upsert_attendance(db, employee_id, target_date)
    return row


def record_checkout(
    db: Session,
    employee_id: int,
    target_date: date,
    time_value: datetime,
    device_id: Optional[str] = None,
    client_ip: Optional[str] = None,
    actor: str = "kiosk",
) -> TimeLog:
    """Insert / update a checkout row in time_log."""
    existing = (
        db.query(TimeLog)
        .filter(
            TimeLog.employee_id == employee_id,
            TimeLog.date == target_date,
            TimeLog.action == "checkout",
        )
        .first()
    )
    if existing:
        existing.time_value = time_value
        existing.device_id = device_id or existing.device_id
        existing.client_ip = client_ip or existing.client_ip
        existing.is_manual = (actor != "kiosk")
        existing.actor = actor
        db.flush()
        _upsert_attendance(db, employee_id, target_date)
        return existing

    row = TimeLog(
        employee_id=employee_id,
        date=target_date,
        action="checkout",
        time_value=time_value,
        device_id=device_id,
        client_ip=client_ip,
        is_manual=(actor != "kiosk"),
        actor=actor,
    )
    db.add(row)
    db.flush()
    _upsert_attendance(db, employee_id, target_date)
    return row


def _upsert_attendance(db: Session, employee_id: int, target_date: date) -> None:
    """Sync the `attendance` summary row from time_log.

    Creates the row if missing. Updates checkin_time/checkout_time to
    the latest derived values. Preserves other attendance columns
    (is_on_time, is_early_leave, etc.) by NOT touching them.
    """
    checkin_t, checkout_t = get_action_times(db, employee_id, target_date)

    att = (
        db.query(Attendance)
        .filter(Attendance.employee_id == employee_id, Attendance.date == target_date)
        .first()
    )
    if att is None:
        att = Attendance(
            employee_id=employee_id,
            date=target_date,
            checkin_time=checkin_t,
            checkout_time=checkout_t,
        )
        db.add(att)
        return

    att.checkin_time = checkin_t
    att.checkout_time = checkout_t


def list_for_employee_date(
    db: Session, employee_id: int, target_date: date
) -> list[TimeLog]:
    """Return all time_log rows for (employee_id, date), ordered by action."""
    return (
        db.query(TimeLog)
        .filter(TimeLog.employee_id == employee_id, TimeLog.date == target_date)
        .order_by(TimeLog.action.asc(), TimeLog.time_value.asc())
        .all()
    )


def replace_times(
    db: Session,
    employee_id: int,
    target_date: date,
    checkin_time: Optional[datetime],
    checkout_time: Optional[datetime],
    actor: str = "hidden",
) -> tuple[Optional[TimeLog], Optional[TimeLog]]:
    """Admin edit: replace the checkin/checkout rows for (employee, date).

    Pass None for either side to leave that action untouched.
    Returns the (checkin_row, checkout_row) after the operation.
    """
    checkin_row = None
    checkout_row = None

    if checkin_time is not None:
        checkin_row = record_checkin(
            db, employee_id, target_date, checkin_time, actor=actor
        )
    if checkout_time is not None:
        checkout_row = record_checkout(
            db, employee_id, target_date, checkout_time, actor=actor
        )

    # Sync attendance summary even if only one of the two was supplied.
    _upsert_attendance(db, employee_id, target_date)
    return checkin_row, checkout_row
