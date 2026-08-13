from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app import crud
from app.schemas import SettingResponse, BanDurationUpdate
from app.routers.auth import _require_session

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/ban-duration", response_model=SettingResponse)
def get_ban_duration(db: Session = Depends(get_db)):
    """Public: current ban duration in minutes (used by the UI)."""
    minutes = crud.get_ban_minutes(db)
    return SettingResponse(key="ban_minutes", value=str(minutes))


@router.put("/ban-duration", response_model=SettingResponse)
def update_ban_duration(
    data: BanDurationUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(_require_session),
):
    """Admin only: update ban duration in minutes."""
    try:
        minutes = crud.set_ban_minutes(db, data.duration_minutes)
        return SettingResponse(key="ban_minutes", value=str(minutes))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))