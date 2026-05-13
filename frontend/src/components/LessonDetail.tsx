"use client";
import { useCallback, useEffect, useState } from "react";
import LessonProcessingStatus, { deriveLessonStage } from "./LessonProcessingStatus";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// Must match PRECOMPUTE_MAX_ATTEMPTS in lesson_summary_worker.py — see the
// note in LessonProcessingStatus.tsx.
const PRECOMPUTE_MAX_ATTEMPTS = 5;

interface LessonFile {
  id: number;
  original_filename: string;
  created_at: string;
  url: string;
}

interface Chunk {
  id: number;
  content: string;
}

interface GlossaryEntry {
  term: string;
  explanation: string;
}

interface PromptCardPreview {
  id?: string;
  question: string;
  color: string;
  triggers?: string[];
}

interface LessonDetailData {
  id: number;
  title: string;
  created_at: string;
  summary: string | null;
  files: LessonFile[];
  chunks: Chunk[];
  chunk_count: number;
  // Pre-computed live-lesson features. Each is null/empty until the
  // background worker has run; the LessonProcessingStatus component
  // reads precomputed_features_at / precomputed_features_attempts to
  // pick the right stage label.
  glossary: GlossaryEntry[];
  prompt_cards: PromptCardPreview[];
  glossary_count: number;
  prompt_card_count: number;
  precomputed_features_at: string | null;
  precomputed_features_attempts: number;
}

interface Props {
  lessonId: number;
  onBack: () => void;
}

function formatDate(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

/** Render a markdown-ish summary: bold **text** and bullet lines */
function SummaryBlock({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {lines.map((line, i) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={i} style={{ height: 6 }} />;

        // Heading: starts with ## or **Something** on its own line
        const headingMatch = trimmed.match(/^#{1,3}\s+(.+)$/) ?? trimmed.match(/^\*\*(.+)\*\*$/);
        if (headingMatch) {
          return (
            <p key={i} style={{ fontWeight: 600, fontSize: 14, color: "var(--ink)", marginTop: 8 }}>
              {headingMatch[1]}
            </p>
          );
        }

        // Bullet
        if (trimmed.startsWith("- ") || trimmed.startsWith("• ")) {
          const content = trimmed.slice(2);
          // inline bold
          const parts = content.split(/\*\*(.+?)\*\*/g);
          return (
            <div key={i} style={{ display: "flex", gap: 8, paddingLeft: 8 }}>
              <span style={{ color: "var(--action)", flexShrink: 0, fontSize: 14 }}>·</span>
              <p style={{ fontSize: 14, color: "var(--ink)", lineHeight: 1.55 }}>
                {parts.map((p, j) => j % 2 === 1 ? <strong key={j}>{p}</strong> : p)}
              </p>
            </div>
          );
        }

        // Plain line with possible inline bold
        const parts = trimmed.split(/\*\*(.+?)\*\*/g);
        return (
          <p key={i} style={{ fontSize: 14, color: "var(--ink)", lineHeight: 1.55 }}>
            {parts.map((p, j) => j % 2 === 1 ? <strong key={j}>{p}</strong> : p)}
          </p>
        );
      })}
    </div>
  );
}

/** Fetches a plain-text file and renders it inline. */
function TxtPreview({ url }: { url: string }) {
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    fetch(url)
      .then((r) => r.text())
      .then(setText)
      .catch(() => setErr(true));
  }, [url]);

  if (err) return <p style={{ padding: 16, fontSize: 13, color: "var(--error)" }}>Could not load file.</p>;
  if (text === null) return <p style={{ padding: 16, fontSize: 13, color: "var(--ink-muted)" }}>Loading…</p>;

  return (
    <pre
      style={{
        margin: 0, padding: "16px", fontSize: 13,
        color: "var(--ink)", fontFamily: "monospace",
        whiteSpace: "pre-wrap", wordBreak: "break-word",
        maxHeight: 480, overflowY: "auto",
        background: "var(--paper-shade)",
      }}
    >
      {text}
    </pre>
  );
}

