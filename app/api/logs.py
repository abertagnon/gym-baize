from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, BookingLog
from app.api.deps import get_current_user

router = APIRouter(prefix="/api/logs", tags=["logs"])


class LogResponse(BaseModel):
    id: int
    course_name: str
    course_date: str
    course_time: str
    status: str
    message: str | None
    created_at: str


@router.get("/", response_model=list[LogResponse])
async def list_logs(
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    logs = (
        db.query(BookingLog).filter(BookingLog.user_id == user.id)
        .order_by(BookingLog.created_at.desc()).limit(limit).all()
    )
    return [
        LogResponse(
            id=l.id, course_name=l.course_name, course_date=l.course_date,
            course_time=l.course_time, status=l.status, message=l.message,
            created_at=l.created_at.isoformat() if l.created_at else "",
        )
        for l in logs
    ]
