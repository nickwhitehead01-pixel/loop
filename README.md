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
│   │  │   Ollama    │  │   FastAPI    │  │  PostgreSQL    │  │              │
│   │  │  gemma4:e2b │  │  + LangGraph │  │  + pgvector    │  │              │
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
│   │  native iOS app │   │   memory      │   │   memory       │            │
│   └─────────────────┘   │ • Apple TTS   │   │ • Apple TTS    │            │
│                         │ • Guided      │   │ • Guided       │            │
│                         │   Access      │   │   Access       │            │
│                         │ native iOS app│   │ native iOS app │            │
│                         └───────────────┘   └────────────────┘            │
└─────────────────────────────────────────────────────────────────────────────┘

4. THE INFRASTRUCTURE: Fully air-gapped. FERPA / GDPR / IEP compliant by design.
```

---

## The Four Nodes

### 1. The Windows Hub (Server Node)
The IT admin downloads a **single 1-Click launcher** to any school-issued Windows Desktop. It silently spins up Ollama (with local hardware acceleration) and a Docker Compose stack containing the FastAPI backend, LangGraph reasoning engine, and a local PostgreSQL/pgvector database.

The Hub is the central brain: it processes document RAG pipelines, runs faster-whisper for live classroom speech-to-text, and serves every student's personalised LangGraph agent simultaneously.

### 2. The Teacher Companion (Command Node)
The teacher runs a **native iOS/iPadOS app** on their school iPad. They are never tethered to a desk.

- **Pre-lesson:** Upload lesson plans, PDFs, worksheets — synced instantly to the Hub to pre-load the AI's context for that session.
- **Live lesson:** The iPad acts as a **high-fidelity wireless microphone**. The teacher's voice streams over the local network to the Hub, which:
  1. Transcribes the audio in real-time using faster-whisper
  2. Chunks the transcript into semantic blocks (sentence-boundary aware)
  3. Embeds each chunk via `nomic-embed-text` and stores it in pgvector
  4. Broadcasts the new chunk to all connected pupil agents immediately

  This means every student's LangGraph agent has the teacher's words in its RAG context **within seconds** — students can ask questions about what was just said, even mid-lesson.

### 3. The Pupil Client (Edge Nodes)
Each student's iPad runs a **native iOS/iPadOS app** distributed silently via MDM (Apple School Manager / Jamf). When opened, it uses **mDNS (Bonjour/ZeroConf)** to auto-discover the Windows Hub — no manual IP addresses required.

Each student logs into their **own personalised LangGraph agent** backed by:
- **Short-term memory:** sliding window of recent conversation messages
- **Long-term memory:** atomic learning facts (struggles, preferences, progress) stored as pgvector embeddings, retrieved by similarity each turn
- **RAG context:** live teacher transcript chunks (streamed in real-time as the teacher speaks) + uploaded lesson materials
- **Apple accessibility:** on-device Text-to-Speech and Guided Access for neurodivergent learners

### 4. The Infrastructure (Air-Gap)
All communication is **entirely on the school's local Wi-Fi**. The system is air-gapped from the internet by design, inherently complying with FERPA, GDPR, and IEP data privacy regulations.

---

## The Pupil Agent: A True Multi-Tool LangGraph Agent

The pupil agent is a **ReAct-style LangGraph StateGraph**, not a simple chatbot. It has access to 8 tools during every turn:

| Tool | Purpose |
|------|---------|
| `retrieve_context` | pgvector similarity search over teacher-uploaded lesson chunks |
| `search_live_transcript` | search the live classroom speech-to-text transcript |
| `get_full_transcript` | retrieve the full ordered lesson transcript |
| `get_conversation_history` | sliding window of recent messages (short-term memory) |
| `load_all_pupil_memories` | inject long-term memory facts into system prompt |
| `save_pupil_memories` | extract and persist new learning facts after each response |
| `list_lessons` | enumerate available lesson materials |
| `get_full_transcript` | full ordered transcript for deep context |

After every response, Gemma 4 automatically extracts 1–3 learning facts from the conversation ("student struggles with fractions", "prefers visual analogies") and stores them as embeddings. On the next turn, relevant memories are retrieved by similarity and injected into the system prompt — **the agent adapts to each individual student across every lesson.**

---

## Semantic Answer Cache

In a classroom of 30 students, many pupils ask the same question about the same lesson. Running Gemma 4 separately for each identical question wastes time and CPU cycles on constrained school hardware.

The platform includes a **per-lesson semantic cache** backed by pgvector:

```
Pupil asks question
       │
       ▼
 embed question (nomic-embed-text)
       │
       ▼
 cosine similarity search → semantic_cache table (scoped to lesson_id)
       │
  hit (≥ 0.92)?──────────── YES ──────── return cached answer instantly
       │
       NO
       │
       ▼
  run LangGraph agent → Gemma 4 generates answer
       │
       ▼
  store (question_embedding, answer, lesson_id) in cache
       │
       ▼
  return answer to pupil
```

The cache is **lesson-scoped** — it resets when a new lesson session starts, so answers always reflect the current material. The similarity threshold (default 0.92) is configurable: lower it to cache more aggressively, raise it to be more precise. This makes the platform viable on modest school hardware even with a full class online simultaneously.

---

## Model choices

| Model | Used for | Why |
|-------|----------|-----|
| `gemma4:e2b` | Pupil chat + Teacher RAG (real-time streaming) | Strong reasoning, memory-efficient (~7.2 GB), runs on school hardware without a dedicated GPU |
| `gemma4:e2b` | Fallback for memory-constrained hardware | Fits on 8 GB RAM, still capable for single-turn Q&A |
| `nomic-embed-text` | pgvector embeddings (RAG + memory retrieval) | Fast, 768-dim vectors, fully local |
| `faster-whisper` (tiny/small) | Live classroom speech-to-text | Runs on CPU, real-time on-device |

---

## Quick Start (Windows Hub)

**Prerequisites:** Docker Desktop, ~15 GB free disk space, Ollama installed.

```bash
git clone <repo-url>
cd gemma-education-platform

# Pull models
ollama pull gemma4:e2b
ollama pull nomic-embed-text

# Start the backend stack
cp .env.example .env
docker-compose up
```

- API docs → http://localhost:8000/docs
- Health   → http://localhost:8000/health
- PoC UI   → http://localhost:8765 (run `python poc/poc_whisper_gemma.py`)

---

## PoC: Whisper + Gemma 4 (Try it now)

Before deploying the full stack, try the self-contained proof-of-concept:

```bash
# Create venv and install deps
python3 -m venv venv && source venv/bin/activate
pip install -r backend/requirements.txt

python poc/poc_whisper_gemma.py
```

Open http://localhost:8765 — record your voice, get a live transcript, ask Gemma 4 a question about it. This is the core loop of the entire platform.

---

## Privacy & Compliance

| Requirement | How we meet it |
|------------|----------------|
| FERPA | All data stays on school hardware — no internet egress |
| GDPR | No cloud processing, no third-party data processors |
| IEP confidentiality | Air-gapped network, local DB, no telemetry |
| COPPA | No accounts linked to cloud services, no data leaves school |

---

## License

[Creative Commons Attribution 4.0 International (CC-BY 4.0)](LICENSE)