const ACCEPTED = ".pdf,.docx,.pptx,.txt";
const MAX_BYTES = 25 * 1024 * 1024;

interface AddFilesFormProps {
  lessonId: number;
  onAdded: () => void;
  onCancel: () => void;
}

function AddFilesForm({ lessonId, onAdded, onCancel }: AddFilesFormProps) {
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const addFiles = (incoming: FileList | null) => {
    if (!incoming) return;
    const valid: File[] = [];
    for (const f of Array.from(incoming)) {
      if (f.size > MAX_BYTES) { setError(`${f.name} exceeds 25 MB`); continue; }
      if (!files.find((x) => x.name === f.name)) valid.push(f);
    }
    setFiles((prev) => [...prev, ...valid]);
  };

  const submit = async () => {
    if (!files.length) return;
    setUploading(true);
    setError(null);
    try {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      const res = await fetch(`${API}/teacher/lessons/${lessonId}/files`, { method: "POST", body: fd });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error((data as { detail?: string }).detail ?? `HTTP ${res.status}`);
      }
      const data = await res.json() as { added: LessonFile[] };
      void data; // consumed by parent reload
      onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
      setUploading(false);
    }
  };

  return (
    <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 10 }}>
      {/* Drop zone */}
      <label
        style={{
          display: "block", border: "2px dashed var(--ink-soft)",
          borderRadius: "var(--radius-sm)", padding: "16px 20px",
          cursor: "pointer", textAlign: "center", background: "var(--paper-shade)",
        }}
      >
        <p style={{ fontSize: 13, color: "var(--ink-muted)" }}>Click to choose files, or drag and drop</p>
        <p style={{ fontSize: 12, color: "var(--ink-muted)", marginTop: 2 }}>PDF, DOCX, PPTX, TXT — up to 25 MB each</p>
        <input
          type="file"
          multiple
          accept={ACCEPTED}
          style={{ display: "none" }}
          onChange={(e) => addFiles(e.target.files)}
        />
      </label>

      {/* Selected files */}
      {files.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {files.map((f) => (
            <div key={f.name} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 12px", background: "var(--paper-shade)", borderRadius: "var(--radius-sm)", border: "1px solid var(--ink-soft)" }}>
              <p style={{ fontSize: 13, color: "var(--ink)" }}>{f.name}</p>
              <button
                onClick={() => setFiles((prev) => prev.filter((x) => x.name !== f.name))}
                style={{ background: "none", border: "none", cursor: "pointer", color: "var(--error)", fontFamily: "var(--font-sans)", fontSize: 13, padding: "2px 6px" }}
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      )}

      {error && <p style={{ fontSize: 13, color: "var(--error)" }}>{error}</p>}

      <div style={{ display: "flex", gap: 10 }}>
        <button
          className="ll-chip"
          onClick={submit}
          disabled={files.length === 0 || uploading}
        >
          {uploading ? "Uploading…" : `Add ${files.length > 0 ? files.length : ""} file${files.length !== 1 ? "s" : ""}`}
        </button>
        <button className="ll-chip ll-chip--ghost" onClick={onCancel} disabled={uploading}>
          Cancel
        </button>
      </div>
    </div>
  );
}

