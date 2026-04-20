"use client";
import { useRef, useState } from "react";

const ACCEPTED = [".pdf", ".docx", ".pptx", ".txt"];
const ACCEPTED_SET = new Set(ACCEPTED);
const MAX_MB = 25;
const MAX_BYTES = MAX_MB * 1024 * 1024;
const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface Props {
  teacherId: number;
  onUploaded: () => void;
  onCancel?: () => void;
}

function ext(filename: string) {
  return "." + filename.split(".").pop()!.toLowerCase();
}

function fmtSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function LessonUpload({ teacherId, onUploaded, onCancel }: Props) {
  const [title, setTitle] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [step, setStep] = useState(0); // 0=idle 1=sending 2=processing
  const inputRef = useRef<HTMLInputElement>(null);

  const addFiles = (incoming: FileList | File[]) => {
    const arr = Array.from(incoming);
    const rejected: string[] = [];
    const valid: File[] = [];

    for (const f of arr) {
      if (!ACCEPTED_SET.has(ext(f.name))) {
        rejected.push(`${f.name} (unsupported type)`);
        continue;
      }
      if (f.size > MAX_BYTES) {
        rejected.push(`${f.name} (${fmtSize(f.size)} — exceeds ${MAX_MB} MB limit)`);
        continue;
      }
      // deduplicate by name
      if (!files.some((existing) => existing.name === f.name)) {
        valid.push(f);
      }
    }

    setFiles((prev) => [...prev, ...valid]);
    if (rejected.length) setError(`Skipped: ${rejected.join("; ")}`);
    else setError(null);

    // Auto-fill title from first file if blank
    if (!title && valid.length > 0) {
      setTitle(valid[0].name.replace(/\.[^.]+$/, "").replace(/[-_]/g, " "));
    }
  };

  const removeFile = (name: string) => {
    setFiles((prev) => prev.filter((f) => f.name !== name));
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    addFiles(e.dataTransfer.files);
  };

  const submit = async () => {
    if (files.length === 0 || !title.trim()) return;
    setUploading(true);
    setError(null);
    setStep(1);
    setProgress(`Sending ${files.length} file${files.length > 1 ? "s" : ""}…`);
    try {
      const fd = new FormData();
      for (const f of files) fd.append("files", f);
      fd.append("title", title.trim());
      fd.append("teacher_id", String(teacherId));

      // Switch message once the request is in-flight (embedding can take several seconds)
      const timer = setTimeout(() => {
        setStep(2);
        setProgress("Processing and indexing content — this may take a moment…");
      }, 2000);

      const res = await fetch(`${API}/teacher/lessons`, { method: "POST", body: fd });
      clearTimeout(timer);

      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
      }
      setFiles([]);
      setTitle("");
      setProgress(null);
      setStep(0);
      onUploaded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
      setProgress(null);
      setStep(0);
    } finally {
      setUploading(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

      {/* Lesson title — first */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <label htmlFor="lesson-title" className="ll-label">
          Lesson title <span style={{ color: "var(--error)" }} aria-hidden="true">*</span>
        </label>
        <input
          id="lesson-title"
          className="ll-input"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          required
          aria-required="true"
        />
      </div>

      {/* Drop zone */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <p className="ll-label">Lesson files</p>
        <p className="ll-body" style={{ fontSize: 14, color: "var(--ink-muted)" }}>
          You can upload multiple files for a single lesson. Include whatever is useful — slides,
          a lesson plan, key vocabulary, common misconceptions, worksheets, quizzes, or any
          supplementary reading. The AI will read and index everything so it can answer pupil
          questions accurately.
        </p>
      </div>
      <div
        onDrop={onDrop}
        onDragOver={(e) => { e.preventDefault(); }}
        onClick={() => inputRef.current?.click()}
        style={{
          border: `2px dashed var(--ink-soft)`,
          borderRadius: "var(--radius-lg)",
          padding: "28px 24px",
          textAlign: "center",
          cursor: "pointer",
          background: "var(--paper-input)",
          transition: "background var(--dur-quick) var(--ease)",
        }}
        onDragEnter={(e) => { e.currentTarget.style.background = "var(--paper-shade)"; }}
        onDragLeave={(e) => { e.currentTarget.style.background = "var(--paper-input)"; }}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED.join(",")}
          multiple
          style={{ display: "none" }}
          onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }}
        />
        <p className="ll-body" style={{ color: "var(--ink-muted)" }}>
          Drop files here or click to browse
        </p>
        <p style={{ fontSize: 13, color: "var(--ink-muted)", marginTop: 4 }}>
          {ACCEPTED.join("  ·  ")} — up to {MAX_MB} MB each
        </p>
      </div>

      {/* Selected files list */}
      {files.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <p className="ll-label" style={{ marginBottom: 4 }}>
            {files.length} file{files.length > 1 ? "s" : ""} selected
          </p>
          {files.map((f) => (
            <div
              key={f.name}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "10px 14px",
                background: "var(--paper)",
                border: "1px solid var(--ink-soft)",
                borderRadius: "var(--radius-md)",
              }}
            >
              <div>
                <p className="ll-body" style={{ fontSize: 14 }}>{f.name}</p>
                <p style={{ fontSize: 12, color: "var(--ink-muted)", marginTop: 2 }}>
                  {fmtSize(f.size)} · {ext(f.name).slice(1).toUpperCase()}
                </p>
              </div>
              <button
                onClick={(e) => { e.stopPropagation(); removeFile(f.name); }}
                style={{
                  background: "none", border: "none", cursor: "pointer",
                  color: "var(--error)", fontFamily: "var(--font-sans)",
                  fontSize: 13, padding: "4px 8px",
                }}
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      )}

      {error && (
        <p style={{ color: "var(--error)", fontSize: 14 }}>{error}</p>
      )}

      {uploading && progress && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "12px 16px",
            background: "var(--paper-shade)",
            borderRadius: "var(--radius-md)",
            border: "1px solid var(--ink-soft)",
          }}
        >
          {/* Spinner */}
          <svg
            width="18" height="18" viewBox="0 0 18 18"
            style={{ flexShrink: 0, animation: "ll-spin 0.9s linear infinite" }}
          >
            <style>{`@keyframes ll-spin { to { transform: rotate(360deg); } }`}</style>
            <circle cx="9" cy="9" r="7" fill="none" stroke="var(--ink-soft)" strokeWidth="2.5" />
            <path d="M9 2 A7 7 0 0 1 16 9" fill="none" stroke="var(--action)" strokeWidth="2.5" strokeLinecap="round" />
          </svg>
          <div>
            <p style={{ fontSize: 14, color: "var(--ink)", fontFamily: "var(--font-sans)" }}>{progress}</p>
            {step === 2 && (
              <p style={{ fontSize: 12, color: "var(--ink-muted)", marginTop: 2 }}>
                The AI is reading and indexing your files — usually under a minute.
              </p>
            )}
          </div>
        </div>
      )}

      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <button
          className="ll-chip"
          onClick={submit}
          disabled={files.length === 0 || !title.trim() || uploading}
        >
          {uploading
            ? step === 2 ? "Indexing content…" : "Uploading…"
            : `Upload lesson${files.length > 1 ? ` (${files.length} files)` : ""}`}
        </button>
        {onCancel && !uploading && (
          <button className="ll-chip ll-chip--ghost" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </div>
  );
}
