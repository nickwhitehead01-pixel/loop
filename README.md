# The Gemma Education Hub

> A 100% offline, privacy-first Edge AI ecosystem for special education classrooms.
> Every student gets a personalised AI tutor. Every teacher gets a smart teaching companion.
> Not a single byte of student data ever leaves the school network.
>
> Built for the **Kaggle Gemma 4 Good Hackathon** — targeting the
> *Future of Education* ($10 K) and *Ollama Special Technology* ($10 K) prize tracks.

---

## The Problem

Special education classrooms generate intensely sensitive data — IEPs, behavioural notes, learning profiles — but the students who need AI-assisted learning the most are the ones most at risk from cloud data exposure. Existing AI tutoring tools require internet access, violate FERPA/GDPR, and are built for neurotypical learners.

**The Gemma Education Hub solves all three problems simultaneously.**

---

## Architecture

The system is a four-node Edge AI ecosystem that runs entirely on a school's existing local Wi-Fi. No internet required. No cloud. No data risk.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SCHOOL LOCAL Wi-Fi (WLAN)                           │
│                                                                             │
│   ┌──────────────────────────────────────────────────────────┐              │
│   │           1. THE WINDOWS HUB  (Server Node)              │              │
│   │                                                          │              │
│   │  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │              │
│   │  │   Ollama    │  │   FastAPI    │  │    SQLite      │  │              │
│   │  │  gemma4:e2b │  │  + LangGraph │  │  + ChromaDB    │  │              │
│   │  │  (local GPU)│  │  (reasoning) │  │  (memory + RAG)│  │              │
│   │  └─────────────┘  └──────────────┘  └────────────────┘  │              │
│   │                                                          │              │
│   │  ┌─────────────┐  ┌──────────────────────────────────┐  │              │
│   │  │   Whisper   │  │  mDNS / Bonjour (ZeroConf)       │  │              │
│   │  │ (live STT)  │  │  auto-discovered by all devices  │  │              │
│   │  └─────────────┘  └──────────────────────────────────┘  │              │
│   └────────────────────────────┬─────────────────────────────┘              │
│                                │ Local WebSocket / HTTP                     │
│              ┌─────────────────┼──────────────────────┐                    │
│              │                 │                      │                    │
│   ┌──────────▼──────┐   ┌──────▼────────┐   ┌────────▼───────┐            │
│   │   2. TEACHER    │   │  3. PUPIL A   │   │  3. PUPIL B    │   ...      │
│   │   COMPANION     │   │    CLIENT     │   │    CLIENT      │            │
│   │   (iPad)        │   │   (iPad)      │   │   (iPad)       │            │
│   │                 │   │               │   │                │            │
│   │ • Upload lessons│   │ • Personal    │   │ • Personal     │            │
│   │ • Wireless mic  │   │   LangGraph   │   │   LangGraph    │            │
│   │ • Live captions │   │   agent       │   │   agent        │            │
│   │ • Manage class  │   │ • Short &     │   │ • Short &      │            │
│   │                 │   │   long-term   │   │   long-term    │            │
│   │  Next.js app    │   │   memory      │   │   memory       │            │
│   └─────────────────┘   │ • Apple TTS   │   │ • Apple TTS    │            │
│                         │ • Guided      │   │ • Guided       │            │
│                         │   Access      │   │   Access       │            │
│                         │ Flutter app   │   │ Flutter app    │            │
│                         └───────────────┘   └────────────────┘            │
└─────────────────────────────────────────────────────────────────────────────┘

