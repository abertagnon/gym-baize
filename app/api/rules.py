from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from datetime import date, datetime, timezone
from app.database import get_db
from app.models import User, BookingRule, BookingRuleBlackout, BookingDateRule
from app.api.deps import get_current_user
from app.core.security import encrypt_credential
from app.core.shaggyowl import ShaggyOwlClient, ShaggyOwlError

router = APIRouter(prefix="/api/rules", tags=["rules"])


class RuleCreate(BaseModel):
    course_name: str = Field(min_length=1, max_length=100)
    day_of_week: int = Field(ge=0, le=6)
    start_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    join_waitlist: bool = False
    is_active: bool = True


class RuleUpdate(BaseModel):
    course_name: str | None = Field(None, min_length=1, max_length=100)
    day_of_week: int | None = Field(None, ge=0, le=6)
    start_time: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    join_waitlist: bool | None = None
    is_active: bool | None = None


class RuleResponse(BaseModel):
    id: int
    course_name: str
    day_of_week: int
    day_name: str
    start_time: str
    join_waitlist: bool
    is_active: bool
    model_config = {"from_attributes": True}


class BlackoutCreate(BaseModel):
    start_date: date
    end_date: date


class BlackoutUpdate(BaseModel):
    start_date: date | None = None
    end_date: date | None = None


class BlackoutResponse(BaseModel):
    id: int
    start_date: str
    end_date: str


class DateRuleCreate(BaseModel):
    course_name: str = Field(min_length=1, max_length=100)
    course_date: date
    start_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    join_waitlist: bool = False
    is_active: bool = True


class DateRuleUpdate(BaseModel):
    course_name: str | None = Field(None, min_length=1, max_length=100)
    course_date: date | None = None
    start_time: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    join_waitlist: bool | None = None
    is_active: bool | None = None


class DateRuleResponse(BaseModel):
    id: int
    course_name: str
    course_date: str
    start_time: str
    join_waitlist: bool
    is_active: bool
    expired: bool


class ShaggyOwlCredentials(BaseModel):
    shaggyowl_email: str = Field(min_length=5, max_length=255)
    shaggyowl_password: str = Field(min_length=1, max_length=256)


class ProfileResponse(BaseModel):
    id: int
    username: str
    is_admin: bool
    has_shaggyowl: bool
    shaggyowl_email: str | None


@router.get("/profile", response_model=ProfileResponse)
async def get_profile(user: User = Depends(get_current_user)):
    return ProfileResponse(
        id=user.id, username=user.username,
        is_admin=user.is_admin, has_shaggyowl=bool(user.shaggyowl_email),
        shaggyowl_email=user.shaggyowl_email,
    )


