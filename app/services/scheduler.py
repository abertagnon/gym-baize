import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import User
from app.services.booking import process_user
from app.config import settings

log = logging.getLogger("scheduler")
scheduler = AsyncIOScheduler()


async def run_booking_job():
    log.info("═" * 50)
    log.info("Scheduler: inizio ciclo prenotazioni")

    db: Session = SessionLocal()
    try:
        users = db.query(User).filter(
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


def start_scheduler():
    trigger = CronTrigger(hour=settings.SCHEDULER_CRON_HOUR, minute=settings.SCHEDULER_CRON_MINUTE)
    scheduler.add_job(run_booking_job, trigger=trigger, id="booking_job",
                      name="Auto-booking ShaggyOwl", replace_existing=True)
    scheduler.start()
    log.info(f"Scheduler avviato — prossima: {scheduler.get_job('booking_job').next_run_time}")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