4. THE INFRASTRUCTURE: Fully air-gapped. FERPA / GDPR / IEP compliant by design.
```

---

## The Four Nodes

### 1. The Windows Hub (Server Node)
The IT admin runs `python setup.py install` once to set up all dependencies, then `python setup.py start` to launch the hub. **No Docker required.** The setup script creates a Python virtual environment, installs all backend packages, installs the Next.js frontend, and creates the local data directories.

The Hub is the central brain: it processes document RAG pipelines, runs faster-whisper for live classroom speech-to-text, and serves every student's personalised LangGraph agent simultaneously.

**Storage:**
- **SQLite** (`data/gemma_edu.db`) — all relational data: users, lessons, conversations, sessions, summaries, quizzes
- **ChromaDB** (`data/chroma/`) — all vector embeddings: lesson chunks, transcript chunks, pupil/teacher memories, semantic cache

### 2. The Teacher Companion (Command Node)
The teacher accesses the **Next.js web app** from any browser on the same Wi-Fi network. They are never tethered to a desk.

- **Pre-lesson:** Upload lesson plans, PDFs, worksheets — synced instantly to the Hub to pre-load the AI's context for that session.
- **Live lesson:** Stream classroom audio from the browser. The Hub:
  1. Transcribes the audio in real-time using faster-whisper
  2. Chunks the transcript into semantic blocks (sentence-boundary aware)
  3. Embeds each chunk via `nomic-embed-text` and stores it in ChromaDB
  4. Broadcasts the new chunk to all connected pupil agents immediately

  This means every student's LangGraph agent has the teacher's words in its RAG context **within seconds** — students can ask questions about what was just said, even mid-lesson.

### 3. The Pupil Client (Edge Nodes)
Each student runs a **Flutter mobile app (iPad + Android, including Google Pixel)**. The app connects to the Hub URL over school Wi-Fi — entered once and saved locally.

Each student logs into their **own personalised LangGraph agent** backed by:
- **Short-term memory:** sliding window of recent conversation messages
- **Long-term memory:** atomic learning facts (struggles, preferences, progress) stored as ChromaDB embeddings, retrieved by similarity each turn
- **RAG context:** live teacher transcript chunks (streamed in real-time as the teacher speaks) + uploaded lesson materials
- **Accessibility:** on-device Text-to-Speech and simplified, low-cognitive-load UX for neurodivergent learners

### 4. The Infrastructure (Air-Gap)
All communication is **entirely on the school's local Wi-Fi**. The system is air-gapped from the internet by design, inherently complying with FERPA, GDPR, and IEP data privacy regulations.

---

## The Pupil Agent: A True Multi-Tool LangGraph Agent

The pupil agent is a **ReAct-style LangGraph StateGraph**, not a simple chatbot. It has access to tools during every turn:

| Tool | Purpose |
|------|---------|
| `retrieve_context` | ChromaDB similarity search over teacher-uploaded lesson chunks |
| `search_live_transcript` | search the live classroom speech-to-text transcript |
| `get_full_transcript` | retrieve the full ordered lesson transcript |
| `get_conversation_history` | sliding window of recent messages (short-term memory) |
| `load_all_pupil_memories` | inject long-term memory facts into system prompt |
| `save_pupil_memories` | extract and persist new learning facts after each response |
| `list_lessons` | enumerate available lesson materials |

After every response, Gemma 4 automatically extracts 1–3 learning facts from the conversation ("student struggles with fractions", "prefers visual analogies") and stores them as ChromaDB embeddings. On the next turn, relevant memories are retrieved by similarity and injected into the system prompt — **the agent adapts to each individual student across every lesson.**

---

## Semantic Answer Cache

In a classroom of 30 students, many pupils ask the same question about the same lesson. Running Gemma 4 separately for each identical question wastes time and CPU cycles on constrained school hardware.

The platform includes a **per-session semantic cache** backed by ChromaDB:

```
Pupil asks question
       │
       ▼
 embed question (nomic-embed-text)
       │
       ▼
 cosine similarity search → ChromaDB semantic_cache collection (scoped to session_id)
       │
  hit (≥ 0.92)?──────────── YES ──────── return cached answer instantly
       │
       NO
       │
       ▼
  run LangGraph agent → Gemma 4 generates answer
       │
       ▼
  store (question_embedding, answer, session_id) in ChromaDB
       │
       ▼
  return answer to pupil
