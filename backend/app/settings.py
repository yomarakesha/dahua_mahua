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

    # ── Branding / white-label ───────────────────────────────────────────────
    # The product brand is configurable per deployment AT RUNTIME (no rebuild):
    # the frontend fetches GET /api/v1/branding (unauthenticated) at boot and
    # themes itself from these values. The DEFAULTS reproduce today's look
    # exactly ("Kanagatly VMS" / "KM" / the green accent), so an un-set deploy is
    # visually unchanged. Override per deploy: BRAND_NAME, BRAND_SHORT,
    # BRAND_PRIMARY, BRAND_ACCENT, BRAND_LOGO_URL.
    #   brand_name    — full product name (header + login + document title).
    #   brand_short   — short mark for the logo circle (used when no logo image).
    #   brand_primary — primary accent as a #rrggbb hex (the green today). Themes
    #                   the logo, active nav, primary buttons, focus rings, etc.
    #   brand_accent  — secondary accent hex (the lighter green today); used for
    #                   active-nav text and other "accent-light" spots.
    #   brand_logo_url— optional image URL for the logo + favicon. Empty → the
    #                   built-in short-mark circle is drawn instead.
    brand_name: str = "Kanagatly VMS"
    brand_short: str = "KM"
    brand_primary: str = "#2ecc71"
    brand_accent: str = "#43e088"
    brand_logo_url: str = ""

    # ── Logging ──────────────────────────────────────────────────────────────
    # On NSSM (Windows service) stderr is not persisted, so add a rotating file
    # handler alongside the stream handler. Empty string disables the file
    # handler (stderr only). The parent dir is created if missing; if it can't
    # be written, startup logs a warning and continues with stderr only.
    log_file: str = "logs/backend.log"
    log_file_max_bytes: int = 10_485_760  # 10 MiB
    log_file_backup_count: int = 5

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

    # ── go2rtc (buffered MSE relay) ──────────────────────────────────────────
    # go2rtc is the sole relay: its buffered MSE pipeline absorbs bursty/jittery
    # camera frame delivery that freezes plain WebRTC at 0% packet loss (see
    # docs/perf-tuning.md), and it also serves WebRTC for the fullscreen main.
    go2rtc_api_url: str = "http://localhost:1984"
    # Browser-facing base the frontend uses for the MSE/WebRTC WebSocket.
    go2rtc_ws_url: str = "ws://localhost:1984"

    # ── Anti-freeze re-encode relay ──────────────────────────────────────────
    # Cameras ship a ~2s GOP (keyframe interval); on any jitter the picture
    # freezes up to 2s waiting for the next keyframe. Re-encoding to a short
    # forced keyframe interval cuts recovery to a blink. This is THE thing that
    # made 4MP stable pre-redesign (a go2rtc `exec:ffmpeg` on-demand source).
    # NOT the transport. On-demand → only streams being
    # viewed are encoded, so concurrency is bounded by viewers, not 34 channels.
    # On the server set REENCODE_ENABLED=true + REENCODE_VCODEC=h264_qsv (Intel
    # QuickSync iGPU). vcodec=libx264 is the portable CPU fallback (heavier).
    reencode_enabled: bool = False
    reencode_keyframe_seconds: float = 0.5
    reencode_qualities: str = "sub"  # "sub" | "main" | "both"
    # Video encoder. Default is the portable CPU encoder "libx264" (works
    # everywhere) — this preserves the deployed default (the auto-probe box has
    # no GPU, so it would resolve to libx264 anyway). The auto-probe-best-encoder
    # feature is OPT-IN via env: set REENCODE_VCODEC=auto to have the host probed
    # (real test-encode) and the best WORKING encoder picked:
    # h264_qsv → h264_nvenc → h264_vaapi → libx264 (CPU). A codec can be compiled
    # into ffmpeg yet fail at runtime when the GPU is absent, which is why the
    # probe runs a tiny real encode rather than trusting ffmpeg's encoder list.
    # Set an explicit codec (e.g. h264_qsv) to skip probing entirely.
    reencode_vcodec: str = "libx264"
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
    # How direct (non-via-NVR) MAIN streams are pulled into go2rtc. Switchable
    # without code changes — each mode is a builder in go2rtc_reencode. Findings
    # 2026-06-29 (these Dahua cams: 4MP collapses to ~2fps over TCP, ~22fps over UDP;
    # the camera segment drops ~2% of UDP packets under load):
    #   native        — raw RTSP, go2rtc's native TCP client. Original; ~2fps here.
    #   copy_pipe     — UDP pull, -c copy, MPEG-TS stdout pipe. Full 4MP + sharp, ~0
    #                   CPU, but UDP loss shows as corruption (worse with a long
    #                   camera GOP). Good once cameras are set to a ~1s I-frame.
    #   reencode_pipe — UDP pull, re-encode to a short GOP, MPEG-TS pipe. Conceals
    #                   the UDP loss; ~1 CPU core/main. [DEFAULT, safe]
    #   reencode_rtsp — UDP pull, re-encode, RTSP republish to {output}. The republish
    #                   throttles 4MP to ~3-8fps; kept for reference.
    #   copy_rtsp     — UDP pull, -c copy, RTSP republish. ~2.6fps; reference.
    main_stream_mode: str = "reencode_pipe"
    # Target bitrate (VBV) for the re-encode main modes. 8 Mbps keeps 4MP sharp on a
    # LAN. 0 = uncapped (CRF).
    main_reencode_maxrate_kbps: int = 8000
    # go2rtc rejects exec:/ffmpeg: (subprocess) sources over its HTTP API
    # ("insecure producer"); they're only honoured from the static YAML. So when
    # re-encoding we write streams into this file and reload go2rtc instead of
    # PUT /api/streams. Path is relative to the process CWD (the repo root, where
    # start.ps1/start-mac.sh copy go2rtc.base.yaml → .go2rtc/go2rtc.yaml).
    go2rtc_config_path: str = ".go2rtc/go2rtc.yaml"
    # go2rtc's POST /api/restart only reloads the config DISPLAY — it does NOT
    # re-init the stream registry, so newly added / changed streams (an NVR
    # enable/add/cred-change) never actually load until the go2rtc PROCESS is
    # restarted. That gap silently breaks new NVRs and feeds the source watchdog
    # phantom failures (it then auto-disables the NVR). When set, the reconcile runs
    # this command to HARD-restart go2rtc after a config change instead of the soft
    # API reload. On the Windows server (services run as LocalSystem):
    #   GO2RTC_RESTART_CMD=powershell -NoProfile -Command "Restart-Service dahua-go2rtc"
    # Empty (dev) → fall back to the soft API restart.
    go2rtc_restart_cmd: str = ""

    # ── Source-on-demand timings ─────────────────────────────────────────────
    sub_start_timeout: str = "10s"
    sub_close_after: str = "30s"
    main_start_timeout: str = "20s"
    main_close_after: str = "60s"

    # ── Source watchdog ──────────────────────────────────────────────────────
    # Polls go2rtc's runtime API and auto-disables an NVR whose source keeps
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
    # First-dial grace for the NVR-wide disable path. When an NVR idle longer
    # than the recovery window is reopened, EVERY channel briefly shows
    # "consumer attached, no producer" while go2rtc (re)dials — on the
    # via-NVR / exec-ffmpeg sites that dial can exceed interval×threshold, and
    # without this grace a HEALTHY recorder would be auto-disabled. Failures
    # are counted only after the NVR has been failing continuously this long.
    source_watch_dial_grace_seconds: float = 20.0
    # Startup grace period. On a cold start the grid immediately pulls streams
    # while go2rtc is still spinning up the on-demand RTSP sources, so for the
    # first few seconds every path is "active but not ready" — which looks
    # exactly like an auth failure to the watchdog and made it disable healthy
    # NVRs on every boot. During this window we poll but never disable, giving
    # sources time to connect.
    source_watch_startup_grace_seconds: float = 45.0

    # ── Playback (Phase 1) ───────────────────────────────────────────────────
    # UTC offset of the NVR's internal clock (minutes east of UTC). Used when
    # converting NVR-local naive recording timestamps to UTC epoch seconds.
    # NOTE: live NVR-clock querying is wired by a later spike task; for now
    # this value is the Phase-1 source of the offset (configurable per deploy).
    playback_tz_offset_minutes: int = 0

    # ── Playback (Phase 2) ───────────────────────────────────────────────────
    # Settings for the server-side ffmpeg playback session (Task 7) and the
    # WebSocket control handler (Task 8).  The ffmpeg binary and keyframe
    # interval are reused from reencode_ffmpeg_bin / reencode_keyframe_seconds
    # above (no duplication needed).
    # Note: the RTSP port is taken from nvr.port (Contract #9) — no deploy-wide
    # default is needed here.

    # Per-NVR playback slot limit (NvrBudget, Task 6).  Defaults to 2 pending
    # V9 verification of how many concurrent RTSP pulls the NVR supports.
    playback_nvr_budget: int = 2

    # Hard global cap on concurrent playback sessions across all NVRs
    # (NvrBudget, Task 6).  When reached, the WS endpoint closes with code
    # 4429 ("resource exhausted"; Contract #2).
    playback_global_cap: int = 4

    # Size of the fMP4 ring buffer (number of chunks).  When full, the oldest
    # chunk is dropped before enqueueing the newest — the stdout reader never
    # blocks on a slow WS client (Contract #11).
    playback_ring_buffer_chunks: int = 32

    # Idle timeout: close a PAUSED/idle session after this many seconds with no
    # activity (the reaper, Task 7).  The client sends ``{"keepalive": true}``
    # (~every 30s) to keep a paused session alive, so this must exceed that
    # interval and cover the spec §10 5-minute-pause check.
    playback_idle_timeout_seconds: int = 300

    # Hard maximum session lifetime (seconds) regardless of activity (reaper,
    # Task 7).
    playback_max_lifetime_seconds: int = 3600

    # How often to emit a ``{"type": "clock", "wall_ts": <epoch>}`` heartbeat
    # to the client so it can correct playhead drift (Contract #3).
    playback_clock_interval_seconds: float = 2.0

    # Rate-limit: max playback WS session OPEN attempts per user per minute
    # (Task 8); excess attempts are rejected with code 4429.
    playback_rate_limit_per_minute: int = 10

    # ── Warm-stream pool (live-open latency) ─────────────────────────────────
    # Keeps a bounded set of server-side consumers draining go2rtc SUB streams so
    # those cameras open ~instantly (warm producer + cached keyframe → ~0.5s vs
    # 2.6–5s cold). SUBS ONLY — mains use an mpegts pipe with no keyframe cache
    # and don't benefit. DEFAULT OFF: over-warming would exhaust an NVR's hard
    # concurrent-pull cap (e.g. testik 192.168.20.39). When False the POST
    # /live/warm endpoint is a 202 no-op so the frontend can call it uncondition-
    # ally. Enable per deploy with WARM_POOL_ENABLED=true.
    warm_pool_enabled: bool = False
    # Global cap on concurrently-warmed streams across all NVRs.
    warm_pool_max_streams: int = 24
    # Per-NVR cap. A warm stream is cheaper than a playback session but STILL
    # counts against the NVR's real concurrent-pull budget — never warm any NVR
    # beyond this (mirrors the NvrBudget per-NVR discipline).
    warm_pool_per_nvr_max: int = 8
    # De-selected streams are kept warm this long before teardown, so a quick
    # page flip (open → close → reopen) doesn't re-dial the NVR.
    warm_pool_drop_grace_seconds: float = 10.0

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
