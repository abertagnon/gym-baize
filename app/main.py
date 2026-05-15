"""Entry point dell'applicazione FastAPI."""

import gc
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

# GC più aggressivo per ambienti con poca RAM
gc.set_threshold(300, 10, 10)
from app.config import settings
from app.database import init_db
from app.api import auth, rules, logs, courses, admin
from app.services.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Avvio applicazione...")
    init_db()
    start_scheduler()
    yield
    # Chiudi tutte le sessioni ShaggyOwl attive
    from app.core.session_manager import close_all
    await close_all()
    stop_scheduler()
    log.info("Applicazione terminata")


app = FastAPI(title="OpusGym Auto-Booker", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(rules.router)
app.include_router(logs.router)
app.include_router(courses.router)
app.include_router(admin.router)

FRONTEND_PATH = Path(__file__).parent.parent / "frontend" / "index.html"


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    if FRONTEND_PATH.exists():
        return FileResponse(FRONTEND_PATH)
    return HTMLResponse("<h1>Frontend non trovato</h1>")


@app.get("/api/health")
async def health():
    from app.database import SessionLocal
    from app.models import User
    db = SessionLocal()
    try:
        user_count = db.query(User).count()
    finally:
        db.close()
    return {"status": "ok", "needs_bootstrap": user_count == 0}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
