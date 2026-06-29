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
    # relay = "go2rtc" (MSE, default) or "mediamtx" (legacy WebRTC). go2rtc's
    # buffered MSE pipeline absorbs bursty/jittery camera frame delivery that
    # freezes WebRTC at 0% packet loss — see docs/perf-tuning.md. The React
    # frontend speaks go2rtc/MSE only, so this is the default it expects.
    relay: str = "go2rtc"
    go2rtc_api_url: str = "http://localhost:1984"
    # Browser-facing base the frontend uses for the MSE/WebRTC WebSocket.
    go2rtc_ws_url: str = "ws://localhost:1984"

    # ── Anti-freeze re-encode relay ──────────────────────────────────────────
    # Cameras ship a ~2s GOP (keyframe interval); on any jitter the picture
    # freezes up to 2s waiting for the next keyframe. Re-encoding to a short
    # forced keyframe interval cuts recovery to a blink. This is THE thing that
    # made 4MP stable pre-redesign (was MediaMTX runOnDemand; here it's a go2rtc
    # `exec:ffmpeg` source). NOT the transport. On-demand → only streams being
    # viewed are encoded, so concurrency is bounded by viewers, not 34 channels.
    # On the server set REENCODE_ENABLED=true + REENCODE_VCODEC=h264_qsv (Intel
    # QuickSync iGPU). vcodec=libx264 is the portable CPU fallback (heavier).
    reencode_enabled: bool = False
    reencode_keyframe_seconds: float = 0.5
    reencode_qualities: str = "sub"  # "sub" | "main" | "both"
    # "auto" probes the host (real test-encode) and picks the best WORKING encoder:
    # h264_qsv → h264_nvenc → h264_vaapi → libx264 (CPU). A codec can be compiled
    # into ffmpeg yet fail at runtime when the GPU is absent (this box: no GPU →
    # auto resolves to libx264). Set an explicit codec to skip probing.
    reencode_vcodec: str = "auto"
    reencode_preset: str = "veryfast"
    reencode_ffmpeg_bin: str = "ffmpeg"
    # Cap the re-encoded bitrate (VBV: -maxrate/-bufsize). 0 = unconstrained CRF.
    # STRONGLY recommended for 4MP mains: forcing a 0.5s GOP on 4MP makes ~4× more
    # (big) I-frames than the camera's native 2s GOP, so an uncapped CRF stream
    # spikes hard and swamps the client network/decoder → cushion underrun → freeze.
    # ~6000 (6 Mbps) is a good start for 4MP; subs sit well under it so one value
    # is fine for both. bufsize is held to ~1s of maxrate to smooth the spikes.
    reencode_maxrate_kbps: int = 0
    # MAIN-only decode-load reducers. A growing buffer → forward jump → freeze on
    # the 4MP main is the CLIENT decoder failing to hold 25fps (decode cost scales
    # with pixels×fps, not bitrate — so the VBV cap alone won't fix it). Downscale
    # and/or drop fps to cut that load. Subs are untouched (already small).
    #   reencode_main_scale: ffmpeg scale, e.g. "1920:-2" (1080p, height auto-even),
    #                        "1280:-2" (720p). "" = keep source resolution.
    #   reencode_main_fps:   cap main fps, e.g. 15. 0 = source fps.
    # 4MP→1080p ≈ half the decode work; +15fps ≈ a third of the original.
    reencode_main_scale: str = ""
    reencode_main_fps: int = 0
    # RTSP transport for the CAMERA pull (the exec ffmpeg `-i` input). "tcp" is
    # reliable but on a lossy link a dropped packet stalls the reader (head-of-line
    # blocking) → the stream collapses to a few fps and freezes. "udp" tolerates
    # loss: lost packets become brief glitches instead of a stall, so the pull
    # holds ~realtime fps (measured: a link with 8% large-packet loss delivered
    # 4fps over TCP vs 23fps over UDP). The server re-encode then heals it into a
    # clean stream and the browser still receives reliable MSE/TCP. Only affects
    # re-encoded streams; the republish to go2rtc stays TCP.
    reencode_input_rtsp_transport: str = "tcp"  # "tcp" | "udp"
    # Direct MAIN streams aren't re-encoded (raw passthrough), so the transport
    # setting above doesn't reach them — they pull over go2rtc's native RTSP/TCP
    # client. On these Dahua cameras that collapses the 4MP main to ~2-7fps (weak
    # camera TCP stack: any loss → head-of-line block + tiny send window), while
    # the SAME camera delivers ~22fps over UDP (measured 2026-06-29, ch5/ch12).
    # When true, direct mains are pulled over RTSP/UDP and RE-ENCODED to a short GOP
    # into go2rtc via an MPEG-TS stdout pipe (full 4MP, no scale/fps cap). UDP fixes
    # the camera delivery (~22fps vs ~2fps over TCP); the pipe avoids the loopback
    # RTSP republish that throttled it; the re-encode conceals the camera segment's
    # ~2% UDP packet loss (a raw copy hands that corruption to the browser, where the
    # 2s camera GOP smears it). ~1 CPU core per viewed main. via-NVR mains stay raw.
    # See go2rtc_reencode.udp_pipe_source.
    main_pull_udp: bool = True
    # Target bitrate (VBV) for the UDP main re-encode. 8 Mbps keeps 4MP sharp; the
    # client link is LAN. 0 = uncapped (CRF).
    main_reencode_maxrate_kbps: int = 8000
    # go2rtc rejects exec:/ffmpeg: (subprocess) sources over its HTTP API
    # ("insecure producer"); they're only honoured from the static YAML. So when
    # re-encoding we write streams into this file and reload go2rtc instead of
    # PUT /api/streams. Path is relative to the process CWD (the repo root, where
    # start.ps1/start-mac.sh copy go2rtc.base.yaml → .go2rtc/go2rtc.yaml).
    go2rtc_config_path: str = ".go2rtc/go2rtc.yaml"

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
