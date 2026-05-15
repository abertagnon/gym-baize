from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    SECRET_KEY: str = "CAMBIAMI"
    ENCRYPTION_KEY: str = "CAMBIAMI"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7
    DATABASE_URL: str = "sqlite:///./data/shaggyowl.db"
    SCHEDULER_CRON_HOUR: int = 0
    SCHEDULER_CRON_MINUTE: int = 5
    BOOKING_DAYS_AHEAD: int = 7
    REGISTRATION_ENABLED: bool = True
    LOG_LEVEL: str = "INFO"
    SHAGGYOWL_BASE_URL: str = "https://app.shaggyowl.com/funzioniapp/v407"
    CORS_ORIGINS: str = "https://gym.baize.dev,http://127.0.0.1:3001,http://localhost:3001"
    LOG_RETENTION_DAYS: int = 90
    SESSION_TTL_HOURS: int = 24

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
Path(settings.DATABASE_URL.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
