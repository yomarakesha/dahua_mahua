from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.settings import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
)

if engine.dialect.name == "sqlite":
    # SQLite ignores FOREIGN KEY constraints unless they're enabled per
    # connection. Without this, ON DELETE rules never fire and dangling
    # region_id references are silently accepted. (No-op on Postgres, which
    # enforces FKs natively.)
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        # WAL lets a reader and a writer coexist; busy_timeout makes a blocked
        # writer wait instead of failing instantly with "database is locked"
        # (default timeout is 0). Together they keep the separate-session audit
        # writes (nvr_events.log_event, watchdog) from colliding under SQLite.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
