"""Helpers for managing holidays / paid-leave days.

`Holiday` records override the normal work-day calculation:
  - T2-T6: 1.0 cong
  - T7:    0.5 cong
  - CN:    khong tinh
"""
from __future__ import annotations

from datetime import date
from typing import Optional, List

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models import Holiday


def list_holidays(
    db: Session,
    year: Optional[int] = None,
    month: Optional[int] = None,
) -> List[Holiday]:
    """List holidays. If year+month given, scope to the pay-period (26 prev - 25 curr)."""
    q = db.query(Holiday)
    if year is not None and month is not None:
        if month == 1:
            period_start = date(year - 1, 12, 26)
            period_end = date(year, 1, 25)
        else:
            period_start = date(year, month - 1, 26)
            period_end = date(year, month, 25)
        q = q.filter(Holiday.date >= period_start, Holiday.date <= period_end)
    return q.order_by(Holiday.date.desc(), Holiday.id.desc()).all()


def add_holiday(
    db: Session,
    target_date: date,
    kind: str,
    label: Optional[str] = None,
    scope: str = "all",
    employee_id: Optional[int] = None,
    actor: str = "admin",
) -> Holiday:
    """Insert a holiday. Raises ValueError on invalid input.

    Returns the existing row if a duplicate (same date+scope+employee) is found
    (idempotent).
    """
    if kind not in ("L", "P"):
        raise ValueError("kind phải là 'L' (lễ) hoặc 'P' (phép)")
    if scope not in ("all", "employee"):
        raise ValueError("scope phải là 'all' hoặc 'employee'")
    if scope == "employee" and employee_id is None:
        raise ValueError("scope='employee' cần employee_id")

    # Check duplicate.
    existing = (
        db.query(Holiday)
        .filter(
            Holiday.date == target_date,
            Holiday.scope == scope,
            Holiday.employee_id == employee_id,
        )
        .first()
    )
    if existing:
        return existing

    row = Holiday(
        date=target_date,
        kind=kind,
        label=label,
        scope=scope,
        employee_id=employee_id,
        created_by=actor,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Race: another process inserted the same row. Re-fetch.
        existing = (
            db.query(Holiday)
            .filter(
                Holiday.date == target_date,
                Holiday.scope == scope,
                Holiday.employee_id == employee_id,
            )
            .first()
        )
        if existing:
            return existing
        raise
    db.refresh(row)
    return row


def delete_holiday(db: Session, holiday_id: int) -> bool:
    row = db.query(Holiday).filter(Holiday.id == holiday_id).first()
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True
