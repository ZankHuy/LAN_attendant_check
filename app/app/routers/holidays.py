"""Holidays router: admin-only endpoints to manage nghi le / nghi phep."""
from datetime import date
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app import crud, holidays
from app.models import Holiday
from app.audit import log_action
from app.routers.auth import _require_session

router = APIRouter(prefix="/api/holidays", tags=["holidays"])


class HolidayCreate(BaseModel):
    date: date
    kind: str              # 'L' (le) | 'P' (phep)
    label: Optional[str] = None
    scope: str             # 'all' | 'employee'
    employee_id: Optional[int] = None

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, v: str) -> str:
        if v not in ("L", "P"):
            raise ValueError("kind phai la 'L' hoac 'P'")
        return v

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, v: str) -> str:
        if v not in ("all", "employee"):
            raise ValueError("scope phai la 'all' hoac 'employee'")
        return v


class HolidayResponse(BaseModel):
    id: int
    date: date
    kind: str
    label: Optional[str]
    scope: str
    employee_id: Optional[int]
    employee_name: Optional[str] = None
    created_at: str
    created_by: str

    class Config:
        from_attributes = True


@router.get("", response_model=List[HolidayResponse])
def list_holidays(
    year: Optional[int] = Query(default=None),
    month: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    _: str = Depends(_require_session),
):
    rows = holidays.list_holidays(db, year=year, month=month)
    result: List[HolidayResponse] = []
    for h in rows:
        emp_name = None
        if h.employee_id:
            emp = crud.get_employee(db, h.employee_id)
            emp_name = emp.name if emp else None
        result.append(HolidayResponse(
            id=h.id,
            date=h.date,
            kind=h.kind,
            label=h.label,
            scope=h.scope,
            employee_id=h.employee_id,
            employee_name=emp_name,
            created_at=h.created_at.isoformat() if h.created_at else "",
            created_by=h.created_by,
        ))
    return result


@router.post("", response_model=HolidayResponse)
def create_holiday(
    payload: HolidayCreate,
    db: Session = Depends(get_db),
    _: str = Depends(_require_session),
):
    if payload.scope == "employee":
        if payload.employee_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="scope='employee' can employee_id",
            )
        emp = crud.get_employee(db, payload.employee_id)
        if not emp:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Khong tim thay nhan vien id={payload.employee_id}",
            )
    try:
        row = holidays.add_holiday(
            db,
            target_date=payload.date,
            kind=payload.kind,
            label=payload.label,
            scope=payload.scope,
            employee_id=payload.employee_id,
            actor="admin",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    log_action(
        db, actor="admin", action="create", entity_type="holiday",
        entity_id=row.id,
        detail={
            "date": str(payload.date),
            "kind": payload.kind,
            "scope": payload.scope,
            "employee_id": payload.employee_id,
            "label": payload.label,
        },
    )

    emp_name = None
    if row.employee_id:
        emp = crud.get_employee(db, row.employee_id)
        emp_name = emp.name if emp else None
    return HolidayResponse(
        id=row.id,
        date=row.date,
        kind=row.kind,
        label=row.label,
        scope=row.scope,
        employee_id=row.employee_id,
        employee_name=emp_name,
        created_at=row.created_at.isoformat() if row.created_at else "",
        created_by=row.created_by,
    )


@router.delete("/{holiday_id}")
def delete_holiday(
    holiday_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(_require_session),
):
    row = Holiday
    h = db.query(row).filter(row.id == holiday_id).first()
    if not h:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Khong tim thay holiday id={holiday_id}",
        )
    snapshot = {"date": str(h.date), "kind": h.kind, "scope": h.scope,
                "employee_id": h.employee_id}
    if not holidays.delete_holiday(db, holiday_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Khong tim thay holiday id={holiday_id}",
        )
    log_action(
        db, actor="admin", action="delete", entity_type="holiday",
        entity_id=holiday_id, detail=snapshot,
    )
    return {"message": f"Da xoa holiday id={holiday_id}"}
