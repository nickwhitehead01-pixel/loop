"""
FastAPI application entry point.

Mounts all routers, sets up CORS, and creates DB tables on startup.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import Base, engine
from app.services import ollama_client

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables + verify Ollama. Shutdown: close HTTP client."""
    # Create all tables (idempotent)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created / verified")

    # Verify Ollama is reachable
    healthy = await ollama_client.health_check()
    if healthy:
        logger.info("Ollama is reachable at %s", settings.ollama_base_url)
    else:
        logger.warning("Ollama is NOT reachable at %s — LLM calls will fail", settings.ollama_base_url)

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

