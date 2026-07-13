# Kanagatly VMS backend — FastAPI/uvicorn on :8000.
#
# ffmpeg is REQUIRED at runtime (recorded-playback transcode + optional
# go2rtc re-encode), so it is installed into the image and REENCODE_FFMPEG_BIN
# defaults to the on-PATH binary. SQLite is the default DB (a named volume);
# Postgres is opt-in via DATABASE_URL. Entry runs migrations (Postgres only —
# SQLite converges via create_all on boot, mirroring the native launcher) then
# uvicorn.
#
# This same image also backs the one-shot `go2rtc-init` compose service, which
# renders the runtime go2rtc.yaml (WebRTC candidates for THIS host) into the
# shared config volume before go2rtc starts.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    REENCODE_FFMPEG_BIN=ffmpeg

WORKDIR /app

# ffmpeg → playback/clip transcode + re-encode. curl → container healthcheck.
# build-essential covers any sdist without a slim wheel (argon2/cryptography
# ship wheels, but keep it for safety on odd arches). Cleaned in the same layer.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl build-essential \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first for layer caching — only re-runs when requirements change.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini .
# Licensing dir (public_key.pem is committed once issued; *.lic is operator-
# supplied at runtime via the mounted volume). Enforcement ships DEFAULT OFF.
COPY licensing/ ./licensing/

# Runtime state dirs. Creating them owned by appuser BEFORE the named volumes
# mount here means Docker seeds each first-run volume with appuser ownership, so
# the non-root process can write the SQLite DB, logs, rendered go2rtc.yaml and
# an operator-dropped license without a chown step.
RUN useradd --system --no-create-home --uid 10001 appuser \
    && mkdir -p /data /data/logs /config \
    && chown -R appuser /app /data /config
USER appuser

# SQLite (default) creates its schema via create_all on boot — running alembic
# against it can drift, so we skip it there (matches start-mac.sh). Postgres
# always migrates. Then exec uvicorn so it becomes PID 1 and gets signals.
CMD ["sh", "-c", "case \"${DATABASE_URL:-sqlite}\" in *sqlite*) echo 'SQLite: schema via create_all on boot (skipping alembic)';; *) alembic upgrade head;; esac; exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
