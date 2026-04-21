"""
FastAPI application entry point.

Mounts all routers, sets up CORS, and creates DB tables on startup.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import Base, engine, AsyncSessionLocal
from app.services import ollama_client
from app.services.transcription import get_model

logger = logging.getLogger(__name__)


async def _seed_default_users() -> None:
    """Ensure a default teacher (id=1) and a default pupil (id=2) exist."""
    from sqlalchemy import select, text
    from app.models.domain import User, Role

    async with AsyncSessionLocal() as db:
        # Check for teacher id=1
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
    # Create all tables (idempotent)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created / verified")

    # Seed default users (auth is deferred — v1 uses fixed IDs)
    await _seed_default_users()

    # Verify Ollama is reachable
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

    yield

    # Shutdown
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

app.include_router(pupil_router)
app.include_router(teacher_router)
app.include_router(session_router)


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


@app.post("/users", response_model=UserResponse)
async def create_user(body: UserCreate, db: AsyncSession = Depends(get_db)):
    user = User(name=body.name, role=body.role)
    db.add(user)
    await db.flush()
    await db.refresh(user)
    await db.commit()
    return user


@app.get("/users", response_model=list[UserResponse])
async def list_users(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).order_by(User.id))
    return result.scalars().all()

