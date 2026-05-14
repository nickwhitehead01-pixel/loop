from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from .config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    connect_args={
        "check_same_thread": False,
        # Give every connection a 30-second busy-wait timeout so that
        # concurrent writers (e.g. ingest_lesson_images + precompute worker)
        # retry instead of immediately raising OperationalError: database is locked.
        # enable_wal() sets PRAGMA busy_timeout on one connection; this ensures
        # ALL connections created via NullPool inherit the same timeout.
        "timeout": 30,
    },
    # NullPool: every coroutine gets its own SQLite connection — no shared pool
    # to exhaust. Correct for SQLite + async workloads where long-running
    # background tasks (Gemma precompute) hold sessions concurrently.
    poolclass=NullPool,
)


async def enable_wal() -> None:
    """Enable WAL journal mode so concurrent reads never block writes."""
    from sqlalchemy import text
    async with engine.connect() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))
        await conn.execute(text("PRAGMA busy_timeout=5000"))
        await conn.commit()

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

