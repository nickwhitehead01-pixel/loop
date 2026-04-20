"use client";
import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import LessonUpload from "@/components/LessonUpload";
import LessonDetail from "@/components/LessonDetail";
import StudentProgress from "@/components/StudentProgress";
import TeacherChat from "@/components/TeacherChat";
import ConfirmDialog from "@/components/ConfirmDialog";

const TEACHER_ID = 1;
const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Tab = "lessons" | "students" | "sessions" | "chat";

// ── Types ─────────────────────────────────────────────────────────────────────
interface Lesson {
  id: number;
  title: string;
  created_at: string;
  summary: string | null;
  file_count: number;
}

interface SessionAnalytics {
  session_id: number;
  total_pupils: number;
  avg_understanding_score: number | null;
  total_questions_asked: number;
  quiz_completion_rate: number | null;
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
              {l.summary && (
                <p className="ll-body" style={{ marginTop: 8, color: "var(--ink-muted)", fontSize: 14 }}>
                  {l.summary}
                </p>
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

function SessionsPanel() {
  const [sessions, setSessions] = useState<SessionAnalytics[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${API}/teacher/sessions`);
        const list = await res.json() as Array<{ session_id: number }>;
        const details = await Promise.all(
          list.map((s) =>
            fetch(`${API}/teacher/sessions/${s.session_id}/analytics`).then((r) => r.json())
          )
        );
        setSessions(details);
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) return <p className="ll-body" style={{ color: "var(--ink-muted)" }}>Loading sessions…</p>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <h2 className="ll-heading">Sessions</h2>
      {sessions.length === 0 ? (
        <p className="ll-body" style={{ color: "var(--ink-muted)" }}>No sessions yet.</p>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
            gap: 16,
          }}
        >
          {sessions.map((s) => (
            <div key={s.session_id} className="ll-card">
              <p className="ll-label" style={{ marginBottom: 12 }}>Session {s.session_id}</p>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <Row label="Pupils" value={String(s.total_pupils)} />
                <Row label="Avg understanding" value={pct(s.avg_understanding_score)} />
                <Row label="Questions asked" value={String(s.total_questions_asked)} />
                <Row label="Quiz completion" value={pct(s.quiz_completion_rate)} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
      <span style={{ fontSize: 14, color: "var(--ink-muted)" }}>{label}</span>
      <span className="ll-subheading" style={{ fontSize: 15 }}>{value}</span>
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
        {tab === "sessions" && <SessionsPanel />}
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
