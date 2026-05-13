"use client";
import { useCallback, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface Features {
  prompt_cards: boolean;
  tappable_terms: boolean;
}

/**
 * Two opt-in toggles that gate the Gemma-backed live features for this
 * session. Both default OFF on the backend so transcription stays snappy
 * on consumer hardware — the teacher turns them on if they want the extra
 * pupil-facing polish and they're not seeing transcription lag.
 *
 * State source of truth is the server: we fetch on mount and PATCH on
 * toggle. The transcription handler reads the in-memory flags on every
 * Gemma-firing tick, so changes take effect within ~one chunk.
 */
export default function LiveFeaturesPanel({ sessionId }: { sessionId: number }) {
  const [features, setFeatures] = useState<Features | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<keyof Features | null>(null);

  // Load initial state. Falls back to "both off" if the GET fails — matches
  // the server default, so we'd at worst show a wrong checked state briefly.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${API}/session/${sessionId}/features`);
        if (r.ok && !cancelled) {
          setFeatures(await r.json());
        } else if (!cancelled) {
          setFeatures({ prompt_cards: false, tappable_terms: false });
        }
      } catch {
        if (!cancelled) setFeatures({ prompt_cards: false, tappable_terms: false });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const toggle = useCallback(
    async (key: keyof Features, next: boolean) => {
      if (!features) return;
      // Optimistic update — the checkbox flips immediately. If the PATCH
      // fails we revert and surface the error.
      const previous = features;
      setFeatures({ ...features, [key]: next });
      setSaving(key);
      setError(null);
      try {
        const r = await fetch(`${API}/session/${sessionId}/features`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ [key]: next }),
        });
        if (!r.ok) throw new Error(await r.text());
        setFeatures(await r.json());
      } catch (e) {
        setFeatures(previous);
        setError(e instanceof Error ? e.message : "Could not update setting.");
      } finally {
        setSaving(null);
      }
    },
    [features, sessionId],
  );

  if (!features) return null;

  return (
    <div className="ll-card" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div>
        <p className="ll-label">Live features for pupils</p>
        <p className="ll-body" style={{ color: "var(--ink-muted)", marginTop: 4 }}>
          These features make the pupil app richer but run extra AI in the background.
          Leave them off if transcription feels laggy on this laptop — turn them on if
          you have headroom to spare.
        </p>
      </div>

      <FeatureToggle
        label="Prompt cards"
        description="Short suggested questions that pop up on pupils' tablets every minute or so, based on what you just said. Helps shy pupils start a conversation with the Class Helper without having to think of a question."
        checked={features.prompt_cards}
        saving={saving === "prompt_cards"}
        onChange={(next) => toggle("prompt_cards", next)}
      />

      <FeatureToggle
        label="Tappable words"
        description="Underlines tricky vocabulary in the pupil's live transcript. Tapping an underlined word reveals a short kid-friendly explanation. Useful for pupils who'd otherwise lose the thread when a new term comes up."
        checked={features.tappable_terms}
        saving={saving === "tappable_terms"}
        onChange={(next) => toggle("tappable_terms", next)}
      />

      {error && <p className="ll-body" style={{ color: "var(--error)" }}>{error}</p>}
    </div>
  );
}

function FeatureToggle({
  label,
  description,
  checked,
  saving,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  saving: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <label
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 14,
        padding: "12px 14px",
        background: "var(--paper-shade)",
        borderRadius: 12,
        cursor: saving ? "wait" : "pointer",
      }}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={saving}
        onChange={(e) => onChange(e.target.checked)}
        style={{ marginTop: 4, width: 18, height: 18, accentColor: "var(--action)" }}
      />
      <div style={{ flex: 1 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <p className="ll-subheading">{label}</p>
          {saving && (
            <span style={{ font: "500 11px/1 var(--font-sans)", color: "var(--ink-muted)" }}>
              saving…
            </span>
          )}
        </div>
        <p className="ll-body" style={{ color: "var(--ink-muted)", marginTop: 4 }}>
          {description}
        </p>
      </div>
    </label>
  );
}
