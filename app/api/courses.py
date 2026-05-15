"""Courses API — usa la sessione ShaggyOwl persistente."""

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from datetime import date as _date, timedelta
from app.models import User
from app.api.deps import get_current_user
from app.core.session_manager import get_session

router = APIRouter(prefix="/api/courses", tags=["courses"])


class CourseSlot(BaseModel):
    id_orario: str
    nome_corso: str
    orario_inizio: str
    orario_fine: str
    posti_disponibili: int
    posti_occupati: int
    frase: str
    prenotabile: bool
    utente_prenotato: bool
    color: str


class DaySchedule(BaseModel):
    giorno: str
    nome_giorno: str
    corsi: list[CourseSlot]


class CourseOption(BaseModel):
    course_name: str
    day_of_week: int
    day_name: str
    start_time: str


GIORNI_NAMES = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]


async def _require_session(user: User):
    """Ottiene la sessione ShaggyOwl, 400 se credenziali mancanti."""
    if not user.shaggyowl_email or not user.shaggyowl_password_encrypted:
        raise HTTPException(status_code=400, detail="Credenziali Opus Gym non configurate")
    return await get_session(user.id, user.shaggyowl_email, user.shaggyowl_password_encrypted)


@router.get("/schedule", response_model=list[DaySchedule])
async def get_schedule(
    giorno: str = Query(default=None),
    user: User = Depends(get_current_user),
):
    if not user.shaggyowl_email or not user.shaggyowl_password_encrypted:
        return []

    session, client = await _require_session(user)
    if giorno is None:
        start_day = _date.today()
    else:
        try:
            start_day = _date.fromisoformat(giorno)
        except ValueError:
            raise HTTPException(status_code=400, detail="Parametro 'giorno' non valido (atteso YYYY-MM-DD)")

    days = []
    giorni_da_mostrare = 8
    for delta in range(giorni_da_mostrare):
        current_day = start_day + timedelta(days=delta)
        current_day_str = current_day.isoformat()
        orari = await client.get_palinsesto(session, current_day_str)

        corsi = []
        for o in orari:
            p = o["prenotazioni"]
            corsi.append(CourseSlot(
                id_orario=o["id_orario_palinsesto"],
                nome_corso=o["nome_corso"],
                orario_inizio=o["orario_inizio"],
                orario_fine=o["orario_fine"],
                posti_disponibili=int(p.get("numero_posti_disponibili", 0)),
                posti_occupati=int(p.get("numero_posti_occupati", 0)),
                frase=p.get("frase", ""),
                prenotabile=str(p.get("id_disponibilita", "0")) == "1",
                utente_prenotato=str(p.get("utente_prenotato", "0")) != "0",
                color=o.get("color_corso", "#666"),
            ))

        days.append(DaySchedule(
            giorno=current_day_str,
            nome_giorno=GIORNI_NAMES[current_day.weekday()],
            corsi=corsi,
        ))

    return days


@router.get("/options", response_model=list[CourseOption])
async def get_course_options(user: User = Depends(get_current_user)):
    """Combinazioni corso+giorno+orario deduplicate dal palinsesto."""
    if not user.shaggyowl_email or not user.shaggyowl_password_encrypted:
        return []

    session, client = await _require_session(user)
    giorno = _date.today().isoformat()

    result = await client._post("palinsesti", {
        "id_sede": session.id_sede,
        "codice_sessione": session.codice_sessione,
        "giorno": giorno,
    })

    seen = set()
    options = []
    for palinsesto in result["parametri"]["lista_risultati"]:
        for g in palinsesto["giorni"]:
            giorno_date = g.get("giorno", "")
            if not giorno_date:
                continue
            try:
                parts = giorno_date.split("-")
                dow = _date(int(parts[0]), int(parts[1]), int(parts[2])).weekday()
            except (ValueError, IndexError):
                continue

            for o in g["orari_giorno"]:
                name = o.get("nome_corso", "").strip()
                ora = o.get("orario_inizio", "").strip()
                if not name or not ora:
                    continue
                key = (name, dow, ora)
                if key not in seen:
                    seen.add(key)
                    options.append(CourseOption(
                        course_name=name, day_of_week=dow,
                        day_name=GIORNI_NAMES[dow], start_time=ora,
                    ))

    options.sort(key=lambda x: (x.course_name, x.day_of_week, x.start_time))
    return options
