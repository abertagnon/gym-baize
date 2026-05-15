"""Gestione sessioni ShaggyOwl persistenti per la durata della sessione web.

Mantiene un pool di sessioni ShaggyOwl in memoria, una per utente.
Le sessioni vengono create al login e riusate sia dal frontend che dallo scheduler.
"""

import asyncio
import logging
from datetime import datetime, timezone
from app.core.shaggyowl import ShaggyOwlClient, ShaggyOwlSession, ShaggyOwlError
from app.core.security import decrypt_credential
from app.config import settings

log = logging.getLogger("shaggyowl.sessions")

# Pool: user_id → (ShaggyOwlSession, ShaggyOwlClient, timestamp ultimo utilizzo)
_sessions: dict[int, tuple[ShaggyOwlSession, ShaggyOwlClient, datetime]] = {}
_lock = asyncio.Lock()


async def get_session(user_id: int, shaggyowl_email: str, shaggyowl_password_encrypted: str) -> tuple[ShaggyOwlSession, ShaggyOwlClient]:
    """Ritorna una sessione ShaggyOwl attiva per l'utente. Crea se non esiste o scaduta."""
    stale_entry = None
    ttl_seconds = settings.SESSION_TTL_HOURS * 3600

    async with _lock:
        if user_id in _sessions:
            session, client, ts = _sessions[user_id]
            if (datetime.now(timezone.utc) - ts).total_seconds() < ttl_seconds:
                return session, client
            # Sessione scaduta: rimuovi per ricrearla
            stale_entry = _sessions.pop(user_id)

    if stale_entry:
        asyncio.create_task(_safe_logout(stale_entry[1], stale_entry[0]))
        log.info(f"[user:{user_id}] Sessione scaduta — ricreazione in corso")

    # Fuori dal lock per non bloccare durante la rete
    client = ShaggyOwlClient()
    password = decrypt_credential(shaggyowl_password_encrypted)
    session = await client.login(shaggyowl_email, password)
    await client.seleziona_sede(session)
    log.info(f"[user:{user_id}] Sessione ShaggyOwl creata — {session.nome_utente}")

    # Double-check: un'altra coroutine potrebbe aver già creato la sessione mentre eravamo in rete
    duplicate_client = None
    duplicate_session = None
    async with _lock:
        if user_id in _sessions:
            duplicate_client, duplicate_session = client, session
            session, client, _ = _sessions[user_id]
        else:
            _sessions[user_id] = (session, client, datetime.now(timezone.utc))

    if duplicate_client is not None:
        try:
            await duplicate_client.logout(duplicate_session)
        except Exception:
            pass

    return session, client


async def _safe_logout(client: ShaggyOwlClient, session: ShaggyOwlSession):
    try:
        await client.logout(session)
    except Exception:
        pass


async def close_session(user_id: int):
    """Chiude e rimuove la sessione ShaggyOwl di un utente."""
    async with _lock:
        entry = _sessions.pop(user_id, None)

    if entry:
        session, client, _ = entry
        await client.logout(session)
        log.info(f"[user:{user_id}] Sessione ShaggyOwl chiusa")


async def replace_session(user_id: int, session: ShaggyOwlSession, client: ShaggyOwlClient):
    """Sostituisce la sessione in memoria con una sessione già validata."""
    async with _lock:
        old_entry = _sessions.pop(user_id, None)
        _sessions[user_id] = (session, client, datetime.now(timezone.utc))

    if old_entry:
        old_session, old_client, _ = old_entry
        try:
            await old_client.logout(old_session)
        except Exception:
            pass
    log.info(f"[user:{user_id}] Sessione ShaggyOwl sostituita — {session.nome_utente}")


async def cleanup_expired_sessions() -> int:
    """Rimuove le sessioni inattive da più di SESSION_TTL_HOURS ore. Ritorna il numero rimosso."""
    ttl_seconds = settings.SESSION_TTL_HOURS * 3600
    now = datetime.now(timezone.utc)
    async with _lock:
        expired_ids = [
            uid for uid, (_, _, ts) in _sessions.items()
            if (now - ts).total_seconds() >= ttl_seconds
        ]
        entries = {uid: _sessions.pop(uid) for uid in expired_ids}

    for user_id, (session, client, _) in entries.items():
        asyncio.create_task(_safe_logout(client, session))

    if entries:
        log.info(f"Cleanup sessioni: rimossi {len(entries)} sessioni scadute")
    return len(entries)


async def close_all():
    """Chiude tutte le sessioni (shutdown dell'app)."""
    async with _lock:
        entries = list(_sessions.items())
        _sessions.clear()

    for user_id, (session, client, _) in entries:
        try:
            await client.logout(session)
        except Exception:
            pass
    log.info(f"Chiuse {len(entries)} sessioni ShaggyOwl")


def has_session(user_id: int) -> bool:
    return user_id in _sessions
