"use client";
import { useCallback, useEffect, useRef, useState } from "react";

type Status = "connecting" | "open" | "closed" | "error";

interface UseWebSocketOptions {
  onMessage?: (data: unknown) => void;
  onOpen?: () => void;
  onClose?: () => void;
  reconnectDelayMs?: number;
}

export function useWebSocket(url: string | null, options: UseWebSocketOptions = {}) {
  const { onMessage, onOpen, onClose, reconnectDelayMs = 2000 } = options;
  const [status, setStatus] = useState<Status>("closed");
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  const connect = useCallback(() => {
    if (!url || !mountedRef.current) return;
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;

    setStatus("connecting");
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) return;
      setStatus("open");
      onOpen?.();
    };

    ws.onmessage = (evt) => {
      if (!mountedRef.current) return;
      try {
        onMessage?.(JSON.parse(evt.data as string));
      } catch {
        onMessage?.(evt.data);
      }
    };

    ws.onclose = () => {
      if (!mountedRef.current) return;
      setStatus("closed");
      onClose?.();
      reconnectTimer.current = setTimeout(connect, reconnectDelayMs);
    };

    ws.onerror = () => {
      if (!mountedRef.current) return;
      setStatus("error");
      ws.close();
    };
  }, [url, onMessage, onOpen, onClose, reconnectDelayMs]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const send = useCallback((data: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(typeof data === "string" ? data : JSON.stringify(data));
    }
  }, []);

  const disconnect = useCallback(() => {
    mountedRef.current = false;
    if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    wsRef.current?.close();
  }, []);

  return { status, send, disconnect };
}
