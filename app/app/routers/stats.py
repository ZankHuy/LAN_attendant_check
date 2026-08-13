from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app import crud
from app.schemas import SheetData

router = APIRouter(prefix="/api/stats", tags=["stats"])

@router.get("/sheet", response_model=SheetData)
def get_sheet_data(
    year: int = Query(default=None),
    month: int = Query(default=None),
    db: Session = Depends(get_db)
):
    """Lấy dữ liệu bảng chấm công theo kỳ (26 tháng trước - 25 tháng hiện tại)"""
    from datetime import date
    
    today = date.today()
    if year is None:
        year = today.year
    if month is None:
        month = today.month
    
    return crud.get_sheet_data(db, year, month)
