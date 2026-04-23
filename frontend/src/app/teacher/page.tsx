"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import LessonUpload from "@/components/LessonUpload";
import LessonDetail from "@/components/LessonDetail";
import StudentProgress from "@/components/StudentProgress";
import TeacherChat from "@/components/TeacherChat";
import ConfirmDialog from "@/components/ConfirmDialog";

const TEACHER_ID = 1;
const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const WS_BASE = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000";

type Tab = "lessons" | "students" | "sessions" | "chat";

// ── Types ─────────────────────────────────────────────────────────────────────
interface Lesson {
  id: number;
  title: string;
  created_at: string;
  summary: string | null;
  file_count: number;
}

interface SessionInfo {
  id: number;
  title: string;
  status: "live" | "ended";
}

interface TranscriptChunkResponse {
  id: number;
  content: string;
  timestamp_ms: number;
}

type WaveState = "paused" | "waiting" | "listening" | "error";

const WAVE_BAR_COUNT = 32;
const IDLE_WAVE = Array.from({ length: WAVE_BAR_COUNT }, (_, idx) => 0.1 + ((idx % 5) * 0.025));
const TRANSCRIBE_MIN_CHUNK_SECONDS = 0.6;
const TRANSCRIBE_MAX_CHUNK_SECONDS = 6;
const TRANSCRIBE_MIN_RMS = 0.0015;
const TRANSCRIBE_VOICE_ON_RMS = 0.003;
const TRANSCRIBE_VOICE_OFF_RMS = 0.002;
const TRANSCRIBE_SILENCE_HOLD_SECONDS = 0.35;

function writeWavHeader(view: DataView, sampleCount: number, sampleRate: number) {
  const writeString = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i += 1) {
      view.setUint8(offset + i, value.charCodeAt(i));
    }
  };

  writeString(0, "RIFF");
  view.setUint32(4, 36 + sampleCount * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, sampleCount * 2, true);
}

function encodeWav(chunks: Float32Array[], sampleRate: number) {
  const sampleCount = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const buffer = new ArrayBuffer(44 + sampleCount * 2);
  const view = new DataView(buffer);
  writeWavHeader(view, sampleCount, sampleRate);

  let offset = 44;
  for (const chunk of chunks) {
    for (let i = 0; i < chunk.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, chunk[i]));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
      offset += 2;
    }
  }

  return buffer;
}

function rmsLevel(chunk: Float32Array) {
  if (chunk.length === 0) return 0;
  let total = 0;
  for (let i = 0; i < chunk.length; i += 1) {
    total += chunk[i] * chunk[i];
  }
  return Math.sqrt(total / chunk.length);
}

