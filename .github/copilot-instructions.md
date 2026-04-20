# Copilot Instructions — Gemma Education Platform

Multi-agent education platform. Pupil personal AI tutor + teacher management dashboard.
Built for the Kaggle Gemma 4 Good Hackathon (Future of Education + Ollama Special Technology tracks).

---

## Project layout

```
backend/app/
  agents/        pupil_graph.py (LangGraph ReAct), teacher_rag.py (pipeline), tools.py
  api/           endpoints_pupil.py, endpoints_teacher.py
  core/          config.py (Settings), database.py (async SQLAlchemy)
  models/        domain.py (ORM), schemas.py (Pydantic)
  services/      ollama_client.py, vector_store.py
frontend/src/
  app/           Next.js 15 App Router — no pages/ directory
  components/    PupilChat, TeacherDashboard, LessonUpload, StudentProgress
  hooks/         useWebSocket, useLessons, useStudentProgress
```

---

## Backend conventions

### Database
- All DB access is **async SQLAlchemy** — always use `AsyncSession`, `await db.execute(...)`, `await db.commit()`
- Never use `Session` (sync) or `db.query()` (legacy ORM style)
- Import the session dependency: `from app.core.database import get_db`
- Use `select()` from `sqlalchemy` — not `db.query(Model)`

```python
# correct
result = await db.execute(select(User).where(User.id == user_id))
user = result.scalar_one_or_none()

# wrong — never do this
user = db.query(User).filter(User.id == user_id).first()
```

### Models
- Config values come from `settings` — never hardcode model names, URLs, or paths
- Pupil model: `settings.ollama_model_pupil` (`gemma4:e2b`)
- Teacher model: `settings.ollama_model_teacher` (`gemma4:27b`)
- Embed model: `settings.ollama_embed_model` (`nomic-embed-text`)

```python
# correct
from app.core.config import settings
model = settings.ollama_model_pupil

# wrong
model = "gemma4:e2b"
```

### LLM / Ollama
- Use `langchain-ollama` (`ChatOllama`) for LangGraph agents — **not** `langchain-community`
- Use `app.services.ollama_client` for raw HTTP calls (teacher RAG, health check, embeddings)
- The shared `httpx.AsyncClient` in `ollama_client` handles connection pooling — do not create new clients inline

```python
# correct — in agents
from langchain_ollama import ChatOllama

# correct — raw calls
from app.services import ollama_client
summary = await ollama_client.generate_full(messages, model=settings.ollama_model_teacher)

# wrong
import httpx
httpx.post("http://localhost:11434/...")  # never inline httpx for Ollama
```

### File uploads
- Accepted types: `.pdf`, `.docx`, `.pptx`, `.txt`
- Always validate against `ACCEPTED_EXTENSIONS` from `app.services.vector_store`
- Always pass the original `filename` to `ingest_lesson()` so the correct parser is used
- Store files to `settings.upload_dir`

### Memory (long-term, per-pupil)
- `PupilMemory` table stores atomic facts per pupil with a `Vector(768)` embedding
- `load_all_pupil_memories_func` loads the last 20 memories at session start — injected into the system prompt via `build_system_prompt(memories)`
- `get_pupil_memories_func` does pgvector similarity search — available as an agent tool
- `save_pupil_memories_func` persists new facts extracted post-turn
- Memory extraction runs after every turn using `gemma4:e2b` — returns a JSON array of strings
- Failures in memory extraction must never raise — always wrapped in `try/except`
- Never load all memories into context blindly — use similarity search (`get_pupil_memories`) for mid-conversation recall


- Routers live in `app/api/` — include them in `main.py`
- WebSocket endpoints stream tokens — yield tokens as JSON `{"token": "...", "done": false}`
- Send `{"token": "", "done": true}` as the final frame
- CORS is configured in `main.py` — do not add it to individual routers

### Pydantic
- Use Pydantic v2 style: `model_config = ConfigDict(from_attributes=True)` not `class Config`
- Response models always have `from_attributes=True` when reading ORM objects

---

## Frontend conventions

