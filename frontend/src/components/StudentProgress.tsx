"use client";
import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface Student {
  pupil_id: number;
  pupil_name: string;
  message_count: number;
  last_active: string | null;
}

interface SessionSummary {
  session_id: number;
  summary_text: string;
  understanding_score: number | null;
  questions_asked: number;
  created_at: string;
}

interface StudentDetail {
  pupil_id: number;
  pupil_name: string;
  total_messages: number;
  session_summaries: SessionSummary[];
  quiz_performance: {
    total_attempts: number;
    correct_answers: number;
    accuracy: number | null;
  };
}

function formatDate(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function ScorePill({ score }: { score: number | null }) {
  if (score == null) return <span className="ll-pill">No data</span>;
  const pct = Math.round(score * 100);
  const cls = pct >= 70 ? "ll-pill--success" : pct >= 40 ? "ll-pill--warn" : "ll-pill--error";
  return <span className={`ll-pill ${cls}`}>{pct}%</span>;
}

export default function StudentProgress() {
  const [students, setStudents] = useState<Student[]>([]);
  const [selected, setSelected] = useState<StudentDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API}/teacher/students`)
      .then((r) => r.json())
      .then(setStudents)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  const openStudent = async (id: number) => {
    const res = await fetch(`${API}/teacher/students/${id}/progress`);
    const data = await res.json();
    setSelected(data);
  };

  if (loading) return <p className="ll-body" style={{ color: "var(--ink-muted)" }}>Loading pupils…</p>;

  if (selected) {
    const acc = selected.quiz_performance.accuracy;
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <button className="ll-chip ll-chip--ghost" onClick={() => setSelected(null)}>Back</button>
          <h2 className="ll-heading">{selected.pupil_name}</h2>
        </div>

        {/* Stats row */}
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <div className="ll-card" style={{ flex: 1, minWidth: 140 }}>
            <p className="ll-label">Messages</p>
            <p className="ll-heading" style={{ marginTop: 8 }}>{selected.total_messages}</p>
          </div>
          <div className="ll-card" style={{ flex: 1, minWidth: 140 }}>
            <p className="ll-label">Quiz accuracy</p>
            <p className="ll-heading" style={{ marginTop: 8 }}>
              {acc != null ? `${Math.round(acc * 100)}%` : "—"}
            </p>
          </div>
          <div className="ll-card" style={{ flex: 1, minWidth: 140 }}>
            <p className="ll-label">Quiz attempts</p>
            <p className="ll-heading" style={{ marginTop: 8 }}>{selected.quiz_performance.total_attempts}</p>
          </div>
        </div>

        {/* Session summaries */}
        <div>
          <p className="ll-label" style={{ marginBottom: 12 }}>Session history</p>
          {selected.session_summaries.length === 0 ? (
            <p className="ll-body" style={{ color: "var(--ink-muted)" }}>No sessions yet.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {selected.session_summaries.map((s) => (
                <div key={s.session_id} className="ll-card">
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 8 }}>
                    <p className="ll-subheading">Session {s.session_id}</p>
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <ScorePill score={s.understanding_score} />
                      <span className="ll-pill">{s.questions_asked} questions</span>
                    </div>
                  </div>
                  <p className="ll-body" style={{ color: "var(--ink-muted)", fontSize: 14 }}>{s.summary_text}</p>
                  <p style={{ fontSize: 12, color: "var(--ink-muted)", marginTop: 8 }}>{formatDate(s.created_at)}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <p className="ll-label">Pupils ({students.length})</p>
      {students.length === 0 ? (
        <p className="ll-body" style={{ color: "var(--ink-muted)" }}>No pupils yet.</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {students.map((s) => (
            <button
              key={s.pupil_id}
              onClick={() => openStudent(s.pupil_id)}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "16px 20px",
                background: "var(--paper)",
                border: "1px solid var(--ink-soft)",
                borderRadius: "var(--radius-lg)",
                cursor: "pointer",
                textAlign: "left",
                fontFamily: "var(--font-sans)",
                transition: "background var(--dur-quick) var(--ease)",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--paper-shade)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "var(--paper)")}
            >
              <span className="ll-subheading">{s.pupil_name}</span>
              <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <span className="ll-pill">{s.message_count} messages</span>
                <span style={{ fontSize: 13, color: "var(--ink-muted)" }}>{formatDate(s.last_active)}</span>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
