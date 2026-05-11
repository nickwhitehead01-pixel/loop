"use client";
import { useCallback, useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const WS_BASE = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000";

// ── Types ────────────────────────────────────────────────────────────────────
//
// Mirror the backend pydantic shapes loosely. We keep them as plain `string`
// unions instead of literal enums so a backend change can't silently break
// rendering — unknown grade values still display, they just won't get a
// coloured pill.

type QuizStatus = "draft" | "sent" | "closed";
type QuizGrade = "correct" | "partial" | "incorrect";

interface QuizQuestion {
  id: number;
  quiz_id: number;
  session_id: number;
  question_text: string;
  correct_answer: string;
  topic_tag: string | null;
  status: QuizStatus;
  source: string;
  time_limit_seconds: number;
  sent_at: string | null;
  closed_at: string | null;
  created_at: string;
}

interface Quiz {
  id: number;
  session_id: number;
  mode: string;
  started_at: string;
  questions: QuizQuestion[];
}

interface Suggestion {
  question_text: string;
  correct_answer: string;
  topic_tag: string | null;
}

// One row on the live answer board. Grade + rationale are null until the
// grader runs after the question closes.
interface AnswerRow {
  id: number;
  question_id: number;
  pupil_id: number;
  pupil_answer: string;
  grade: QuizGrade | null;
  grader_rationale: string | null;
  submitted_at: string | null;
}

interface UserLite {
  id: number;
  name: string;
}

// Draft form state — used for both AI-suggested and manually-entered questions
// before the teacher sends them.
interface Draft {
  question_text: string;
  correct_answer: string;
  topic_tag: string;
  // 'source' is set when the draft is created and preserved through edits:
  // an edited AI suggestion stays 'ai_edited', a manual one stays 'teacher_manual'.
  source: "ai_suggested" | "ai_edited" | "teacher_manual";
  // Tracks the original text so we know whether the teacher actually edited
  // an AI suggestion (in which case source becomes 'ai_edited' on send).
  ai_original?: { question_text: string; correct_answer: string };
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function gradePillStyle(grade: QuizGrade | null): React.CSSProperties {
  const base: React.CSSProperties = {
    fontSize: 11,
    fontWeight: 600,
    padding: "2px 8px",
    borderRadius: 999,
    textTransform: "uppercase",
    letterSpacing: "0.06em",
    fontFamily: "var(--font-sans)",
  };
  if (!grade) {
    return { ...base, background: "var(--paper-shade)", color: "var(--ink-muted)" };
  }
  if (grade === "correct") return { ...base, background: "#dff5e1", color: "#1f7a3a" };
  if (grade === "partial") return { ...base, background: "#fff2c2", color: "#7a5a1f" };
  return { ...base, background: "#fbdada", color: "#a02222" };
}

// ── Main component ──────────────────────────────────────────────────────────

export default function QuizPanel({ sessionId }: { sessionId: number }) {
  const [quiz, setQuiz] = useState<Quiz | null>(null);
  const [starting, setStarting] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [sending, setSending] = useState(false);
  const [closing, setClosing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Answers grouped by question_id. We accumulate across the lifetime of the
  // quiz so the teacher can scroll back through earlier questions.
  const [answersByQ, setAnswersByQ] = useState<Record<number, AnswerRow[]>>({});

  // Pupil id → name, fetched once. Falls back to "Pupil #id" if missing.
  const [pupils, setPupils] = useState<Record<number, string>>({});

  // Countdown for the currently-sent question, derived from deadline_ms.
  const [remainingMs, setRemainingMs] = useState<number | null>(null);
  const deadlineRef = useRef<number | null>(null);

  const wsRef = useRef<WebSocket | null>(null);

  // The question currently accepting answers, if any.
  const liveQuestion = quiz?.questions.find((q) => q.status === "sent") ?? null;
  const closedQuestions = (quiz?.questions ?? []).filter((q) => q.status === "closed");

  // ── Load existing quiz on mount (handles page refresh mid-quiz) ───────────
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${API}/session/${sessionId}/quiz`);
        if (r.ok && !cancelled) {
          setQuiz(await r.json());
        }
      } catch {
        // 404 just means no quiz yet — leave state at null.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  // ── Load pupil names ─────────────────────────────────────────────────────
  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(`${API}/users`);
        if (!r.ok) return;
        const data = (await r.json()) as UserLite[];
        const map: Record<number, string> = {};
        for (const u of data) map[u.id] = u.name;
        setPupils(map);
      } catch {
        // names are nice-to-have; fall back to ids
      }
    })();
  }, []);

  // ── Teacher WS subscription ──────────────────────────────────────────────
  //
  // Open once we have a quiz; keep open until the component unmounts or the
  // session changes. Pupil-side events (quiz_question_opened/closed) go to
  // the pupils' channel — we only handle answers + grades here.
  useEffect(() => {
    if (!quiz) return;

    const ws = new WebSocket(`${WS_BASE}/session/ws/${sessionId}/teacher`);
    wsRef.current = ws;

    ws.onmessage = (evt) => {
      try {
        const payload = JSON.parse(evt.data) as {
          type?: string;
          attempt?: AnswerRow;
        };
        if (payload.type === "quiz_answer_received" && payload.attempt) {
          const a = payload.attempt;
          setAnswersByQ((prev) => {
            const existing = prev[a.question_id] ?? [];
            // De-dup in case of a reconnect replay.
            if (existing.some((row) => row.id === a.id)) return prev;
            return { ...prev, [a.question_id]: [...existing, a] };
          });
        } else if (payload.type === "quiz_attempt_graded" && payload.attempt) {
          const g = payload.attempt;
          setAnswersByQ((prev) => {
            const existing = prev[g.question_id] ?? [];
            return {
              ...prev,
              [g.question_id]: existing.map((row) =>
                row.id === g.id
                  ? { ...row, grade: g.grade, grader_rationale: g.grader_rationale }
                  : row,
              ),
            };
          });
        }
      } catch {
        // ignore non-JSON frames
      }
    };

    ws.onerror = () => setError("Live answer board disconnected — refresh to reconnect.");

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [quiz?.id, sessionId]);

  // ── Countdown tick ───────────────────────────────────────────────────────
  useEffect(() => {
    if (!liveQuestion || !liveQuestion.sent_at) {
      deadlineRef.current = null;
      setRemainingMs(null);
      return;
    }
    // Backend doesn't expose deadline_ms on the REST shape — derive it from
    // sent_at + time_limit_seconds, which the WS broadcast also did.
    const deadline = new Date(liveQuestion.sent_at).getTime() + liveQuestion.time_limit_seconds * 1000;
    deadlineRef.current = deadline;
    setRemainingMs(Math.max(0, deadline - Date.now()));
    const id = window.setInterval(() => {
      const remaining = Math.max(0, deadline - Date.now());
      setRemainingMs(remaining);
    }, 100);
    return () => window.clearInterval(id);
  }, [liveQuestion?.id, liveQuestion?.sent_at, liveQuestion?.time_limit_seconds]);

  // ── Quiz refresh helper ──────────────────────────────────────────────────
  // The backend mutates quiz state via several endpoints; after each we
  // re-fetch the whole quiz so the UI reflects the new status.
  const refreshQuiz = useCallback(async () => {
    const r = await fetch(`${API}/session/${sessionId}/quiz`);
    if (r.ok) setQuiz(await r.json());
  }, [sessionId]);

  // ── Actions ──────────────────────────────────────────────────────────────

  const startQuiz = async () => {
    setStarting(true);
    setError(null);
    try {
      const r = await fetch(`${API}/session/${sessionId}/quiz/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // Mode picker isn't in v1 — one-at-a-time is the only UX wired.
        body: JSON.stringify({ mode: "one_at_a_time" }),
      });
      if (!r.ok) throw new Error(await r.text());
      setQuiz(await r.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start quiz.");
    } finally {
      setStarting(false);
    }
  };

  const suggestQuestion = async () => {
    setSuggesting(true);
    setError(null);
    try {
      const r = await fetch(`${API}/session/${sessionId}/quiz/suggest`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      const s = (await r.json()) as Suggestion;
      setDraft({
        question_text: s.question_text,
        correct_answer: s.correct_answer,
        topic_tag: s.topic_tag ?? "",
        source: "ai_suggested",
        ai_original: {
          question_text: s.question_text,
          correct_answer: s.correct_answer,
        },
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Suggestion failed.");
    } finally {
      setSuggesting(false);
    }
  };

  const newManualDraft = () => {
    setDraft({
      question_text: "",
      correct_answer: "",
      topic_tag: "",
      source: "teacher_manual",
    });
  };

  const sendDraft = async () => {
    if (!draft) return;
    if (!draft.question_text.trim() || !draft.correct_answer.trim()) {
      setError("Question and correct answer are both required.");
      return;
    }
    setSending(true);
    setError(null);
    try {
      // Promote ai_suggested → ai_edited if the teacher changed either field
      // before sending. The backend stores this for later analytics.
      let source = draft.source;
      if (draft.source === "ai_suggested" && draft.ai_original) {
        if (
          draft.question_text.trim() !== draft.ai_original.question_text.trim()
          || draft.correct_answer.trim() !== draft.ai_original.correct_answer.trim()
        ) {
          source = "ai_edited";
        }
      }

      const create = await fetch(`${API}/session/${sessionId}/quiz/questions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question_text: draft.question_text.trim(),
          correct_answer: draft.correct_answer.trim(),
          topic_tag: draft.topic_tag.trim() || null,
          source,
          time_limit_seconds: 20,
        }),
      });
      if (!create.ok) throw new Error(await create.text());
      const question = (await create.json()) as QuizQuestion;

      const sendRes = await fetch(`${API}/quiz/questions/${question.id}/send`, {
        method: "POST",
      });
      if (!sendRes.ok) throw new Error(await sendRes.text());

      setDraft(null);
      await refreshQuiz();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not send question.");
    } finally {
      setSending(false);
    }
  };

  const closeNow = async () => {
    if (!liveQuestion) return;
    setClosing(true);
    setError(null);
    try {
      const r = await fetch(`${API}/quiz/questions/${liveQuestion.id}/close`, {
        method: "POST",
      });
      if (!r.ok) throw new Error(await r.text());
      await refreshQuiz();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not close question.");
    } finally {
      setClosing(false);
    }
  };

  // ── Render ───────────────────────────────────────────────────────────────

  // Not started yet — single button to open the quiz.
  if (!quiz) {
    return (
      <div className="ll-card" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <p className="ll-label">Quiz</p>
        <p className="ll-body" style={{ color: "var(--ink-muted)" }}>
          Quizzes are entirely up to you. Hit Start whenever you want to ask the class a question — you can suggest, edit, and send questions throughout the lesson.
        </p>
        <div>
          <button className="ll-chip" onClick={startQuiz} disabled={starting}>
            {starting ? "Starting…" : "Start Quiz"}
          </button>
        </div>
        {error && <p className="ll-body" style={{ color: "var(--error)" }}>{error}</p>}
      </div>
    );
  }

  return (
    <div className="ll-card" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <p className="ll-label">Quiz (one question at a time)</p>
        <p style={{ font: "500 12px/1 var(--font-sans)", color: "var(--ink-muted)" }}>
          {quiz.questions.length} question{quiz.questions.length === 1 ? "" : "s"} asked so far
        </p>
      </div>

      {/* Live question — pupils are answering right now */}
      {liveQuestion && (
        <div
          style={{
            background: "var(--paper-shade)",
            borderRadius: 14,
            padding: "16px 18px",
            display: "flex",
            flexDirection: "column",
            gap: 12,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
            <p className="ll-subheading">{liveQuestion.question_text}</p>
            <div
              style={{
                fontVariantNumeric: "tabular-nums",
                fontWeight: 700,
                fontSize: 22,
                color: remainingMs != null && remainingMs <= 5000 ? "var(--error)" : "var(--action)",
                minWidth: 64,
                textAlign: "right",
              }}
            >
              {remainingMs != null ? `${Math.ceil(remainingMs / 1000)}s` : "—"}
            </div>
          </div>
          <p style={{ font: "500 12px/1.4 var(--font-sans)", color: "var(--ink-muted)" }}>
            Correct answer (visible to you only): <em>{liveQuestion.correct_answer}</em>
          </p>

          <AnswerBoard
            answers={answersByQ[liveQuestion.id] ?? []}
            pupils={pupils}
          />

          <div>
            <button className="ll-chip" onClick={closeNow} disabled={closing}>
              {closing ? "Closing…" : "Close question now"}
            </button>
          </div>
        </div>
      )}

      {/* Draft — teacher reviewing a suggestion or composing manually */}
      {!liveQuestion && draft && (
        <div
          style={{
            background: "var(--paper-shade)",
            borderRadius: 14,
            padding: "16px 18px",
            display: "flex",
            flexDirection: "column",
            gap: 10,
          }}
        >
          <p className="ll-label">
            {draft.source === "teacher_manual" ? "Manual question" : "Suggested question"}
          </p>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span style={{ font: "500 12px/1 var(--font-sans)", color: "var(--ink-muted)" }}>Question</span>
            <textarea
              value={draft.question_text}
              onChange={(e) => setDraft({ ...draft, question_text: e.target.value })}
              rows={2}
              style={{
                border: "1px solid var(--ink-soft)",
                borderRadius: "var(--radius-md)",
                padding: "10px 12px",
                fontFamily: "var(--font-sans)",
                fontSize: 14,
                background: "var(--paper)",
                resize: "vertical",
              }}
            />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span style={{ font: "500 12px/1 var(--font-sans)", color: "var(--ink-muted)" }}>Correct answer</span>
            <textarea
              value={draft.correct_answer}
              onChange={(e) => setDraft({ ...draft, correct_answer: e.target.value })}
              rows={2}
              style={{
                border: "1px solid var(--ink-soft)",
                borderRadius: "var(--radius-md)",
                padding: "10px 12px",
                fontFamily: "var(--font-sans)",
                fontSize: 14,
                background: "var(--paper)",
                resize: "vertical",
              }}
            />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span style={{ font: "500 12px/1 var(--font-sans)", color: "var(--ink-muted)" }}>Topic tag (optional)</span>
            <input
              type="text"
              value={draft.topic_tag}
              onChange={(e) => setDraft({ ...draft, topic_tag: e.target.value })}
              placeholder="e.g. photosynthesis"
              style={{
                border: "1px solid var(--ink-soft)",
                borderRadius: "var(--radius-md)",
                padding: "10px 12px",
                fontFamily: "var(--font-sans)",
                fontSize: 14,
                background: "var(--paper)",
              }}
            />
          </label>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="ll-chip" onClick={sendDraft} disabled={sending}>
              {sending ? "Sending…" : "Send to class (20s)"}
            </button>
            <button
              className="ll-chip"
              onClick={() => setDraft(null)}
              disabled={sending}
              style={{ background: "var(--paper)", color: "var(--ink)" }}
            >
              Discard
            </button>
          </div>
        </div>
      )}

      {/* Idle — no live question, no draft; show the action chips */}
      {!liveQuestion && !draft && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="ll-chip" onClick={suggestQuestion} disabled={suggesting}>
            {suggesting ? "Thinking…" : "Suggest question"}
          </button>
          <button
            className="ll-chip"
            onClick={newManualDraft}
            style={{ background: "var(--paper)", color: "var(--ink)", border: "1px solid var(--ink-soft)" }}
          >
            Write my own
          </button>
        </div>
      )}

      {/* History — every closed question with grade summary */}
      {closedQuestions.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <p className="ll-label">Earlier questions</p>
          {closedQuestions.map((q) => (
            <ClosedQuestionSummary
              key={q.id}
              question={q}
              answers={answersByQ[q.id] ?? []}
              pupils={pupils}
            />
          ))}
        </div>
      )}

      {error && <p className="ll-body" style={{ color: "var(--error)" }}>{error}</p>}
    </div>
  );
}

// ── Sub-components ──────────────────────────────────────────────────────────

function AnswerBoard({
  answers,
  pupils,
}: {
  answers: AnswerRow[];
  pupils: Record<number, string>;
}) {
  if (answers.length === 0) {
    return (
      <p className="ll-body" style={{ color: "var(--ink-muted)", fontStyle: "italic" }}>
        Waiting for the first answer…
      </p>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {answers.map((row) => (
        <div
          key={row.id}
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 10,
            padding: "8px 10px",
            background: "var(--paper)",
            borderRadius: 10,
          }}
        >
          <span
            style={{
              minWidth: 110,
              fontWeight: 600,
              fontSize: 13,
              color: "var(--ink)",
            }}
          >
            {pupils[row.pupil_id] ?? `Pupil #${row.pupil_id}`}
          </span>
          <span style={{ flex: 1, fontSize: 14, color: "var(--ink)" }}>{row.pupil_answer}</span>
          <span style={gradePillStyle(row.grade)}>
            {row.grade ?? "pending"}
          </span>
        </div>
      ))}
    </div>
  );
}

function ClosedQuestionSummary({
  question,
  answers,
  pupils,
}: {
  question: QuizQuestion;
  answers: AnswerRow[];
  pupils: Record<number, string>;
}) {
  const [expanded, setExpanded] = useState(false);
  const counts = {
    correct: answers.filter((a) => a.grade === "correct").length,
    partial: answers.filter((a) => a.grade === "partial").length,
    incorrect: answers.filter((a) => a.grade === "incorrect").length,
    pending: answers.filter((a) => !a.grade).length,
  };

  return (
    <div
      style={{
        background: "var(--paper-shade)",
        borderRadius: 12,
        padding: "10px 14px",
      }}
    >
      <button
        onClick={() => setExpanded((v) => !v)}
        style={{
          background: "none",
          border: "none",
          padding: 0,
          width: "100%",
          textAlign: "left",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          fontFamily: "var(--font-sans)",
        }}
      >
        <span style={{ flex: 1, color: "var(--ink)", fontSize: 14 }}>{question.question_text}</span>
        <span style={{ display: "flex", gap: 6 }}>
          {counts.correct > 0 && <span style={gradePillStyle("correct")}>{counts.correct}</span>}
          {counts.partial > 0 && <span style={gradePillStyle("partial")}>{counts.partial}</span>}
          {counts.incorrect > 0 && <span style={gradePillStyle("incorrect")}>{counts.incorrect}</span>}
          {counts.pending > 0 && <span style={gradePillStyle(null)}>{counts.pending}</span>}
        </span>
      </button>
      {expanded && <div style={{ marginTop: 10 }}><AnswerBoard answers={answers} pupils={pupils} /></div>}
    </div>
  );
}
