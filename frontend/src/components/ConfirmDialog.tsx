"use client";
import { useEffect } from "react";
import { createPortal } from "react-dom";

interface Props {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  dangerous?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmDialog({
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  dangerous = false,
  onConfirm,
  onCancel,
}: Props) {
  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onCancel]);

  const dialog = (
    <div
      className="ll-backdrop"
      onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="ll-dialog-title"
      aria-describedby="ll-dialog-body"
    >
      <div className="ll-dialog">
        <p className="ll-dialog__title" id="ll-dialog-title">{title}</p>
        <p className="ll-dialog__body" id="ll-dialog-body">{message}</p>
        <div className="ll-dialog__actions">
          <button className="ll-chip ll-chip--ghost" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button
            className={`ll-chip${dangerous ? " ll-chip--danger" : ""}`}
            onClick={onConfirm}
            autoFocus
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );

  // Render into document.body so it sits above everything
  return typeof window !== "undefined"
    ? createPortal(dialog, document.body)
    : null;
}