export default function LessonDetail({ lessonId, onBack }: Props) {
  const [data, setData] = useState<LessonDetailData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedChunks, setExpandedChunks] = useState(false);
  const [previewFileId, setPreviewFileId] = useState<number | null>(null);
  const [copiedId, setCopiedId] = useState<number | null>(null);
  const [showAddFiles, setShowAddFiles] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetch(`${API}/teacher/lessons/${lessonId}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d: LessonDetailData) => setData(d))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [lessonId]);

  useEffect(() => { load(); }, [load]);

  // Re-fetch every 8s while any worker stage is still pending. We stop
  // polling once everything's done (or precompute has given up), so the
  // detail page goes quiet when nothing more is going to change.
  useEffect(() => {
    if (!data) return;
    const pending =
      data.summary == null ||
      (!data.precomputed_features_at &&
        data.precomputed_features_attempts < PRECOMPUTE_MAX_ATTEMPTS);
    if (!pending) return;
    const id = window.setInterval(load, 8000);
    return () => window.clearInterval(id);
  }, [data, load]);

  const copyLink = (f: LessonFile) => {
    const url = `${API}${f.url}`;
    navigator.clipboard.writeText(url).then(() => {
      setCopiedId(f.id);
      setTimeout(() => setCopiedId(null), 2000);
    }).catch(() => {});
  };

  const fileUrl = (f: LessonFile) => `${API}${f.url}`;
  const isPdf = (filename: string) => filename.toLowerCase().endsWith(".pdf");
  const isTxt = (filename: string) => filename.toLowerCase().endsWith(".txt");
  const isPreviewable = (filename: string) => isPdf(filename) || isTxt(filename);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Back nav */}
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <button
          onClick={onBack}
          style={{
            background: "none", border: "none", cursor: "pointer",
            color: "var(--action)", fontFamily: "var(--font-sans)",
            fontSize: 14, padding: 0, display: "flex", alignItems: "center", gap: 6,
          }}
        >
          ← Lessons
        </button>
        {data && (
          <span style={{ color: "var(--ink-muted)", fontSize: 14 }}>
            / {data.title}
          </span>
        )}
      </div>

      {loading && (
        <p className="ll-body" style={{ color: "var(--ink-muted)" }}>
          Loading lesson — the AI is summarising the content…
        </p>
      )}

      {error && (
        <p style={{ color: "var(--error)", fontSize: 14 }}>Failed to load lesson: {error}</p>
      )}

      {data && (
        <>
          {/* Header */}
          <div>
            <h2 className="ll-heading">{data.title}</h2>
            <p style={{ fontSize: 13, color: "var(--ink-muted)", marginTop: 4 }}>
              Uploaded {formatDate(data.created_at)}
              {" · "}
              {data.files.length} {data.files.length === 1 ? "file" : "files"}
              {" · "}
              {data.chunk_count} indexed {data.chunk_count === 1 ? "passage" : "passages"}
            </p>
          </div>

          {/* Uploaded files */}
          <div className="ll-card">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <p className="ll-label">Uploaded files</p>
              {!showAddFiles && (
                <button
                  onClick={() => setShowAddFiles(true)}
                  style={{ background: "none", border: "none", cursor: "pointer", color: "var(--action)", fontFamily: "var(--font-sans)", fontSize: 13, padding: 0 }}
                >
                  + Add files
                </button>
              )}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {data.files.length === 0 ? (
                <p style={{ fontSize: 14, color: "var(--ink-muted)" }}>No file records found.</p>
              ) : (
                data.files.map((f) => (
                  <div key={f.id}>
                    {/* File row */}
                    <div
                      style={{
                        display: "flex", justifyContent: "space-between", alignItems: "center",
                        padding: "8px 12px",
                        background: "var(--paper-shade)",
                        borderRadius: previewFileId === f.id ? "var(--radius-sm) var(--radius-sm) 0 0" : "var(--radius-sm)",
                        border: "1px solid var(--ink-soft)",
                        borderBottom: previewFileId === f.id ? "none" : "1px solid var(--ink-soft)",
                      }}
                    >
                      <p style={{ fontSize: 14, color: "var(--ink)", fontFamily: "var(--font-sans)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {f.original_filename}
                      </p>
                      <div style={{ display: "flex", gap: 8, alignItems: "center", marginLeft: 12, flexShrink: 0 }}>
                        <p style={{ fontSize: 12, color: "var(--ink-muted)" }}>{formatDate(f.created_at)}</p>
                        {isPreviewable(f.original_filename) && (
                          <button
                            onClick={() => setPreviewFileId(previewFileId === f.id ? null : f.id)}
                            style={{
                              background: "none", border: "none", cursor: "pointer",
                              color: "var(--action)", fontFamily: "var(--font-sans)", fontSize: 13, padding: "2px 6px",
                            }}
                          >
                            {previewFileId === f.id ? "Hide" : "Preview"}
                          </button>
                        )}
                        <a
                          href={fileUrl(f)}
                          target="_blank"
                          rel="noopener noreferrer"
                          style={{
                            color: "var(--action)", fontFamily: "var(--font-sans)", fontSize: 13,
                            textDecoration: "none", padding: "2px 6px",
                          }}
                        >
                          Open
                        </a>
                        <button
                          onClick={() => copyLink(f)}
                          style={{
                            background: "none", border: "none", cursor: "pointer",
                            color: copiedId === f.id ? "var(--success, #2d7a4f)" : "var(--ink-muted)",
                            fontFamily: "var(--font-sans)", fontSize: 13, padding: "2px 6px",
                          }}
                        >
                          {copiedId === f.id ? "Copied!" : "Copy link"}
                        </button>
                      </div>
                    </div>

                    {/* Inline preview panel */}
                    {previewFileId === f.id && (
                      <div
                        style={{
                          border: "1px solid var(--ink-soft)",
                          borderTop: "none",
                          borderRadius: "0 0 var(--radius-sm) var(--radius-sm)",
                          overflow: "hidden",
                          background: "#fff",
                        }}
                      >
                        {isPdf(f.original_filename) ? (
                          <iframe
                            src={fileUrl(f)}
                            title={f.original_filename}
                            style={{ width: "100%", height: 640, display: "block", border: "none" }}
                          />
                        ) : (
                          /* .txt — fetch and show as preformatted text */
                          <TxtPreview url={fileUrl(f)} />
                        )}
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>

            {showAddFiles && (
              <AddFilesForm
                lessonId={lessonId}
                onAdded={() => {
                  setShowAddFiles(false);
                  load();
                }}
                onCancel={() => setShowAddFiles(false)}
              />
            )}
          </div>
          {/* Processing status banner. Hidden once everything is ready
              so the page doesn't carry a permanent "Ready" badge. */}
          {deriveLessonStage(data) !== "ready" && (
            <div
              className="ll-card"
              style={{
                background: "var(--paper-shade)",
                display: "flex",
                alignItems: "center",
                gap: 12,
              }}
            >
              <LessonProcessingStatus lesson={data} textColor="var(--ink)" />
            </div>
          )}

          <div className="ll-card">
            <p className="ll-label" style={{ marginBottom: 10 }}>AI summary</p>
            {data.summary ? (
              <SummaryBlock text={data.summary} />
            ) : (
              <p style={{ fontSize: 14, color: "var(--ink-muted)" }}>
                Summary is being generated — this should appear in a couple of minutes.
              </p>
            )}
          </div>

          {/* Live-lesson features. Shows progress while the worker is
              still building them, or previews once they're ready. */}
          <LiveFeaturesSection data={data} onChange={setData} />

          {/* Indexed passages */}
          <div className="ll-card">
            <div
              style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}
            >
              <p className="ll-label">
                Indexed passages
                {data.chunk_count > 0 && (
                  <span style={{ fontWeight: 400, color: "var(--ink-muted)", marginLeft: 6 }}>
                    ({data.chunk_count})
                  </span>
                )}
              </p>
              {data.chunks.length > 0 && (
                <button
                  onClick={() => setExpandedChunks((v) => !v)}
                  style={{
                    background: "none", border: "none", cursor: "pointer",
                    color: "var(--action)", fontFamily: "var(--font-sans)",
                    fontSize: 13, padding: 0,
                  }}
                >
                  {expandedChunks ? "Collapse" : "Show passages"}
                </button>
              )}
            </div>

            {data.chunks.length === 0 ? (
              <p style={{ fontSize: 14, color: "var(--ink-muted)" }}>
                No passages have been indexed yet. This usually means the file could not be parsed.
              </p>
            ) : !expandedChunks ? (
              <p style={{ fontSize: 14, color: "var(--ink-muted)" }}>
                The AI has broken this lesson into {data.chunk_count} passages and stored vector
                embeddings for each. Click "Show passages" to inspect the extracted text.
              </p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {data.chunks.map((c, idx) => (
                  <div
                    key={c.id}
                    style={{
                      padding: "10px 14px",
                      background: "var(--paper-shade)",
                      borderRadius: "var(--radius-sm)",
                      border: "1px solid var(--ink-soft)",
                    }}
                  >
                    <p style={{ fontSize: 11, color: "var(--ink-muted)", marginBottom: 4, fontFamily: "var(--font-sans)" }}>
                      Passage {idx + 1}
                    </p>
                    <p style={{ fontSize: 13, color: "var(--ink)", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
                      {c.content}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Renders the pre-computed glossary and prompt-card library produced by the
 * background worker. Each sub-section shows one of:
 *   - a spinner ("Preparing…") while the worker is still running
 *   - the actual entries once they're ready
 *   - a warning if the worker has given up
 *
 * Kept inside this file because it shares the LessonDetailData shape with
 * the main component — splitting it would just mean re-declaring the
 * GlossaryEntry / PromptCardPreview types.
 */
function LiveFeaturesSection({
  data,
  onChange,
}: {
  data: LessonDetailData;
  /** Called with an updated LessonDetailData to optimistically refresh the
   *  parent's state without waiting for the next poll. */
  onChange: (next: LessonDetailData) => void;
}) {
  const [regenerating, setRegenerating] = useState(false);
  const stage = deriveLessonStage(data);
  // Don't render anything while we're still earlier than the precompute
  // stage — the user gets the processing banner instead. Once we reach
  // preparing/ready/failed we have something useful to say here.
  if (stage === "reading" || stage === "summarising") return null;

  const regenerate = async () => {
    setRegenerating(true);
    // Optimistic local update — flip into "preparing" stage IMMEDIATELY so
    // the user sees the spinner the instant they click, rather than waiting
    // up to 8s for the next poll tick. The polling effect in the parent
    // page only starts ticking once the data is in a pending state, so
    // without this the page would look frozen until manual refresh.
    onChange({
      ...data,
      glossary: [],
      prompt_cards: [],
      glossary_count: 0,
      prompt_card_count: 0,
      precomputed_features_at: null,
      precomputed_features_attempts: 0,
    });
    try {
      await fetch(`${API}/teacher/lessons/${data.id}/regenerate-features`, {
        method: "POST",
      });
    } finally {
      setRegenerating(false);
    }
  };

  // Allow regenerate whenever there's something to regenerate FROM (i.e.
  // we're not mid-precompute already). The teacher usually hits this when
  // the auto-generated list missed words they care about, or after the
  // 5-attempt failure state.
  const canRegenerate = stage === "ready" || stage === "features_failed";

  return (
    <div className="ll-card">
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, marginBottom: 4 }}>
        <p className="ll-label">Live-lesson features</p>
        {canRegenerate && (
          <button
            onClick={regenerate}
            disabled={regenerating}
            style={{
              background: "none",
              border: "1px solid var(--ink-soft)",
              borderRadius: "var(--radius-sm)",
              padding: "4px 10px",
              fontSize: 12,
              fontFamily: "var(--font-sans)",
              color: regenerating ? "var(--ink-muted)" : "var(--ink)",
              cursor: regenerating ? "wait" : "pointer",
            }}
            title="Re-run the AI analysis to refresh the glossary and prompt cards. Takes a few minutes."
          >
            {regenerating ? "Queued…" : "Regenerate"}
          </button>
        )}
      </div>
      <p style={{ fontSize: 13, color: "var(--ink-muted)", marginBottom: 14 }}>
        Pupils see these during the lesson: tappable words for tricky vocabulary,
        and prompt cards suggesting questions they could ask.
      </p>

      <FeatureSubsection
        title="Tappable words"
        emptyHint={data.glossary_count === 0
          ? "Gemma is picking the trickiest terms from your material — usually 15-25 words."
          : null}
        stage={stage}
      >
        {data.glossary.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {data.glossary.map((g, i) => (
              <span
                key={i}
                title={g.explanation}
                style={{
                  background: "var(--paper-shade)",
                  color: "var(--ink)",
                  fontSize: 12,
                  fontFamily: "var(--font-sans)",
                  padding: "4px 10px",
                  borderRadius: 999,
                  cursor: "help",
                  border: "1px solid var(--ink-soft)",
                }}
              >
                {g.term}
              </span>
            ))}
          </div>
        )}
      </FeatureSubsection>

      <div style={{ height: 16 }} />

      <FeatureSubsection
        title="Prompt cards"
        emptyHint={data.prompt_card_count === 0
          ? "Gemma is drafting questions a pupil might naturally ask while you teach this."
          : null}
        stage={stage}
      >
        {data.prompt_cards.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {data.prompt_cards.map((c, i) => (
              <div
                key={c.id ?? i}
                style={{
                  padding: "10px 14px",
                  background: "var(--paper-shade)",
                  borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--ink-soft)",
                  borderLeft: `3px solid ${cardColor(c.color)}`,
                }}
              >
                <p style={{ fontSize: 14, color: "var(--ink)", marginBottom: 4 }}>
                  {c.question}
                </p>
                {c.triggers && c.triggers.length > 0 && (
                  <p style={{ fontSize: 11, color: "var(--ink-muted)", fontFamily: "var(--font-sans)" }}>
                    surfaces on: {c.triggers.join(", ")}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </FeatureSubsection>
    </div>
  );
}

function FeatureSubsection({
  title,
  emptyHint,
  stage,
  children,
}: {
  title: string;
  emptyHint: string | null;
  stage: ReturnType<typeof deriveLessonStage>;
  children: React.ReactNode;
}) {
  const isPreparing = stage === "preparing_features";
  const isFailed = stage === "features_failed";
  return (
    <div>
      <p
        className="ll-label"
        style={{ marginBottom: 8, color: "var(--ink)" }}
      >
        {title}
      </p>
      {/* Show the children (the actual content) if we have any, otherwise
          show a spinner / hint / failure message. */}
      {children ? <div>{children}</div> : null}
      {!children && isPreparing && emptyHint && (
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <svg
            width="13"
            height="13"
            viewBox="0 0 13 13"
            style={{ flexShrink: 0, animation: "ll-spin 1.1s linear infinite" }}
            aria-hidden="true"
          >
            <circle cx="6.5" cy="6.5" r="5" fill="none" stroke="var(--ink-soft)" strokeWidth="2" />
            <path d="M6.5 1.5 A5 5 0 0 1 11.5 6.5" fill="none" stroke="var(--action)" strokeWidth="2" strokeLinecap="round" />
          </svg>
          <p style={{ fontSize: 13, color: "var(--ink-muted)" }}>{emptyHint}</p>
        </div>
      )}
      {!children && isFailed && (
        <p style={{ fontSize: 13, color: "var(--warn)" }}>
          Couldn't generate {title.toLowerCase()} after five attempts. The lesson
          will still work — pupils just won't see this feature.
        </p>
      )}
    </div>
  );
}

function cardColor(color: string): string {
  // Map the backend's named palette ("blue" / "green" / "amber") to the
  // existing design-system tokens. Falls back to action blue for unknown
  // values so a new colour wouldn't render as invisible.
  switch (color) {
    case "green":
      return "var(--success)";
    case "amber":
      return "var(--warn)";
    case "blue":
    default:
      return "var(--action)";
  }
}