```

The cache is **session-scoped** — it resets when a new lesson session starts, so answers always reflect the current material. The similarity threshold (default 0.92) is configurable via `SEMANTIC_CACHE_THRESHOLD` in `.env`. This makes the platform viable on modest school hardware even with a full class online simultaneously.

---

## Model choices

| Model | Used for | Why |
|-------|----------|-----|
| `gemma4:e2b` | Pupil chat + Teacher RAG (real-time streaming) | Strong reasoning, memory-efficient (~7.2 GB), runs on school hardware without a dedicated GPU |
| `nomic-embed-text` | ChromaDB embeddings (RAG + memory retrieval) | Fast, 768-dim vectors, fully local |
| `faster-whisper` (tiny/small) | Live classroom speech-to-text | Runs on CPU, real-time on-device |

---

## Quick Start (Windows Hub)

**Prerequisites:** Python 3.11+, Node.js 18+, Ollama installed and running. No Docker required.

```bash
git clone <repo-url>
cd gemma-education-platform

# Pull models (first time only)
ollama pull gemma4:e2b
ollama pull nomic-embed-text

# Install everything
python setup.py install

# Start the hub
python setup.py start
```

- Teacher UI  → http://localhost:3000
- API docs    → http://localhost:8000/docs
- Health      → http://localhost:8000/health

### What `setup.py install` does
1. Checks Python ≥ 3.11 and Node.js ≥ 18
2. Creates `backend/.venv` and installs all Python dependencies
3. Runs `npm install` in `frontend/`
4. Creates `backend/data/`, `backend/data/chroma/`, `backend/uploads/`
5. Writes a `.env` file with sensible defaults (skips if it already exists)
6. Verifies Ollama is reachable at `http://localhost:11434`

### What `setup.py start` does
Launches the FastAPI backend (`uvicorn`, port 8000) and Next.js frontend (`npm run dev`, port 3000) as sub-processes. Ctrl-C shuts both down cleanly.

```bash
# Production mode (npm start instead of npm run dev)
python setup.py start --prod
```

---

## PoC: Whisper + Gemma 4 (Try it now)

Before deploying the full stack, try the self-contained proof-of-concept:

```bash
# Ensure Ollama is running
ollama serve  # in another terminal

python3 -m venv venv && source venv/bin/activate
pip install -r backend/requirements.txt

python poc/poc_whisper_gemma.py
```

Open http://localhost:8765 — record your voice, get a live transcript, ask Gemma 4 a question about it. This is the core loop of the entire platform.

---

## Local Data

All persistent data is stored inside the `backend/` directory — nothing is written outside the project folder:

| Path | Contents |
|------|----------|
| `backend/data/gemma_edu.db` | SQLite database (users, lessons, conversations, sessions, quizzes) |
| `backend/data/chroma/` | ChromaDB vector store (lesson chunks, transcript, memories, cache) |
| `backend/uploads/` | Raw uploaded lesson files (PDF, DOCX, PPTX, TXT) |
| `.env` | Configuration overrides (auto-generated by `setup.py install`) |

To start fresh, delete `backend/data/` and re-run `python setup.py start`.

---

## Privacy & Compliance

| Requirement | How we meet it |
|------------|----------------|
| FERPA | All data stays on school hardware — no internet egress |
| GDPR | No cloud processing, no third-party data processors |
| IEP confidentiality | Air-gapped network, local SQLite + ChromaDB, no telemetry |
| COPPA | No accounts linked to cloud services, no data leaves school |

---

## Development Guidelines

**Branching & commits:**
- Always create a feature branch before making changes: `git checkout -b feature/your-feature-name`
- **Do not commit to `main` unless explicitly instructed**
- Commit messages should be clear and descriptive (e.g., `Add semantic cache to pupil agent`)
- Push your feature branch and create a Pull Request for review
- Only merge to `main` after review and approval

**Example workflow:**
```bash
git checkout -b feature/new-endpoint
# Make changes
git add .
git commit -m "Add new WebSocket endpoint"
git push origin feature/new-endpoint
# Create PR on GitHub
```

---

## License

[Creative Commons Attribution 4.0 International (CC-BY 4.0)](LICENSE)
