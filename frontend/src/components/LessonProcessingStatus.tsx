"use client";

/**
 * Step-by-step processing indicator for a lesson.
 *
 * Replaces the previous binary "AI analysis in progress" spinner, which
 * disappeared the moment the summary landed even though pre-computed live
 * features (glossary, prompt cards) were still being generated in the
 * background. The new pipeline has three sequential stages off the wire:
 *
 *   1. Reading the file        ← extraction + chunking
 *   2. Writing the summary     ← summarise_lesson()
 *   3. Preparing live features ← glossary + prompt-card precompute
 *
 * Each stage is inferred from the lesson row; we never trust the backend
 * to send a "current stage" string because that field would need to stay
 * synchronised with whatever the worker decides to do next.
 *
 * Keep this component dumb: it just maps fields to a label + spinner.
 * The parent decides whether to render it at all.
 */

interface LessonProcessingFields {
  chunk_count: number;
  summary: string | null;
  precomputed_features_at: string | null;
  precomputed_features_attempts: number;
}

// Must match PRECOMPUTE_MAX_ATTEMPTS in lesson_summary_worker.py. If we
// raise the backend cap, raise this here too — otherwise the UI would
// show "Preparing…" forever after the worker has given up.
const PRECOMPUTE_MAX_ATTEMPTS = 5;

export type LessonStage =
  | "reading"
  | "summarising"
  | "preparing_features"
  | "ready"
  | "features_failed";

export function deriveLessonStage(lesson: LessonProcessingFields): LessonStage {
  if (!lesson.chunk_count || lesson.chunk_count === 0) return "reading";
  if (lesson.summary == null) return "summarising";
  if (!lesson.precomputed_features_at) {
    if (lesson.precomputed_features_attempts >= PRECOMPUTE_MAX_ATTEMPTS) {
      return "features_failed";
    }
    return "preparing_features";
  }
  return "ready";
}

const STAGE_LABEL: Record<LessonStage, string> = {
  reading: "Reading file…",
  summarising: "Writing summary…",
  preparing_features: "Preparing live features…",
  ready: "Ready for live lesson",
  features_failed: "Live features unavailable",
};

const STAGE_TOOLTIP: Record<LessonStage, string> = {
  reading: "Extracting text and slides so the AI can read the lesson.",
  summarising: "Gemma is writing a short paragraph summary.",
  preparing_features:
    "Gemma is preparing the glossary of tricky words and the prompt cards pupils will see during the lesson. This takes a few minutes for a large file.",
  ready: "All pre-lesson AI work is done — start a session whenever you're ready.",
  features_failed:
    "Tried 5 times and Gemma kept failing — the lesson will still work but pupils won't see tappable words or prompt cards. Re-uploading may fix it.",
};

function Spinner() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 13 13"
      style={{ flexShrink: 0, animation: "ll-spin 1.1s linear infinite" }}
      aria-hidden="true"
    >
      <circle cx="6.5" cy="6.5" r="5" fill="none" stroke="var(--ink-soft)" strokeWidth="2" />
      <path
        d="M6.5 1.5 A5 5 0 0 1 11.5 6.5"
        fill="none"
        stroke="var(--action)"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

function ReadyTick() {
  return (
    <svg width="13" height="13" viewBox="0 0 13 13" aria-hidden="true">
      <circle cx="6.5" cy="6.5" r="5.5" fill="var(--success-soft)" />
      <path
        d="M3.8 6.7 L5.6 8.4 L9.2 4.6"
        fill="none"
        stroke="var(--success)"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function WarnIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 13 13" aria-hidden="true">
      <circle cx="6.5" cy="6.5" r="5.5" fill="var(--warn-soft)" />
      <text
        x="6.5"
        y="9.3"
        textAnchor="middle"
        fontSize="8"
        fontWeight="700"
        fill="var(--warn)"
        fontFamily="var(--font-sans)"
      >
        !
      </text>
    </svg>
  );
}

interface Props {
  lesson: LessonProcessingFields;
  /** If true, hide the indicator entirely when stage is "ready". */
  hideWhenReady?: boolean;
  /** Override the default label colour (used in the dense list view). */
  textColor?: string;
}

export default function LessonProcessingStatus({
  lesson,
  hideWhenReady = false,
  textColor = "var(--ink-muted)",
}: Props) {
  const stage = deriveLessonStage(lesson);
  if (stage === "ready" && hideWhenReady) return null;

  const Icon =
    stage === "ready" ? ReadyTick : stage === "features_failed" ? WarnIcon : Spinner;

  return (
    <div
      style={{ display: "flex", alignItems: "center", gap: 6 }}
      title={STAGE_TOOLTIP[stage]}
    >
      <Icon />
      <p
        style={{
          fontSize: 13,
          color: textColor,
          fontFamily: "var(--font-sans)",
        }}
      >
        {STAGE_LABEL[stage]}
      </p>
    </div>
  );
}