@router.put("/shaggyowl-account")
async def set_shaggyowl_credentials(
    creds: ShaggyOwlCredentials,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.core.session_manager import replace_session

    try:
        encrypted_password = encrypt_credential(creds.shaggyowl_password)
    except ValueError:
        raise HTTPException(
            status_code=500,
            detail="ENCRYPTION_KEY non valida: genera una chiave Fernet e aggiornala nel file .env",
        )

    client = ShaggyOwlClient()
    session = None
    try:
        session = await client.login(creds.shaggyowl_email, creds.shaggyowl_password)
        await client.seleziona_sede(session)
    except ShaggyOwlError as e:
        if session:
            await client.logout(session)
        raise HTTPException(
            status_code=400,
            detail=f"Credenziali Opus Gym non valide o sessione non creabile: {e}",
        )
    except Exception:
        if session:
            await client.logout(session)
        raise HTTPException(
            status_code=502,
            detail="Impossibile verificare le credenziali Opus Gym. Riprova più tardi.",
        )

    user.shaggyowl_email = creds.shaggyowl_email
    user.shaggyowl_password_encrypted = encrypted_password
    try:
        db.commit()
    except Exception:
        db.rollback()
        await client.logout(session)
        raise

    await replace_session(user.id, session, client)

    return {"ok": True, "message": "Credenziali salvate e sessione attiva"}


@router.get("/", response_model=list[RuleResponse])
async def list_rules(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rules = db.query(BookingRule).filter(BookingRule.user_id == user.id).order_by(
        BookingRule.day_of_week, BookingRule.start_time).all()
    return [_to_response(r) for r in rules]


@router.post("/", response_model=RuleResponse, status_code=201)
async def create_rule(data: RuleCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    existing = db.query(BookingRule).filter(
        BookingRule.user_id == user.id, BookingRule.course_name == data.course_name,
        BookingRule.day_of_week == data.day_of_week, BookingRule.start_time == data.start_time,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Regola già esistente")

    rule = BookingRule(user_id=user.id, **data.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return _to_response(rule)


@router.get("/blackouts", response_model=list[BlackoutResponse])
async def list_blackouts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    blackouts = db.query(BookingRuleBlackout).filter(BookingRuleBlackout.user_id == user.id).order_by(
        BookingRuleBlackout.start_date, BookingRuleBlackout.end_date).all()
    return [_to_blackout_response(b) for b in blackouts]


@router.post("/blackouts", response_model=BlackoutResponse, status_code=201)
async def create_blackout(data: BlackoutCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    start_date = data.start_date.isoformat()
    end_date = data.end_date.isoformat()
    _validate_blackout_range(start_date, end_date)
    _ensure_no_blackout_overlap(db, user.id, start_date, end_date)

    blackout = BookingRuleBlackout(user_id=user.id, start_date=start_date, end_date=end_date)
    db.add(blackout)
    db.commit()
    db.refresh(blackout)
    return _to_blackout_response(blackout)


@router.put("/blackouts/{blackout_id}", response_model=BlackoutResponse)
async def update_blackout(
    blackout_id: int,
    data: BlackoutUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    blackout = _get_user_blackout(db, user.id, blackout_id)
    payload = data.model_dump(exclude_unset=True)
    start_date = payload.get("start_date", date.fromisoformat(blackout.start_date)).isoformat()
    end_date = payload.get("end_date", date.fromisoformat(blackout.end_date)).isoformat()
    _validate_blackout_range(start_date, end_date)
    _ensure_no_blackout_overlap(db, user.id, start_date, end_date, exclude_id=blackout.id)

    blackout.start_date = start_date
    blackout.end_date = end_date
    db.commit()
    db.refresh(blackout)
    return _to_blackout_response(blackout)


@router.delete("/blackouts/{blackout_id}", status_code=204)
async def delete_blackout(blackout_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    blackout = _get_user_blackout(db, user.id, blackout_id)
    db.delete(blackout)
    db.commit()


@router.get("/date-rules", response_model=list[DateRuleResponse])
async def list_date_rules(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rules = db.query(BookingDateRule).filter(BookingDateRule.user_id == user.id).order_by(
        BookingDateRule.course_date, BookingDateRule.start_time).all()
    return [_to_date_rule_response(r) for r in rules]


@router.post("/date-rules", response_model=DateRuleResponse, status_code=201)
async def create_date_rule(data: DateRuleCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if data.course_date < date.today():
        raise HTTPException(status_code=400, detail="La data non può essere nel passato")
    course_date = data.course_date.isoformat()
    _ensure_no_date_rule_duplicate(db, user.id, data.course_name, course_date, data.start_time)

    rule = BookingDateRule(
        user_id=user.id, course_name=data.course_name, course_date=course_date,
        start_time=data.start_time, join_waitlist=data.join_waitlist, is_active=data.is_active,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return _to_date_rule_response(rule)


@router.put("/date-rules/{date_rule_id}", response_model=DateRuleResponse)
async def update_date_rule(
    date_rule_id: int,
    data: DateRuleUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rule = _get_user_date_rule(db, user.id, date_rule_id)
    payload = data.model_dump(exclude_unset=True)
    next_course_name = payload.get("course_name", rule.course_name)
    next_course_date = payload.get("course_date", date.fromisoformat(rule.course_date)).isoformat()
    next_start_time = payload.get("start_time", rule.start_time)
    if date.fromisoformat(next_course_date) < date.today():
        raise HTTPException(status_code=400, detail="La data non può essere nel passato")
    _ensure_no_date_rule_duplicate(
        db, user.id, next_course_name, next_course_date, next_start_time, exclude_id=rule.id,
    )

    for field, value in payload.items():
        setattr(rule, field, value.isoformat() if field == "course_date" else value)
    db.commit()
    db.refresh(rule)
    return _to_date_rule_response(rule)


@router.delete("/date-rules/{date_rule_id}", status_code=204)
async def delete_date_rule(date_rule_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rule = _get_user_date_rule(db, user.id, date_rule_id)
    db.delete(rule)
    db.commit()


@router.put("/{rule_id}", response_model=RuleResponse)
async def update_rule(rule_id: int, data: RuleUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rule = _get_user_rule(db, user.id, rule_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    db.commit()
    db.refresh(rule)
    return _to_response(rule)


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rule = _get_user_rule(db, user.id, rule_id)
    db.delete(rule)
    db.commit()


def _get_user_rule(db, user_id, rule_id):
    rule = db.query(BookingRule).filter(BookingRule.id == rule_id, BookingRule.user_id == user_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Regola non trovata")
    return rule


def _to_response(rule: BookingRule) -> RuleResponse:
    return RuleResponse(
        id=rule.id, course_name=rule.course_name, day_of_week=rule.day_of_week,
        day_name=rule.day_name, start_time=rule.start_time,
        join_waitlist=rule.join_waitlist, is_active=rule.is_active,
    )


def _get_user_blackout(db, user_id, blackout_id):
    blackout = db.query(BookingRuleBlackout).filter(
        BookingRuleBlackout.id == blackout_id, BookingRuleBlackout.user_id == user_id,
    ).first()
    if not blackout:
        raise HTTPException(status_code=404, detail="Sospensione non trovata")
    return blackout


def _get_user_date_rule(db, user_id, date_rule_id):
    rule = db.query(BookingDateRule).filter(
        BookingDateRule.id == date_rule_id, BookingDateRule.user_id == user_id,
    ).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Prenotazione giornaliera non trovata")
    return rule


def _validate_blackout_range(start_date: str, end_date: str):
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="La data fine deve essere uguale o successiva alla data inizio")


def _ensure_no_blackout_overlap(db, user_id: int, start_date: str, end_date: str, exclude_id: int | None = None):
    query = db.query(BookingRuleBlackout).filter(
        BookingRuleBlackout.user_id == user_id,
        BookingRuleBlackout.start_date <= end_date,
        BookingRuleBlackout.end_date >= start_date,
    )
    if exclude_id is not None:
        query = query.filter(BookingRuleBlackout.id != exclude_id)
    if query.first():
        raise HTTPException(status_code=409, detail="Periodo di sospensione sovrapposto a uno esistente")


def _ensure_no_date_rule_duplicate(
    db, user_id: int, course_name: str, course_date: str, start_time: str, exclude_id: int | None = None,
):
    query = db.query(BookingDateRule).filter(
        BookingDateRule.user_id == user_id,
        BookingDateRule.course_name == course_name,
        BookingDateRule.course_date == course_date,
        BookingDateRule.start_time == start_time,
    )
    if exclude_id is not None:
        query = query.filter(BookingDateRule.id != exclude_id)
    if query.first():
        raise HTTPException(status_code=409, detail="Prenotazione giornaliera già esistente")


def _to_blackout_response(blackout: BookingRuleBlackout) -> BlackoutResponse:
    return BlackoutResponse(id=blackout.id, start_date=blackout.start_date, end_date=blackout.end_date)


def _to_date_rule_response(rule: BookingDateRule) -> DateRuleResponse:
    today = datetime.now(timezone.utc).date().isoformat()
    return DateRuleResponse(
        id=rule.id, course_name=rule.course_name, course_date=rule.course_date,
        start_time=rule.start_time, join_waitlist=rule.join_waitlist,
        is_active=rule.is_active, expired=rule.course_date < today,
    )
