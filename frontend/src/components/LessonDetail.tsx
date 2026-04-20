"use client";
import { useCallback, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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

interface LessonDetailData {
  id: number;
  title: string;
  created_at: string;
  summary: string | null;
  files: LessonFile[];
  chunks: Chunk[];
  chunk_count: number;
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
          <div className="ll-card">
            <p className="ll-label" style={{ marginBottom: 10 }}>AI summary</p>
            {data.summary ? (
              <SummaryBlock text={data.summary} />
            ) : (
              <p style={{ fontSize: 14, color: "var(--ink-muted)" }}>
                No summary available — the AI may not have been able to read the content yet.
              </p>
            )}
          </div>

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
