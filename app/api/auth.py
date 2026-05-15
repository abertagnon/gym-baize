"""Auth: login con username, registrazione con codice invito."""

import re
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, InviteCode
from app.core.security import hash_password, verify_password, create_access_token, decode_access_token, blacklist_token
from app.core.rate_limit import check_rate_limit

router = APIRouter(prefix="/api/auth", tags=["auth"])

USERNAME_RE = re.compile(r"^[a-z0-9]+$")


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=30)
    password: str = Field(min_length=8, max_length=128)
    invite_code: str = Field(min_length=1, max_length=20)

    @field_validator("username")
    @classmethod
    def validate_username(cls, v):
        v = v.lower().strip()
        if not USERNAME_RE.match(v):
            raise ValueError("Solo lettere minuscole e numeri")
        return v


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, v):
        return v.lower().strip()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


# ─── Bootstrap: primo utente (admin) senza codice ───────────────────────────

@router.post("/bootstrap", response_model=TokenResponse, status_code=201)
async def bootstrap(req: LoginRequest, db: Session = Depends(get_db)):
    """Crea il primo utente admin. Funziona solo se il DB è vuoto."""
    if db.query(User).count() > 0:
        raise HTTPException(status_code=403, detail="Admin già esistente")

    username = req.username.lower().strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(status_code=400, detail="Username: solo lettere minuscole e numeri")
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password: minimo 8 caratteri")

    user = User(username=username, hashed_password=hash_password(req.password), is_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenResponse(access_token=create_access_token(user.id, user.username), user=_user_dict(user))


# ─── Registrazione con codice invito ────────────────────────────────────────

@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(request: Request, req: RegisterRequest, db: Session = Depends(get_db)):
    if not check_rate_limit(f"register:{request.client.host}", max_requests=5, window_seconds=60):
        raise HTTPException(status_code=429, detail="Troppi tentativi. Riprova tra un minuto.")
    # Valida codice invito
    invite = db.query(InviteCode).filter(InviteCode.code == req.invite_code).first()
    if not invite:
        raise HTTPException(status_code=400, detail="Codice invito non valido")
    if not invite.is_valid:
        reason = "scaduto" if invite.is_expired else "già utilizzato"
        raise HTTPException(status_code=400, detail=f"Codice invito {reason}")

    # Verifica username disponibile
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(status_code=409, detail="Username già in uso")

    user = User(username=req.username, hashed_password=hash_password(req.password))
    db.add(user)
    db.flush()

    # Segna codice come usato
    invite.is_used = True
    invite.used_by = user.id
    db.commit()
    db.refresh(user)

    return TokenResponse(access_token=create_access_token(user.id, user.username), user=_user_dict(user))


# ─── Login ──────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def login(request: Request, req: LoginRequest, db: Session = Depends(get_db)):
    if not check_rate_limit(f"login:{request.client.host}", max_requests=10, window_seconds=60):
        raise HTTPException(status_code=429, detail="Troppi tentativi. Riprova tra un minuto.")
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Credenziali non valide")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disattivato")

    # Auto-apri sessione ShaggyOwl se credenziali configurate
    if user.shaggyowl_email and user.shaggyowl_password_encrypted:
        try:
            from app.core.session_manager import get_session
            await get_session(user.id, user.shaggyowl_email, user.shaggyowl_password_encrypted)
        except Exception:
            pass  # Non bloccare il login se ShaggyOwl è giù

    return TokenResponse(access_token=create_access_token(user.id, user.username), user=_user_dict(user))


@router.post("/logout")
async def logout_endpoint(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
    db: Session = Depends(get_db),
):
    payload = decode_access_token(credentials.credentials)
    if payload:
        jti = payload.get("jti")
        if jti:
            exp = payload.get("exp")
            expires_at = datetime.fromtimestamp(exp, tz=timezone.utc) if exp else datetime.now(timezone.utc)
            blacklist_token(jti, expires_at)
        user_id = int(payload["sub"])
        from app.core.session_manager import close_session
        await close_session(user_id)
    return {"ok": True}


def _user_dict(user: User) -> dict:
    return {
        "id": user.id, "username": user.username,
        "is_admin": user.is_admin, "has_shaggyowl": bool(user.shaggyowl_email),
    }
