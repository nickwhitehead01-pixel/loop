"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useWebSocket } from "@/hooks/useWebSocket";

const WS_BASE = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000";

interface Message {
  role: "user" | "assistant";
  text: string;
  pending?: boolean;
}

interface Props {
  teacherId: number;
}

export default function TeacherChat({ teacherId }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [waiting, setWaiting] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const onMessage = useCallback((data: unknown) => {
    const frame = data as Record<string, unknown>;

    if (frame.type === "conversation_created" && typeof frame.conversation_id === "number") {
      setConversationId(frame.conversation_id);
      return;
    }

    if (typeof frame.token === "string") {
      const token = frame.token;
      const done = frame.done === true;
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last?.role === "assistant" && last.pending) {
          const updated: Message = { ...last, text: last.text + token, pending: !done };
          return [...prev.slice(0, -1), updated];
        }
        return [...prev, { role: "assistant" as const, text: token, pending: !done }];
      });
      if (done) setWaiting(false);
    }
  }, []);

  const { status, send } = useWebSocket(
    `${WS_BASE}/teacher/ws/${teacherId}/chat`,
    { onMessage }
  );

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const submit = () => {
    const text = input.trim();
    if (!text || waiting || status !== "open") return;
    setMessages((prev) => [...prev, { role: "user", text }]);
    setInput("");
    setWaiting(true);
    send({ message: text, conversation_id: conversationId });
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: 0 }}>
      {/* Connection badge */}
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
        <span
          className="ll-pill"
          style={{
            background:
              status === "open" ? "var(--success-soft)" :
              status === "connecting" ? "var(--warn-soft)" : "var(--error-soft)",
            color:
              status === "open" ? "var(--success)" :
              status === "connecting" ? "var(--warn)" : "var(--error)",
          }}
        >
          {status === "open" ? "Connected" : status === "connecting" ? "Connecting…" : "Disconnected"}
        </span>
      </div>

      {/* Message list */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 12,
          paddingBottom: 8,
        }}
      >
        {messages.length === 0 && (
          <p className="ll-body" style={{ color: "var(--ink-muted)", marginTop: 24, textAlign: "center" }}>
            Ask me anything about your lessons, pupils, or sessions.
          </p>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className="ll-turn"
            style={{
              alignSelf: m.role === "user" ? "flex-end" : "flex-start",
              maxWidth: "80%",
              background: m.role === "user" ? "var(--action)" : "var(--paper)",
              color: m.role === "user" ? "#fff" : "var(--ink)",
              border: m.role === "user" ? "none" : "1px solid var(--ink-soft)",
            }}
          >
            <p style={{ margin: 0, whiteSpace: "pre-wrap", lineHeight: 1.6 }}>
              {m.text}
              {m.pending && <span style={{ opacity: 0.5 }}>▌</span>}
            </p>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input row */}
      <div style={{ display: "flex", gap: 10, marginTop: 12, alignItems: "flex-end" }}>
        <textarea
          className="ll-input"
          rows={2}
          placeholder="Ask the AI assistant…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={waiting || status !== "open"}
          style={{ flex: 1, resize: "none" }}
        />
        <button
          className="ll-chip"
          onClick={submit}
          disabled={!input.trim() || waiting || status !== "open"}
          style={{ alignSelf: "stretch" }}
        >
          Send
        </button>
      </div>
    </div>
  );
}
