"""Admin: gestione utenti, codici invito, trigger manuale."""

import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, InviteCode
from app.api.deps import get_admin_user
from app.services.scheduler import run_booking_job

router = APIRouter(prefix="/api/admin", tags=["admin"])

INVITE_EXPIRY_MINUTES = 20


class UserSummary(BaseModel):
    id: int
    username: str
    is_active: bool
    is_admin: bool
    has_shaggyowl: bool
    rules_count: int


class InviteResponse(BaseModel):
    code: str
    expires_at: str
    expires_in_minutes: int


class InviteListItem(BaseModel):
    id: int
    code: str
    is_used: bool
    is_expired: bool
    is_valid: bool
    created_at: str
    expires_at: str
    used_by_username: str | None


# ─── Trigger booking ────────────────────────────────────────────────────────

@router.post("/trigger-booking")
async def trigger_booking(admin: User = Depends(get_admin_user)):
    await run_booking_job()
    return {"ok": True, "message": "Ciclo completato"}


# ─── Utenti ─────────────────────────────────────────────────────────────────

@router.get("/users", response_model=list[UserSummary])
async def list_users(admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.created_at).all()
    return [
        UserSummary(
            id=u.id, username=u.username,
            is_active=u.is_active, is_admin=u.is_admin,
            has_shaggyowl=bool(u.shaggyowl_email), rules_count=len(u.rules),
        )
        for u in users
    ]


@router.put("/users/{user_id}/toggle-active")
async def toggle_user_active(user_id: int, admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Non puoi disattivare te stesso")
    user.is_active = not user.is_active
    db.commit()
    return {"ok": True, "is_active": user.is_active}


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: int, admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Non puoi eliminare te stesso")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="Non puoi eliminare un admin")
    db.delete(user)
    db.commit()


# ─── Codici invito ──────────────────────────────────────────────────────────

@router.post("/invites", response_model=InviteResponse)
async def create_invite(admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    code = secrets.token_hex(4).upper()  # 8 caratteri alfanumerici
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=INVITE_EXPIRY_MINUTES)

    invite = InviteCode(code=code, created_by=admin.id, expires_at=expires_at)
    db.add(invite)
    db.commit()
    db.refresh(invite)

    return InviteResponse(
        code=invite.code,
        expires_at=invite.expires_at.isoformat(),
        expires_in_minutes=INVITE_EXPIRY_MINUTES,
    )


@router.get("/invites", response_model=list[InviteListItem])
async def list_invites(admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    invites = db.query(InviteCode).order_by(InviteCode.created_at.desc()).limit(20).all()
    result = []
    for inv in invites:
        used_by_username = None
        if inv.used_by:
            u = db.query(User).filter(User.id == inv.used_by).first()
            used_by_username = u.username if u else None
        result.append(InviteListItem(
            id=inv.id, code=inv.code,
            is_used=inv.is_used, is_expired=inv.is_expired, is_valid=inv.is_valid,
            created_at=inv.created_at.isoformat(),
            expires_at=inv.expires_at.isoformat(),
            used_by_username=used_by_username,
        ))
    return result


@router.delete("/invites/{invite_id}", status_code=204)
async def revoke_invite(invite_id: int, admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    invite = db.query(InviteCode).filter(InviteCode.id == invite_id).first()
    if not invite:
        raise HTTPException(status_code=404, detail="Codice non trovato")
    if invite.is_used:
        raise HTTPException(status_code=400, detail="Codice già utilizzato, non revocabile")
    db.delete(invite)
    db.commit()
