from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────────────────
    app_name: str = "DSS Backend"
    debug: bool = False
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:8080"])

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://dss:dss@localhost:5432/dss"

    # ── Security ─────────────────────────────────────────────────────────────
    # Override JWT_SECRET in production. The default is intentionally insecure
    # so misconfiguration is loud rather than silent.
    jwt_secret: str = "CHANGE-ME-IN-PRODUCTION"
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_seconds: int = 8 * 3600
    login_rate_max: int = 10
    login_rate_window_seconds: int = 300
    # Fernet key for at-rest encryption of NVR passwords. Must be a 32-byte
    # url-safe base64 string. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
    nvr_secret_key: str = "CHANGE-ME-32-BYTE-FERNET-KEY-IN-PRODUCTION="

    # ── MediaMTX ─────────────────────────────────────────────────────────────
    mediamtx_api_url: str = "http://localhost:9997"
    mediamtx_webrtc_url: str = "http://localhost:8889"
    mediamtx_hls_url: str = "http://localhost:8888"
    mediamtx_rtsp_url: str = "rtsp://localhost:8554"
    # When true, backend manages MediaMTX as a child process.
    # When false (e.g. inside docker-compose), MediaMTX runs separately.
    mediamtx_managed: bool = False
    mediamtx_bin: str = "mediamtx"
    mediamtx_config_path: str = "mediamtx.yml"

    # ── Source-on-demand timings ─────────────────────────────────────────────
    sub_start_timeout: str = "10s"
    sub_close_after: str = "30s"
    main_start_timeout: str = "20s"
    main_close_after: str = "60s"

    # ── Bootstrap ────────────────────────────────────────────────────────────
    # On first startup, create this user if no users exist. Operator must
    # change the password on first login.
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "admin"

    @property
    def project_root(self) -> Path:
        # backend/app/settings.py -> backend/ -> project_root
        return Path(__file__).resolve().parent.parent.parent


@lru_cache
def get_settings() -> Settings:
    return Settings()
