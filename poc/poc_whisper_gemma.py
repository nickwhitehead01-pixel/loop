"""
PoC: Mac Mic → faster-whisper (live transcript) + manual question → Gemma4:e4b response

Flow:
  1. Teacher speaks → Whisper transcribes → transcript shown on screen
  2. Student types a question → Gemma4:e4b answers it (streamed)

Run:
    python poc/poc_whisper_gemma.py

Then open http://localhost:8765 in your browser.

Prerequisites:
    pip install fastapi "uvicorn[standard]" faster-whisper httpx
    (all already in backend/requirements.txt)

    Ollama must be running with gemma4:e2b pulled:
        ollama serve
        ollama pull gemma4:e2b
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor

import httpx
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from faster_whisper import WhisperModel

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "gemma4:e2b"
WHISPER_MODEL_SIZE = "tiny"   # tiny ~75MB RAM; small ~500MB — use tiny to avoid OOM on 16GB with Ollama running

app = FastAPI()

_whisper: WhisperModel | None = None
_executor = ThreadPoolExecutor(max_workers=1)


def _load_whisper_sync() -> WhisperModel:
    """Blocking load — called in a thread so the event loop stays free."""
    global _whisper
    if _whisper is None:
        logger.info("Loading Whisper '%s' (cpu / int8) …", WHISPER_MODEL_SIZE)
        _whisper = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
        logger.info("Whisper model ready")
    return _whisper


async def get_whisper() -> WhisperModel:
    """Async wrapper — loads model in a thread on first call."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _load_whisper_sync)


