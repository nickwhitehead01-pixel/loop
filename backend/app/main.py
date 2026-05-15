"""
FastAPI application entry point.

Mounts all routers, sets up CORS, and creates DB tables on startup.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import Base, engine, AsyncSessionLocal, enable_wal
from app.services import ollama_client
from app.services.chroma_client import init_collections
from app.services.lesson_summary_worker import start_summary_worker
from app.services.transcription import get_model

logger = logging.getLogger(__name__)

# Configure logging before anything else.
# force=True is required because Uvicorn registers its own handlers first;
# without it basicConfig is a no-op.
logging.basicConfig(
    force=True,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def _close_stale_sessions() -> None:
    """Mark any live/open sessions as ended on startup.

    Active transcription cannot survive a backend restart — WebSocket
    connections are gone, Whisper is no longer running — so leaving sessions
    in 'live' or 'open' would permanently show stale entries in the pupil app.
    """
    from datetime import datetime, timezone
    from sqlalchemy import update
    from app.models.domain import LessonSession, SessionStatus

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(LessonSession)
            .where(LessonSession.status.in_([SessionStatus.live, SessionStatus.open]))
            .values(status=SessionStatus.ended, ended_at=datetime.now(timezone.utc))
        )
        if result.rowcount:
            logger.info(
                "Closed %d stale session(s) left open from a previous run",
                result.rowcount,
            )
        await db.commit()


async def _seed_default_users() -> None:
    """Ensure a default teacher (id=1) and a default pupil (id=2) exist."""
    from sqlalchemy import select, text
    from app.models.domain import User

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id == 1))
        if result.scalar_one_or_none() is None:
            # Force id=1 via raw INSERT so the FK from lessons always resolves
            await db.execute(
                text("INSERT INTO users (id, name, role) VALUES (1, 'Default Teacher', 'teacher') ON CONFLICT DO NOTHING")
            )
        result = await db.execute(select(User).where(User.id == 2))
        if result.scalar_one_or_none() is None:
            await db.execute(
                text("INSERT INTO users (id, name, role) VALUES (2, 'Default Pupil', 'pupil') ON CONFLICT DO NOTHING")
            )
        await db.commit()
    logger.info("Default users seeded (teacher id=1, pupil id=2)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables + verify Ollama. Shutdown: close HTTP client."""
    # Enable WAL mode before any queries (reads never block writes)
    await enable_wal()
    logger.info("SQLite WAL mode enabled")

    # Create all tables (idempotent)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created / verified")

    init_collections()
    logger.info("ChromaDB collections initialised")

    # Seed default users (auth is deferred — v1 uses fixed IDs)
    await _seed_default_users()

    # Close any sessions that were left open by a previous backend process
    await _close_stale_sessions()

    healthy = await ollama_client.health_check()
    if healthy:
        logger.info("Ollama is reachable at %s", settings.ollama_base_url)
    else:
        logger.warning("Ollama is NOT reachable at %s — LLM calls will fail", settings.ollama_base_url)

    # Warm up Whisper once so first live transcript does not pay model load latency.
    try:
        await asyncio.to_thread(get_model)
        logger.info("Whisper model warm-up complete")
    except Exception:
        logger.exception("Whisper warm-up failed; live transcription may have cold-start delay")

    # Warm up Gemma and the embed model so the first pupil message does not pay
    # Ollama's cold-start model-load latency (~3-8 s).  keep_alive=-1 pins both
    # models in memory for the server's lifetime — no 5-minute idle eviction.
    await ollama_client.warmup_model(settings.ollama_model_pupil)
    await ollama_client.warmup_model(settings.ollama_embed_model)

    summary_task = asyncio.create_task(start_summary_worker())
    logger.info("Lesson summary worker task started")

    yield

    # Shutdown
    summary_task.cancel()
    try:
        await summary_task
    except asyncio.CancelledError:
        pass
    await ollama_client.close_client()
    await engine.dispose()
    logger.info("Shutdown complete")


app = FastAPI(
    title="LoopLens — AI Education Platform",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
from app.api.endpoints_pupil import router as pupil_router
from app.api.endpoints_teacher import router as teacher_router
from app.api.endpoints_session import router as session_router
from app.api.endpoints_quiz import router as quiz_router

app.include_router(pupil_router)
app.include_router(teacher_router)
app.include_router(session_router)
app.include_router(quiz_router)


# ---------------------------------------------------------------------------
# Global exception handlers
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for any unhandled exception — logs the full traceback and
    returns a safe, non-disclosing 500 response body."""
    logger.error(
        "Unhandled exception: %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Log request validation errors at WARNING with the full detail for
    server-side diagnostics while returning the default 422 body."""
    logger.warning(
        "Request validation error: %s %s — %s",
        request.method,
        request.url.path,
        exc.errors(),
        exc_info=True,
    )
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )


@app.get("/health")
async def health():
    ollama_ok = await ollama_client.health_check()
    return {
        "status": "ok",
        "ollama": "connected" if ollama_ok else "unreachable",
    }


# ── Users (minimal CRUD for dev) ──────────────────────────────────────────

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.models.domain import User
from app.models.schemas import UserCreate, UserResponse
from sqlalchemy.exc import IntegrityError


@app.post("/users", response_model=UserResponse)
async def create_user(body: UserCreate, db: AsyncSession = Depends(get_db)):
    try:
        user = User(name=body.name, role=body.role)
        db.add(user)
        await db.flush()
        await db.refresh(user)
        await db.commit()
        return user
    except IntegrityError as exc:
        await db.rollback()
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="A user with that name already exists") from exc
    except Exception as exc:
        await db.rollback()
        logger.exception("Unexpected error creating user")
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@app.get("/users", response_model=list[UserResponse])
async def list_users(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).order_by(User.id))
    return result.scalars().all()

