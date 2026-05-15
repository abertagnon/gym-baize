import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.core.shaggyowl import ShaggyOwlError
from app.core.session_manager import get_session
from app.models import User, BookingRule, BookingLog, BookingDateRule, BookingRuleBlackout
from app.config import settings

log = logging.getLogger("booking.service")


def _match(orario: dict, target_day: int, rule: BookingRule) -> bool:
    if rule.course_name.lower() not in orario["nome_corso"].lower():
        return False
    if target_day != rule.day_of_week:
        return False
    if orario["orario_inizio"] != rule.start_time:
        return False
    return True


def _match_date_rule(orario: dict, rule: BookingDateRule) -> bool:
    if rule.course_name.lower() not in orario["nome_corso"].lower():
        return False
    if orario["orario_inizio"] != rule.start_time:
        return False
    return True


def _is_blackout(giorno: str, blackout: BookingRuleBlackout) -> bool:
    return blackout.start_date <= giorno <= blackout.end_date


def _is_bookable(orario: dict, join_waitlist: bool = False) -> tuple[bool, str]:
    pren = orario["prenotazioni"]
    if str(pren.get("utente_prenotato", "0")) != "0":
        return False, "already_booked"
    if str(pren.get("id_disponibilita", "0")) != "1":
        return False, "closed"
    if int(pren.get("numero_posti_disponibili", 0)) > 0:
        return True, f"{pren['numero_posti_disponibili']} posti"
    if join_waitlist and str(pren.get("prenota_coda", "1")) == "2":
        return True, "in coda"
    return False, "no_spots"


def _log_entry(db: Session, user_id: int, course: str, date: str, time: str, status: str, message: str):
    db.add(BookingLog(
        user_id=user_id, course_name=course, course_date=date,
        course_time=time, status=status, message=(message or "")[:500],
    ))
    db.commit()


async def process_user(user: User, db: Session) -> int:
    if not user.shaggyowl_email or not user.shaggyowl_password_encrypted:
        return 0

    active_rules = [r for r in user.rules if r.is_active]
    today = datetime.now(timezone.utc).date().isoformat()
    active_date_rules = [r for r in user.date_rules if r.is_active and r.course_date >= today]
    if not active_rules and not active_date_rules:
        return 0

    blackouts = list(user.rule_blackouts)
    booked = 0

    try:
        session, client = await get_session(user.id, user.shaggyowl_email, user.shaggyowl_password_encrypted)
        log.info(f"[{user.username}] Sessione OK")
    except Exception as e:
        log.error(f"[{user.username}] Login fallito: {e}")
        _log_entry(db, user.id, "LOGIN", datetime.now().strftime("%Y-%m-%d"), "--:--", "failed", str(e))
        return 0

    oggi = datetime.now(timezone.utc).date()
    # Scansiona fino allo stesso giorno della settimana successiva (8 giorni)
    giorni_da_controllare = 8
    for delta in range(giorni_da_controllare):
        giorno = oggi + timedelta(days=delta)
        giorno_str = giorno.strftime("%Y-%m-%d")
        weekday = giorno.weekday()

        if any(_is_blackout(giorno_str, blackout) for blackout in blackouts):
            log.info(f"  [{user.username}] {giorno_str} — regole sospese")
            continue

        rules_today = [r for r in active_rules if r.day_of_week == weekday]
        date_rules_today = [r for r in active_date_rules if r.course_date == giorno_str]
        if not rules_today and not date_rules_today:
            continue

        try:
            orari = await client.get_palinsesto(session, giorno_str)
        except ShaggyOwlError as e:
            log.error(f"[{user.username}] Palinsesto {giorno_str}: {e}")
            continue

        for orario in orari:
            matching_rules = [r for r in rules_today if _match(orario, weekday, r)]
            matching_rules.extend(r for r in date_rules_today if _match_date_rule(orario, r))
            if not matching_rules:
                continue

            corso = orario["nome_corso"]
            ora = orario["orario_inizio"]
            join_waitlist = any(r.join_waitlist for r in matching_rules)
            bookable, reason = _is_bookable(orario, join_waitlist)

            if reason == "already_booked":
                log.info(f"  [{user.username}] {giorno_str} {corso} {ora} — già prenotato")
                continue
            if not bookable:
                log.info(f"  [{user.username}] {giorno_str} {corso} {ora} — {reason}")
                _log_entry(db, user.id, corso, giorno_str, ora, "no_spots", reason)
                continue

            try:
                result = await client.prenota(session, giorno_str, orario["id_orario_palinsesto"])
                msg = result.get("messaggio", "OK")
                log.info(f"  [{user.username}] ✅ {giorno_str} {corso} {ora} — {msg}")
                _log_entry(db, user.id, corso, giorno_str, ora, "success", msg)
                booked += 1
            except ShaggyOwlError as e:
                log.error(f"  [{user.username}] ❌ {giorno_str} {corso} {ora} — {e}")
                _log_entry(db, user.id, corso, giorno_str, ora, "failed", str(e))

    return booked