# ---------------------------------------------------------------------------
# Embedded HTML UI
# ---------------------------------------------------------------------------

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Whisper - Gemma PoC</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: system-ui, -apple-system, sans-serif;
      background: #0f0f11; color: #e8e8e8;
      padding: 28px 32px; max-width: 960px; margin: auto;
    }
    h1 { font-size: 1.25rem; font-weight: 700; margin-bottom: 4px; }
    .subtitle { font-size: 0.82rem; color: #888; margin-bottom: 24px; }
    .section-label {
      font-size: 0.7rem; font-weight: 700; color: #71717a;
      text-transform: uppercase; letter-spacing: .06em; margin-bottom: 8px;
    }
    .transcript-panel {
      background: #18181b; border: 1px solid #27272a; border-radius: 10px;
      padding: 14px 16px; min-height: 72px; margin-bottom: 20px;
      font-size: 0.9rem; line-height: 1.7; white-space: pre-wrap;
    }
    .transcript-panel.muted { color: #52525b; font-style: italic; }
    .mic-controls { display: flex; align-items: center; gap: 12px; margin-bottom: 28px; }
    button {
      padding: 9px 20px; border: none; border-radius: 8px;
      font-size: 0.88rem; font-weight: 600; cursor: pointer; transition: opacity .15s;
    }
    button:disabled { opacity: .35; cursor: default; }
    #startBtn { background: #3b82f6; color: #fff; }
    #stopBtn  { background: #ef4444; color: #fff; }
    .mic-status { font-size: 0.83rem; color: #a78bfa; }
    .qa-row { display: flex; gap: 10px; margin-bottom: 20px; }
    #questionInput {
      flex: 1; padding: 10px 14px; border-radius: 8px;
      background: #27272a; border: 1px solid #3f3f46; color: #e8e8e8;
      font-size: 0.9rem;
    }
    #questionInput:focus { outline: none; border-color: #6366f1; }
    #askBtn { background: #6366f1; color: #fff; white-space: nowrap; }
    .qa-entry {
      border: 1px solid #27272a; border-radius: 10px;
      background: #18181b; overflow: hidden; margin-bottom: 14px;
    }
    .qa-q {
      padding: 10px 16px; border-bottom: 1px solid #27272a;
      font-size: 0.82rem; color: #a1a1aa;
    }
    .qa-q span { color: #e8e8e8; font-weight: 600; }
    .qa-a {
      padding: 12px 16px; font-size: 0.9rem; line-height: 1.7;
      white-space: pre-wrap; min-height: 1.6rem;
    }
    .qa-a.thinking { color: #52525b; font-style: italic; }
  </style>
</head>
<body>
  <h1>Whisper - Gemma PoC</h1>
  <p class="subtitle">Teacher speaks - Whisper transcribes. Student types a question - Gemma 4 (9b) answers.</p>

  <div class="section-label">Live Transcript (Whisper)</div>
  <div class="transcript-panel muted" id="transcript">Transcript will appear here as you record...</div>

  <div class="mic-controls">
    <button id="startBtn">&#9679; Start Recording</button>
    <button id="stopBtn" disabled>&#9632; Stop</button>
    <span class="mic-status" id="micStatus">Idle</span>
  </div>

  <div class="section-label">Ask Gemma 4 a question</div>
  <div class="qa-row">
    <input id="questionInput" type="text" placeholder="Type your question and press Enter or click Ask" />
    <button id="askBtn">Ask</button>
  </div>

  <div id="answers"></div>

  <script>
    var startBtn      = document.getElementById('startBtn');
    var stopBtn       = document.getElementById('stopBtn');
    var micStatus     = document.getElementById('micStatus');
    var transcriptEl  = document.getElementById('transcript');
    var questionInput = document.getElementById('questionInput');
    var askBtn        = document.getElementById('askBtn');
    var answersEl     = document.getElementById('answers');

    var mediaRecorder   = null;
    var audioChunks    = [];
    var ws             = null;
    var currentAnsEl   = null;
    var lastTranscript = '';

    function connectWS() {
      ws = new WebSocket('ws://' + location.host + '/ws/audio');
      ws.binaryType = 'arraybuffer';
      ws.onmessage = function(evt) {
        var msg = JSON.parse(evt.data);
        if (msg.type === 'transcript') {
          var text = msg.text || '(nothing recognised - try speaking louder)';
          lastTranscript = text;
          if (transcriptEl.classList.contains('muted')) {
            transcriptEl.textContent = '';
            transcriptEl.classList.remove('muted');
          }
          transcriptEl.textContent += (transcriptEl.textContent ? '\\n' : '') + text;
          micStatus.textContent = 'Idle';
          startBtn.disabled = false;
        } else if (msg.type === 'token') {
          if (currentAnsEl && currentAnsEl.classList.contains('thinking')) {
            currentAnsEl.textContent = '';
            currentAnsEl.classList.remove('thinking');
          }
          if (currentAnsEl) { currentAnsEl.textContent += msg.text; }
        } else if (msg.type === 'done') {
          currentAnsEl = null;
          questionInput.disabled = false;
          askBtn.disabled = false;
          questionInput.focus();
        } else if (msg.type === 'error') {
          if (currentAnsEl) {
            currentAnsEl.textContent = 'Error: ' + msg.detail;
            currentAnsEl.classList.remove('thinking');
          }
          questionInput.disabled = false;
          askBtn.disabled = false;
          startBtn.disabled = false;
        }
      };
      ws.onerror = function() { micStatus.textContent = 'WebSocket error'; };
      ws.onclose = function() { micStatus.textContent = 'Disconnected'; };
    }

    function ensureWS() {
      if (!ws || ws.readyState !== WebSocket.OPEN) { connectWS(); }
    }

    startBtn.addEventListener('click', function() {
      micStatus.textContent = 'Requesting mic...';
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        micStatus.textContent = 'ERROR: open this page in Chrome or Firefox at http://localhost:8765';
        return;
      }
      navigator.mediaDevices.getUserMedia({ audio: true }).then(function(stream) {
        ensureWS();
        audioChunks = [];
        mediaRecorder = new MediaRecorder(stream);
        mediaRecorder.ondataavailable = function(e) {
          if (e.data.size > 0) { audioChunks.push(e.data); }
        };
        mediaRecorder.onstop = function() {
          stream.getTracks().forEach(function(t) { t.stop(); });
          var blob = new Blob(audioChunks, { type: mediaRecorder.mimeType });
          blob.arrayBuffer().then(function(buf) {
            ws.send(buf);
            micStatus.textContent = 'Transcribing...';
          });
        };
        mediaRecorder.start();
        micStatus.textContent = 'Recording...';
        startBtn.disabled = true;
        stopBtn.disabled = false;
      }).catch(function(e) {
        micStatus.textContent = 'Mic denied: ' + e.message;
      });
    });

    stopBtn.addEventListener('click', function() {
      stopBtn.disabled = true;
      mediaRecorder.stop();
    });

    function sendQuestion() {
      var q = questionInput.value.trim();
      if (!q) { return; }
      ensureWS();
      var entry = document.createElement('div');
      entry.className = 'qa-entry';
      var qDiv = document.createElement('div');
      qDiv.className = 'qa-q';
      qDiv.textContent = 'You asked: ';
      var span = document.createElement('span');
      span.textContent = q;
      qDiv.appendChild(span);
      var aDiv = document.createElement('div');
      aDiv.className = 'qa-a thinking';
      aDiv.textContent = 'Gemma is thinking...';
      entry.appendChild(qDiv);
      entry.appendChild(aDiv);
      answersEl.prepend(entry);
      currentAnsEl = aDiv;
      questionInput.value = '';
      questionInput.disabled = true;
      askBtn.disabled = true;
      ws.send(JSON.stringify({ type: 'question', text: q, transcript: lastTranscript }));
    }

    askBtn.addEventListener('click', sendQuestion);
    questionInput.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') { sendQuestion(); }
    });

    connectWS();
  </script>
</body>
</html>"""



# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


@app.websocket("/ws/audio")
async def audio_ws(websocket: WebSocket):
    await websocket.accept()
    logger.info("WS client connected")

    try:
        while True:
            message = await websocket.receive()

            # ── Binary message: audio blob → Whisper (loaded lazily in thread) ──
            if "bytes" in message and message["bytes"]:
                audio_bytes = message["bytes"]
                model = await get_whisper()  # non-blocking: runs in thread executor

                def _transcribe():
                    segs, info = model.transcribe(
                        io.BytesIO(audio_bytes),
                        beam_size=1,
                        best_of=1,
                        temperature=0.0,
                        vad_filter=True,
                        vad_parameters={"min_silence_duration_ms": 500},
                    )
                    return " ".join(s.text.strip() for s in segs).strip(), info.language

                try:
                    loop = asyncio.get_event_loop()
                    transcript, lang = await loop.run_in_executor(_executor, _transcribe)
                    logger.info("Whisper [lang=%s]: %s", lang, transcript)
                except Exception as exc:
                    logger.error("Transcription error: %s", exc)
                    await websocket.send_text(json.dumps({"type": "error", "detail": str(exc)}))
                    continue

                await websocket.send_text(json.dumps({"type": "transcript", "text": transcript}))

            # ── Text message: typed question → Gemma streamed response ──
            elif "text" in message and message["text"]:
                try:
                    data = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue

                if data.get("type") != "question" or not data.get("text", "").strip():
                    continue

                question = data["text"].strip()
                transcript_ctx = data.get("transcript", "").strip()
                logger.info("Gemma question: %s | transcript: %.60s", question, transcript_ctx)

                if transcript_ctx:
                    messages = [
                        {
                            "role": "system",
                            "content": (
                                "You are a helpful teaching assistant. "
                                "A teacher just said the following in class:\n\n"
                                f"{transcript_ctx}\n\n"
                                "Answer the student's question based on this context."
                            ),
                        },
                        {"role": "user", "content": question},
                    ]
                else:
                    messages = [{"role": "user", "content": question}]

                try:
                    async with httpx.AsyncClient(timeout=120.0) as client:
                        async with client.stream(
                            "POST",
                            f"{OLLAMA_BASE_URL}/api/chat",
                            json={
                                "model": OLLAMA_MODEL,
                                "messages": messages,
                                "stream": True,
                            },
                        ) as resp:
                            resp.raise_for_status()
                            async for line in resp.aiter_lines():
                                if not line:
                                    continue
                                try:
                                    chunk = json.loads(line)
                                except json.JSONDecodeError:
                                    continue
                                token = chunk.get("message", {}).get("content", "")
                                if token:
                                    await websocket.send_text(
                                        json.dumps({"type": "token", "text": token})
                                    )
                                if chunk.get("done"):
                                    break
                except Exception as exc:
                    logger.error("Ollama/Gemma error: %s", exc)
                    await websocket.send_text(json.dumps({"type": "error", "detail": str(exc)}))

                await websocket.send_text(json.dumps({"type": "done"}))

    except WebSocketDisconnect:
        logger.info("WS client disconnected")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="info")
