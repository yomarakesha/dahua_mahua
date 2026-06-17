from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Sentinel defaults. The app refuses to start in production (debug=False) if any
# of these is left unchanged — a known JWT secret means anyone can forge an
# admin token, and a known Fernet key means stored NVR passwords are readable.
_DEFAULT_JWT_SECRET = "CHANGE-ME-IN-PRODUCTION"
_DEFAULT_NVR_SECRET_KEY = "CHANGE-ME-32-BYTE-FERNET-KEY-IN-PRODUCTION="
_DEFAULT_ADMIN_PASSWORD = "admin"


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
    # SQLite is the default so local dev works without installing Postgres.
    # For prod set DATABASE_URL=postgresql+asyncpg://dss:dss@host:5432/dss
    database_url: str = "sqlite+aiosqlite:///./dss.db"

    # ── Security ─────────────────────────────────────────────────────────────
    # Override JWT_SECRET in production. The default is intentionally insecure
    # so misconfiguration is loud rather than silent.
    jwt_secret: str = _DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_seconds: int = 8 * 3600
    login_rate_max: int = 10
    login_rate_window_seconds: int = 300
    # Fernet key for at-rest encryption of NVR passwords. Must be a 32-byte
    # url-safe base64 string. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
    nvr_secret_key: str = _DEFAULT_NVR_SECRET_KEY

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

    # ── go2rtc (buffered MSE relay) ──────────────────────────────────────────
    # relay = "mediamtx" (WebRTC) or "go2rtc" (MSE). go2rtc's buffered MSE
    # pipeline absorbs bursty/jittery camera frame delivery that freezes WebRTC
    # at 0% packet loss — see docs/perf-tuning.md.
    relay: str = "mediamtx"
    go2rtc_api_url: str = "http://localhost:1984"
    # Browser-facing base the frontend uses for the MSE/WebRTC WebSocket.
    go2rtc_ws_url: str = "ws://localhost:1984"

    # ── Source-on-demand timings ─────────────────────────────────────────────
    sub_start_timeout: str = "10s"
    sub_close_after: str = "30s"
    main_start_timeout: str = "20s"
    main_close_after: str = "60s"

    # ── Source watchdog ──────────────────────────────────────────────────────
    # Polls MediaMTX's runtime API and auto-disables an NVR whose source keeps
    # failing while a viewer is pulling it — before the camera firmware bans
    # our account for repeated failed RTSP auths. Disable only fires when the
    # NVR has NO working channel (so one offline camera won't kill the NVR).
    source_watch_enabled: bool = True
    source_watch_interval_seconds: float = 3.0
    # Consecutive failing polls before we pull the plug. 2 polls × 3s ≈ 6s,
    # which keeps us under the ~5-failed-auth threshold most Dahua firmwares
    # use before locking the account.
    source_watch_threshold: int = 2
    # Per-channel threshold: when the NVR otherwise streams fine but one channel
    # keeps failing (phantom channel that doesn't exist, or a camera that's
    # offline), disable just that channel after this many polls. More lenient
    # than the NVR-wide threshold so a brief blip on a real camera is tolerated.
    source_watch_camera_threshold: int = 4
    # A channel is only treated as "phantom/offline" (and auto-disabled) if it
    # has NOT streamed successfully within this window. A real camera that was
    # working seconds ago and then blips (ICE drop, packet loss, on-demand
    # source restart) must not be disabled — otherwise transient network loss
    # makes working cameras vanish from the grid.
    source_watch_camera_recovery_seconds: float = 180.0
    # Startup grace period. On a cold start the grid immediately pulls streams
    # while MediaMTX is still spinning up the on-demand RTSP sources, so for the
    # first few seconds every path is "active but not ready" — which looks
    # exactly like an auth failure to the watchdog and made it disable healthy
    # NVRs on every boot. During this window we poll but never disable, giving
    # sources time to connect.
    source_watch_startup_grace_seconds: float = 45.0

    # ── Bootstrap ────────────────────────────────────────────────────────────
    # On first startup, create this user if no users exist. Operator must
    # change the password on first login.
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = _DEFAULT_ADMIN_PASSWORD

    @property
    def project_root(self) -> Path:
        # backend/app/settings.py -> backend/ -> project_root
        return Path(__file__).resolve().parent.parent.parent

    def validate_production(self) -> None:
        """Fail loudly at startup if security-critical secrets are left at
        their insecure defaults while running with debug=False. In debug mode
        the defaults are tolerated so local dev works out of the box.

        Validates that a configured (non-default) Fernet key is well-formed so
        a typo fails at boot rather than on the first NVR password encrypt. The
        default sentinel is left alone in debug — local dev that never touches
        NVR creds shouldn't be blocked by it.
        """
        from cryptography.fernet import Fernet

        if not self.debug:
            insecure: list[str] = []
            if self.jwt_secret == _DEFAULT_JWT_SECRET:
                insecure.append("JWT_SECRET")
            if self.nvr_secret_key == _DEFAULT_NVR_SECRET_KEY:
                insecure.append("NVR_SECRET_KEY")
            if self.bootstrap_admin_password == _DEFAULT_ADMIN_PASSWORD:
                insecure.append("BOOTSTRAP_ADMIN_PASSWORD")
            if insecure:
                raise RuntimeError(
                    "Refusing to start with insecure default(s): "
                    + ", ".join(insecure)
                    + ". Set them via environment / .env (set DEBUG=true to bypass "
                    "for local development)."
                )

        if self.nvr_secret_key != _DEFAULT_NVR_SECRET_KEY:
            try:
                Fernet(self.nvr_secret_key.encode())
            except (ValueError, TypeError) as e:
                raise RuntimeError(
                    "NVR_SECRET_KEY is not a valid Fernet key. Generate one with: "
                    'python -c "from cryptography.fernet import Fernet; '
                    'print(Fernet.generate_key().decode())"'
                ) from e


@lru_cache
def get_settings() -> Settings:
    return Settings()