### Routing
- Next.js 15 **App Router** only — all pages in `src/app/`
- No `pages/` directory — never suggest `getServerSideProps` or `getStaticProps`
- Layouts in `layout.tsx`, pages in `page.tsx`

### API calls
- Base URL comes from `process.env.NEXT_PUBLIC_API_URL` — never hardcode `localhost:8000`
- WebSocket URL from `process.env.NEXT_PUBLIC_WS_URL`
- The `useWebSocket` hook handles connection, reconnect, and message parsing — reuse it

### Styling
- Tailwind CSS 4 — utility classes only, no custom CSS files unless absolutely necessary
- Dark-friendly palette: assume the UI may be used in dark mode

### TypeScript
- Strict mode is on — no `any` types
- API response types should mirror the Pydantic schemas in `backend/app/models/schemas.py`

---

## Testing best practices

### Backend (pytest + pytest-asyncio)

**Setup**
```python
# conftest.py
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.main import app
from app.core.database import Base, get_db

TEST_DB = "postgresql+asyncpg://gemma_user:gemma_password@localhost:5432/gemma_test"

@pytest.fixture(scope="session")
async def engine():
    eng = create_async_engine(TEST_DB)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()

@pytest.fixture
async def db(engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()

@pytest.fixture
async def client(db):
    app.dependency_overrides[get_db] = lambda: db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
```

**Rules**
- All test functions that touch the DB or HTTP client must be `async def` with `@pytest.mark.asyncio`
- Use `pytest.mark.asyncio(loop_scope="session")` at module level to avoid event loop conflicts
- Use the `db` fixture for DB tests — never create your own session in a test
- Use `AsyncClient` with `ASGITransport` for endpoint tests — never run a live server in tests

**Mocking Ollama**
- Never call real Ollama in tests — always mock at the `ollama_client` level
- Mock `ollama_client.embed` to return a deterministic `[0.0] * 768` vector
- Mock `ollama_client.generate_full` / `generate_stream` to return stub strings

```python
# correct
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_ingest_lesson(db):
    with patch("app.services.vector_store.embed", new=AsyncMock(return_value=[0.0] * 768)):
        count = await ingest_lesson(1, pdf_bytes, db, filename="test.pdf")
    assert count > 0

# wrong — this will try to reach a real Ollama instance
async def test_ingest_lesson(db):
    count = await ingest_lesson(1, pdf_bytes, db)
```

**What to test**
- `vector_store.py`: `_chunk_text()` is pure — test it directly with edge cases (empty input, very long paragraphs, short content below MIN_CHUNK_CHARS)
- `vector_store.py`: `extract_text()` dispatcher — test each format with a minimal fixture file
- `schemas.py`: round-trip serialisation (`UserResponse.model_validate(orm_obj)`)
- `endpoints_teacher.py`: upload endpoint — assert 422 on bad extension, 200 on valid file
- `endpoints_pupil.py`: WebSocket — assert token frames arrive before the `done` frame
- `domain.py`: relationships load correctly (Lesson → LessonChunks cascade delete)

### Frontend (Jest + React Testing Library)

**Rules**
- Test component behaviour, not implementation — query by role/text, not class names
- Mock `fetch` and WebSocket at the module level using `jest.mock`
- Never test Tailwind classes — test visible text and ARIA roles

```tsx
// correct
expect(screen.getByRole('button', { name: /send/i })).toBeInTheDocument()

// wrong
expect(container.querySelector('.btn-primary')).toBeInTheDocument()
```

**What to test**
- `useWebSocket`: connection opens, messages are appended, reconnect fires on close
- `LessonUpload`: rejects non-accepted file types before upload, shows progress on valid file
- `PupilChat`: renders streamed tokens as they arrive, shows loading state
- `StudentProgress`: renders table rows from mocked API response

---

## What not to do

- Do not add authentication logic — v1 uses plain user IDs; JWT auth is deferred
- Do not create a `pages/` directory in the frontend
- Do not import `langchain-community` for Ollama — use `langchain-ollama`
- Do not hardcode `localhost` URLs anywhere — always use env vars
- Do not use synchronous SQLAlchemy — everything is async
- Do not add a `railway.json` — deployment is via ngrok + local Docker
