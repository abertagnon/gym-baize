import logging
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session, noload
from app.database import SessionLocal
from app.models import User
from app.models.booking_log import BookingLog
from app.services.booking import process_user
from app.config import settings

log = logging.getLogger("scheduler")
scheduler = AsyncIOScheduler()


async def run_booking_job():
    log.info("═" * 50)
    log.info("Scheduler: inizio ciclo prenotazioni")

    db: Session = SessionLocal()
    try:
        # noload(booking_logs) evita di caricare in RAM i log storici di ogni utente
        users = db.query(User).options(noload(User.booking_logs)).filter(
            User.is_active == True,
            User.shaggyowl_email.isnot(None),
            User.shaggyowl_password_encrypted.isnot(None),
        ).all()

        if not users:
            log.info("Nessun utente configurato")
            return

        total = 0
        for user in users:
            try:
                n = await process_user(user, db)
                total += n
                log.info(f"[{user.username}] → {n} prenotazioni")
            except Exception as e:
                log.error(f"[{user.username}] Errore: {e}", exc_info=True)

        log.info(f"Ciclo completato — totale: {total}")
    finally:
        db.close()


def cleanup_booking_logs(days: int | None = None) -> int:
    """Elimina i log più vecchi di `days` giorni. Ritorna il numero di record eliminati."""
    retention = days if days is not None else settings.LOG_RETENTION_DAYS
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention)
    db = SessionLocal()
    try:
        deleted = db.query(BookingLog).filter(BookingLog.created_at < cutoff).delete()
        db.commit()
        if deleted:
            log.info(f"Cleanup log: eliminati {deleted} record più vecchi di {retention} giorni")
        return deleted
    except Exception as e:
        db.rollback()
        log.error(f"Errore cleanup log: {e}", exc_info=True)
        return 0
    finally:
        db.close()


async def cleanup_memory_job():
    """Job periodico: pulizia sessioni scadute e rate limiter."""
    from app.core.session_manager import cleanup_expired_sessions
    from app.core.rate_limit import cleanup_old_attempts

    sessions_removed = await cleanup_expired_sessions()
    keys_removed = cleanup_old_attempts(max_age_seconds=3600)
    if sessions_removed or keys_removed:
        log.info(f"Cleanup memoria: {sessions_removed} sessioni, {keys_removed} IP rate-limit")


def start_scheduler():
    # Job principale prenotazioni
    trigger = CronTrigger(hour=settings.SCHEDULER_CRON_HOUR, minute=settings.SCHEDULER_CRON_MINUTE)
    scheduler.add_job(run_booking_job, trigger=trigger, id="booking_job",
                      name="Auto-booking ShaggyOwl", replace_existing=True)

    # Cleanup memoria ogni ora
    scheduler.add_job(cleanup_memory_job, "interval", hours=1,
                      id="cleanup_memory", name="Cleanup sessioni e rate-limit",
                      replace_existing=True)

    # Cleanup log prenotazioni ogni notte alle 03:00
    scheduler.add_job(cleanup_booking_logs, CronTrigger(hour=3, minute=0),
                      id="cleanup_logs", name="Cleanup booking logs",
                      replace_existing=True)

    scheduler.start()
    log.info(f"Scheduler avviato — prossima prenotazione: {scheduler.get_job('booking_job').next_run_time}")

    # Pulizia log al primo avvio
    cleanup_booking_logs()


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
