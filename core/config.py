import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str = "Bid/No-Bid Platform"
    app_version: str = "8.0"
    environment: str = os.getenv("APP_ENV", "production")
    database_url: str = os.getenv("DATABASE_URL", "").strip()
    jwt_secret: str = os.getenv("JWT_SECRET", os.getenv("ADMIN_TOKEN", "change-me"))
    jwt_algorithm: str = "HS256"
    jwt_exp_minutes: int = int(os.getenv("JWT_EXP_MINUTES", "720"))
    worker_poll_interval: float = float(os.getenv("WORKER_POLL_INTERVAL", "1.0"))

    @property
    def database_enabled(self) -> bool:
        return bool(self.database_url)


settings = Settings()