function WaveformCard({ state, levels }: { state: WaveState; levels: number[] }) {
  const dotColor = state === "error"
    ? "var(--error)"
    : state === "listening"
      ? "var(--action)"
      : state === "waiting"
        ? "rgba(58,102,219,0.45)"
        : "var(--ink-muted)";

  const stateLabel = state === "error"
    ? "Mic Error"
    : state === "listening"
      ? "Listening"
      : state === "waiting"
        ? "Waiting…"
        : "Paused";

  return (
    <div
      style={{
        background: "var(--paper-shade)",
        borderRadius: 14,
        padding: "16px 18px",
        display: "flex",
        alignItems: "center",
        gap: 14,
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: 10,
          height: 10,
          borderRadius: 999,
          flex: "none",
          background: dotColor,
          boxShadow: state === "listening" ? "0 0 0 0 rgba(58,102,219,.45)" : "none",
          animation: state === "listening" ? "ll-wave-pulse 1.4s ease-out infinite" : "none",
        }}
      />
      <div style={{ flex: 1, height: 56, display: "flex", alignItems: "center", gap: 4 }}>
        {levels.map((level, idx) => (
          <span
            key={`${idx}-${level.toFixed(3)}`}
            aria-hidden="true"
            style={{
              flex: 1,
              minHeight: 4,
              height: `${6 + level * 42}px`,
              borderRadius: 3,
              background: state === "error"
                ? "var(--error)"
                : state === "paused"
                  ? "rgba(43,43,43,0.3)"
                  : state === "waiting"
                    ? "rgba(58,102,219,0.45)"
                    : "var(--action)",
              transformOrigin: "center",
              transition: "height .12s ease, background-color .2s ease",
            }}
          />
        ))}
      </div>
      <div
        style={{
          font: "600 11px/12px var(--font-sans)",
          color: "var(--ink-muted)",
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          minWidth: 84,
          textAlign: "right",
        }}
      >
        {stateLabel}
      </div>
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function formatDate(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function pct(v: number | null) {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

// ── Sub-views ─────────────────────────────────────────────────────────────────
function LessonsPanel({ teacherId }: { teacherId: number }) {
  const [lessons, setLessons] = useState<Lesson[]>([]);
  const [showUpload, setShowUpload] = useState(false);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [confirmId, setConfirmId] = useState<number | null>(null);
  const [selectedLessonId, setSelectedLessonId] = useState<number | null>(null);

  const load = useCallback(() => {
    fetch(`${API}/teacher/lessons?teacher_id=${teacherId}`)
      .then((r) => r.json())
      .then(setLessons)
      .catch(console.error);
  }, [teacherId]);

  useEffect(() => { load(); }, [load]);

  // Poll every 8 s while any lesson is still pending background analysis
  useEffect(() => {
    const hasPending = lessons.some((l) => l.summary === null);
    if (!hasPending) return;
    const id = setInterval(load, 8000);
    return () => clearInterval(id);
  }, [lessons, load]);

  const deleteLesson = async (id: number) => {
    setConfirmId(null);
    setDeleting(id);
    try {
      await fetch(`${API}/teacher/lessons/${id}`, { method: "DELETE" });
      setLessons((prev) => prev.filter((l) => l.id !== id));
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {selectedLessonId !== null ? (
        <LessonDetail
          lessonId={selectedLessonId}
          onBack={() => setSelectedLessonId(null)}
        />
      ) : (
        <>
      <h2 className="ll-heading">Lessons</h2>

      {showUpload && (
        <div className="ll-card">
          <LessonUpload
            teacherId={teacherId}
            onUploaded={() => { setShowUpload(false); load(); }}
            onCancel={() => setShowUpload(false)}
          />
        </div>
      )}

      {lessons.length === 0 ? (
        <p className="ll-body" style={{ color: "var(--ink-muted)" }}>No lessons uploaded yet.</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {lessons.map((l) => (
            <div
              key={l.id}
              className="ll-card"
              onClick={() => setSelectedLessonId(l.id)}
              style={{ cursor: "pointer", transition: "box-shadow 0.15s" }}
              onMouseEnter={(e) => (e.currentTarget.style.boxShadow = "0 2px 12px rgba(58,102,219,0.12)")}
              onMouseLeave={(e) => (e.currentTarget.style.boxShadow = "")}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                <p className="ll-subheading">{l.title}</p>
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginLeft: 16, flexShrink: 0 }}>
                  <span className="ll-pill">
                    {l.file_count ?? 1} {(l.file_count ?? 1) === 1 ? "file" : "files"}
                  </span>
                  <p style={{ fontSize: 13, color: "var(--ink-muted)", whiteSpace: "nowrap" }}>
                    {formatDate(l.created_at)}
                  </p>
                  <button
                    onClick={(e) => { e.stopPropagation(); setConfirmId(l.id); }}
                    disabled={deleting === l.id}
                    style={{
                      background: "none", border: "none", cursor: "pointer",
                      color: deleting === l.id ? "var(--ink-muted)" : "var(--error)",
                      fontFamily: "var(--font-sans)", fontSize: 13, padding: "2px 6px",
                    }}
                  >
                    {deleting === l.id ? "Deleting…" : "Delete"}
                  </button>
                </div>
              </div>
              {l.summary ? (
                <p className="ll-body" style={{ marginTop: 8, color: "var(--ink-muted)", fontSize: 14 }}>
                  {l.summary}
                </p>
              ) : (
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 10 }}>
                  <svg width="13" height="13" viewBox="0 0 13 13" style={{ flexShrink: 0, animation: "ll-spin 1.1s linear infinite" }} aria-hidden="true">
                    <circle cx="6.5" cy="6.5" r="5" fill="none" stroke="var(--ink-soft)" strokeWidth="2" />
                    <path d="M6.5 1.5 A5 5 0 0 1 11.5 6.5" fill="none" stroke="var(--action)" strokeWidth="2" strokeLinecap="round" />
                  </svg>
                  <p style={{ fontSize: 13, color: "var(--ink-muted)", fontFamily: "var(--font-sans)" }}>
                    AI analysis in progress — check back in a few minutes
                  </p>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {!showUpload && (
        <div>
          <button className="ll-chip" onClick={() => setShowUpload(true)}>
            Upload new lesson
          </button>
        </div>
      )}

      {confirmId !== null && (() => {
        const lesson = lessons.find((l) => l.id === confirmId);
        return (
          <ConfirmDialog
            title="Delete lesson"
            message={`"${lesson?.title}" and all its files will be permanently deleted. This cannot be undone.`}
            confirmLabel="Delete lesson"
            dangerous
            onConfirm={() => deleteLesson(confirmId)}
            onCancel={() => setConfirmId(null)}
          />
        );
      })()}
        </>
      )}
    </div>
  );
}

function SessionsPanel({ teacherId }: { teacherId: number }) {
  const [lessons, setLessons] = useState<Lesson[]>([]);
  const [selectedLessonId, setSelectedLessonId] = useState<number | null>(null);
  const [activeSession, setActiveSession] = useState<SessionInfo | null>(null);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [liveTranscripts, setLiveTranscripts] = useState<string[]>([]);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [waveLevels, setWaveLevels] = useState<number[]>(IDLE_WAVE);
  const [waveState, setWaveState] = useState<WaveState>("paused");
  const socketRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const activeSessionIdRef = useRef<number | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const gainRef = useRef<GainNode | null>(null);
  const animationFrameRef = useRef<number | null>(null);
  const audioChunksRef = useRef<Float32Array[]>([]);
  const chunkDurationSecondsRef = useRef<number>(0);
  const silenceDurationSecondsRef = useRef<number>(0);
  const speechActiveRef = useRef<boolean>(false);
  const lastFlushAtRef = useRef<number>(0);
  const sampleRateRef = useRef<number>(16000);
  const transcriptContainerRef = useRef<HTMLDivElement>(null);
  const transcriptEndRef = useRef<HTMLDivElement>(null);
  const shouldAutoScrollRef = useRef(true);

  useEffect(() => {
    const transcriptContainer = transcriptContainerRef.current;
    if (!transcriptContainer || !shouldAutoScrollRef.current) return;
    transcriptContainer.scrollTop = transcriptContainer.scrollHeight;
  }, [liveTranscripts]);

  const applyPersistedTranscript = useCallback((sessionId: number, persistedLines: string[]) => {
    setLiveTranscripts((prev) => {
      const isActiveSession = activeSessionIdRef.current === sessionId;
      if (!isActiveSession) return persistedLines;

      // Keep already-rendered live lines while a running session catches up in DB.
      if (persistedLines.length === 0 && prev.length > 0) return prev;
      if (persistedLines.length < prev.length) return prev;

      return persistedLines;
    });
  }, []);

  useEffect(() => {
    fetch(`${API}/teacher/lessons?teacher_id=${teacherId}`)
      .then((r) => r.json())
      .then((data: Lesson[]) => {
        setLessons(data);
        if (data.length > 0) {
          setSelectedLessonId((prev) => prev ?? data[0].id);
        }
      })
      .catch((e) => {
        console.error(e);
        setSessionError("Could not load lessons for session start.");
      });
  }, [teacherId]);

  useEffect(() => {
    return () => {
      const socket = socketRef.current;
      const stream = streamRef.current;
      if (stream) stream.getTracks().forEach((track) => track.stop());
      if (socket && socket.readyState === WebSocket.OPEN) socket.close();
      if (animationFrameRef.current !== null) cancelAnimationFrame(animationFrameRef.current);
      processorRef.current?.disconnect();
      analyserRef.current?.disconnect();
      gainRef.current?.disconnect();
      void audioContextRef.current?.close();
    };
  }, []);

  const refreshTranscript = useCallback(async (sessionId: number) => {
    try {
      const res = await fetch(`${API}/session/${sessionId}/transcript`);
      if (!res.ok) return;
      const data = await res.json() as TranscriptChunkResponse[];
      applyPersistedTranscript(sessionId, data.map((chunk) => chunk.content));
    } catch (error) {
      console.error(error);
    }
  }, [applyPersistedTranscript]);

  useEffect(() => {
    if (!activeSession?.id) return;

    let cancelled = false;
    const sessionId = activeSession.id;

    const loadTranscript = async () => {
      try {
        const res = await fetch(`${API}/session/${sessionId}/transcript`);
        if (!res.ok) return;
        const data = await res.json() as TranscriptChunkResponse[];
        if (!cancelled) {
          applyPersistedTranscript(sessionId, data.map((chunk) => chunk.content));
        }
      } catch (error) {
        console.error(error);
      }
    };

    void loadTranscript();
    const intervalId = window.setInterval(() => {
      void loadTranscript();
    }, 500);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [activeSession?.id, applyPersistedTranscript]);

  const resetAudioPipeline = useCallback(() => {
    if (animationFrameRef.current !== null) {
      cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }
    processorRef.current?.disconnect();
    analyserRef.current?.disconnect();
    gainRef.current?.disconnect();
    void audioContextRef.current?.close();
    processorRef.current = null;
    analyserRef.current = null;
    gainRef.current = null;
    audioContextRef.current = null;
    audioChunksRef.current = [];
    chunkDurationSecondsRef.current = 0;
    silenceDurationSecondsRef.current = 0;
    speechActiveRef.current = false;
    lastFlushAtRef.current = 0;
    setWaveLevels(IDLE_WAVE);
    setWaveState(sessionError ? "error" : "paused");
  }, [sessionError]);

  const flushAudioChunk = useCallback((force = false): boolean => {
    const socket = socketRef.current;
    const audioContext = audioContextRef.current;
    const chunks = audioChunksRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN || !audioContext || chunks.length === 0) {
      return false;
    }

    const sampleCount = chunks.reduce((total, chunk) => total + chunk.length, 0);
    if (!force && sampleCount < sampleRateRef.current * TRANSCRIBE_MIN_CHUNK_SECONDS) {
      return false;
    }

    const mergedRms = chunks.reduce((max, chunk) => Math.max(max, rmsLevel(chunk)), 0);
    if (!force && mergedRms < TRANSCRIBE_MIN_RMS) {
      audioChunksRef.current = [];
      chunkDurationSecondsRef.current = 0;
      silenceDurationSecondsRef.current = 0;
      speechActiveRef.current = false;
      return false;
    }

    socket.send(encodeWav(chunks, sampleRateRef.current));
    audioChunksRef.current = [];
    chunkDurationSecondsRef.current = 0;
    silenceDurationSecondsRef.current = 0;
    speechActiveRef.current = false;
    if (activeSessionIdRef.current != null) {
      window.setTimeout(() => {
        void refreshTranscript(activeSessionIdRef.current as number);
      }, 250);
    }
    lastFlushAtRef.current = audioContext.currentTime;
    return true;
  }, [refreshTranscript]);

  const startWaveAnimation = useCallback(() => {
    const analyser = analyserRef.current;
    if (!analyser) return;
    const data = new Uint8Array(analyser.frequencyBinCount);

    const tick = () => {
      analyser.getByteFrequencyData(data);
      const stride = Math.max(1, Math.floor(data.length / WAVE_BAR_COUNT));
      const nextLevels = Array.from({ length: WAVE_BAR_COUNT }, (_, idx) => {
        const start = idx * stride;
        const end = Math.min(start + stride, data.length);
        let total = 0;
        for (let i = start; i < end; i += 1) total += data[i];
        return Math.max(0.08, Math.min(1, (total / Math.max(1, end - start)) / 255));
      });
      const level = nextLevels.reduce((sum, value) => sum + value, 0) / nextLevels.length;
      setWaveLevels(nextLevels);
      setWaveState((prev) => {
        if (prev === "error") return prev;
        if (!activeSession) return "paused";
        return level > 0.18 ? "listening" : "waiting";
      });
      animationFrameRef.current = requestAnimationFrame(tick);
    };

    animationFrameRef.current = requestAnimationFrame(tick);
  }, [activeSession]);

  const startLiveSession = async () => {
    if (selectedLessonId == null) {
      setSessionError("Select a lesson before starting a session.");
      return;
    }

    setStarting(true);
    setSessionError(null);

    try {
      const lesson = lessons.find((l) => l.id === selectedLessonId);
      const response = await fetch(`${API}/session/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          teacher_id: teacherId,
          lesson_id: selectedLessonId,
          title: lesson ? `Live: ${lesson.title}` : undefined,
        }),
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(errorText || "Failed to start session.");
      }

      const session = (await response.json()) as SessionInfo;
      activeSessionIdRef.current = session.id;

      const mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = mediaStream;
      const ws = new WebSocket(`${WS_BASE}/teacher/ws/${teacherId}/transcribe/${session.id}`);
      socketRef.current = ws;
      setWaveState("waiting");

      ws.onopen = () => {
        const audioContext = new AudioContext({ sampleRate: 16000 });
        const source = audioContext.createMediaStreamSource(mediaStream);
        const analyser = audioContext.createAnalyser();
        analyser.fftSize = 128;
        analyser.smoothingTimeConstant = 0.75;

        const processor = audioContext.createScriptProcessor(4096, 1, 1);
        const gain = audioContext.createGain();
        gain.gain.value = 0;

        sampleRateRef.current = audioContext.sampleRate;
        audioContextRef.current = audioContext;
        analyserRef.current = analyser;
        processorRef.current = processor;
        gainRef.current = gain;
        audioChunksRef.current = [];
        chunkDurationSecondsRef.current = 0;
        silenceDurationSecondsRef.current = 0;
        speechActiveRef.current = false;
        lastFlushAtRef.current = audioContext.currentTime;

        void audioContext.resume();

        source.connect(analyser);
        analyser.connect(processor);
        processor.connect(gain);
        gain.connect(audioContext.destination);

        processor.onaudioprocess = (event) => {
          const input = event.inputBuffer.getChannelData(0);
          const copy = new Float32Array(input.length);
          copy.set(input);
          const seconds = copy.length / sampleRateRef.current;
          const level = rmsLevel(copy);
          const speechDetected = level >= TRANSCRIBE_VOICE_ON_RMS;
          const silenceDetected = level < TRANSCRIBE_VOICE_OFF_RMS;

          if (speechDetected) {
            speechActiveRef.current = true;
            silenceDurationSecondsRef.current = 0;
          }

          if (!speechActiveRef.current) {
            return;
          }

          audioChunksRef.current.push(copy);
          chunkDurationSecondsRef.current += seconds;

          if (silenceDetected) {
            silenceDurationSecondsRef.current += seconds;
          } else {
            silenceDurationSecondsRef.current = 0;
          }

          // End segment at a natural pause once minimum segment length is reached.
          if (
            silenceDurationSecondsRef.current >= TRANSCRIBE_SILENCE_HOLD_SECONDS
            && chunkDurationSecondsRef.current >= TRANSCRIBE_MIN_CHUNK_SECONDS
          ) {
            flushAudioChunk();
            return;
          }

          // Safety flush for very long uninterrupted speech.
          if (chunkDurationSecondsRef.current >= TRANSCRIBE_MAX_CHUNK_SECONDS) {
            flushAudioChunk();
          }
        };

        startWaveAnimation();
      };

      ws.onmessage = (evt) => {
        try {
          const payload = JSON.parse(evt.data) as {
            type?: string;
            text?: string;
            detail?: string;
          };
          if (payload.type === "transcript" && payload.text) {
            setLiveTranscripts((prev) => [...prev.slice(-20), payload.text as string]);
            setSessionError(null);
            void refreshTranscript(session.id);
          }
          if (payload.type === "rationalized" && payload.text && typeof (payload as { replaces?: number }).replaces === "number") {
            const replaces = (payload as { replaces: number }).replaces;
            setLiveTranscripts((prev) => [
              ...prev.slice(0, Math.max(0, prev.length - replaces)),
              `✦ ${payload.text as string}`,
            ]);
          }
          if (payload.type === "error") {
            setSessionError(payload.detail ?? "Transcription error.");
            setWaveState("error");
          }
        } catch {
          // Ignore non-JSON frames.
        }
      };

      ws.onerror = () => {
        setSessionError("Transcription socket failed.");
        setWaveState("error");
      };

      ws.onclose = () => {
        flushAudioChunk(true);
        mediaStream.getTracks().forEach((track) => track.stop());
        socketRef.current = null;
        streamRef.current = null;
        resetAudioPipeline();
      };

      setActiveSession(session);
      setLiveTranscripts([]);
    } catch (e) {
      console.error(e);
      setSessionError(e instanceof Error ? e.message : "Could not start live session.");
      setWaveState("error");
    } finally {
      setStarting(false);
    }
  };

  const stopLiveSession = async () => {
    if (!activeSession) return;
    setStopping(true);
    setSessionError(null);

    try {
      const socket = socketRef.current;
      const stream = streamRef.current;

      flushAudioChunk(true);
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "end_session" }));
      }
      if (stream) stream.getTracks().forEach((track) => track.stop());
      if (socket) socket.close();

      await fetch(`${API}/session/${activeSession.id}/end`, { method: "POST" });
      await refreshTranscript(activeSession.id);
      socketRef.current = null;
      streamRef.current = null;
      activeSessionIdRef.current = null;
      setActiveSession(null);
      resetAudioPipeline();
    } catch (e) {
      console.error(e);
      setSessionError("Could not end the active session.");
      setWaveState("error");
    } finally {
      setStopping(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <h2 className="ll-heading">Sessions</h2>

      <div className="ll-card" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <p className="ll-label">Start live session</p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" }}>
          <select
            value={selectedLessonId ?? ""}
            onChange={(e) => setSelectedLessonId(Number(e.target.value))}
            disabled={activeSession !== null || lessons.length === 0}
            style={{
              minWidth: 260,
              border: "1px solid var(--ink-soft)",
              borderRadius: "var(--radius-md)",
              padding: "10px 12px",
              fontFamily: "var(--font-sans)",
              fontSize: 14,
              background: "var(--paper)",
            }}
          >
            {lessons.map((lesson) => (
              <option key={lesson.id} value={lesson.id}>{lesson.title}</option>
            ))}
          </select>

          {activeSession ? (
            <button className="ll-chip" onClick={stopLiveSession} disabled={stopping}>
              {stopping ? "Stopping…" : `Stop Session #${activeSession.id}`}
            </button>
          ) : (
            <button className="ll-chip" onClick={startLiveSession} disabled={starting || lessons.length === 0}>
              {starting ? "Starting…" : "Start Session + Transcription"}
            </button>
          )}
        </div>

        {activeSession && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <p className="ll-body" style={{ color: "var(--ink-muted)" }}>
              Live now: {activeSession.title} (session {activeSession.id})
            </p>
            <WaveformCard state={waveState} levels={waveLevels} />
          </div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <p className="ll-label">Live transcript</p>
          {liveTranscripts.length === 0 ? (
            <div
              style={{
                padding: "16px 18px",
                borderRadius: 14,
                background: "var(--paper-shade)",
                color: "var(--ink-muted)",
              }}
            >
              {activeSession ? "Listening for speech… transcript will appear here in real time." : "Start a live session to see transcript updates."}
            </div>
          ) : (
            <div
              ref={transcriptContainerRef}
              onScroll={(event) => {
                const element = event.currentTarget;
                const distanceFromBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
                shouldAutoScrollRef.current = distanceFromBottom < 48;
              }}
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 8,
                maxHeight: 340,
                overflowY: "auto",
                paddingRight: 4,
              }}
            >
              {liveTranscripts.map((line, idx) => {
                const isLatest = idx === liveTranscripts.length - 1;
                const isRationalized = line.startsWith("✦ ");
                const displayLine = isRationalized ? line.slice(2).trimStart() : line;
                return (
                <div
                  key={`${idx}-${line.slice(0, 12)}`}
                  style={{
                    background: isRationalized ? "var(--paper)" : "var(--paper-shade)",
                    borderRadius: 14,
                    padding: "14px 16px",
                    borderLeft: isLatest
                      ? "4px solid var(--action)"
                      : isRationalized
                      ? "4px solid var(--ink-muted)"
                      : "4px solid transparent",
                  }}
                >
                  <p
                    className="ll-body"
                    style={{
                      color: isLatest ? "var(--ink)" : "var(--ink-muted)",
                      fontStyle: isRationalized ? "italic" : "normal",
                    }}
                  >
                    {displayLine}
                  </p>
                </div>
                );
              })}
              <div ref={transcriptEndRef} />
            </div>
          )}
        </div>

        {sessionError && (
          <p className="ll-body" style={{ color: "var(--error)" }}>{sessionError}</p>
        )}
      </div>
    </div>
  );
}

// ── Nav item ──────────────────────────────────────────────────────────────────
function NavItem({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: "block",
        width: "100%",
        textAlign: "left",
        padding: "10px 16px",
        borderRadius: "var(--radius-md)",
        border: "none",
        background: active ? "var(--action)" : "transparent",
        color: active ? "#fff" : "var(--ink)",
        fontFamily: "var(--font-sans)",
        fontWeight: active ? 600 : 400,
        fontSize: 15,
        cursor: "pointer",
        transition: "background var(--dur-quick) var(--ease)",
      }}
    >
      {label}
    </button>
  );
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
export default function TeacherDashboard() {
  const [tab, setTab] = useState<Tab>("lessons");

  return (
    <div
      style={{
        display: "flex",
        height: "100dvh",
        fontFamily: "var(--font-sans)",
        background: "var(--paper)",
      }}
    >
      {/* Sidebar */}
      <aside
        style={{
          width: 220,
          flexShrink: 0,
          background: "var(--paper-shade)",
          borderRight: "1px solid var(--ink-soft)",
          display: "flex",
          flexDirection: "column",
          padding: "28px 16px",
          gap: 4,
        }}
      >
        {/* Wordmark */}
        <div style={{ marginBottom: 32 }}>
          <p style={{ fontWeight: 700, fontSize: 20, color: "var(--ink)", fontFamily: "var(--font-sans)" }}>
            Looplense
          </p>
          <p className="ll-label" style={{ marginTop: 2 }}>Teacher</p>
        </div>

        <NavItem label="Lessons" active={tab === "lessons"} onClick={() => setTab("lessons")} />
        <NavItem label="Students" active={tab === "students"} onClick={() => setTab("students")} />
        <NavItem label="Sessions" active={tab === "sessions"} onClick={() => setTab("sessions")} />
        <NavItem label="AI Assistant" active={tab === "chat"} onClick={() => setTab("chat")} />
      </aside>

      {/* Main content */}
      <main
        style={{
          flex: 1,
          overflowY: tab === "chat" ? "hidden" : "auto",
          padding: "40px 48px",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {tab === "lessons"  && <LessonsPanel teacherId={TEACHER_ID} />}
        {tab === "students" && <StudentProgress />}
        {tab === "sessions" && <SessionsPanel teacherId={TEACHER_ID} />}
        {tab === "chat"     && (
          <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <h2 className="ll-heading" style={{ marginBottom: 24 }}>AI Assistant</h2>
            <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
              <TeacherChat teacherId={TEACHER_ID} />
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
