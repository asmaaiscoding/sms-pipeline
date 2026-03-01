import { useEffect, useRef, useCallback, useState } from "react";
import type { ProgressEvent } from "@/types";

interface UseWebSocketOptions {
  projectId: string;
  onProgress?: (event: ProgressEvent) => void;
  enabled?: boolean;
}

export function useWebSocket({
  projectId,
  onProgress,
  enabled = true,
}: UseWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout>>();

  const connect = useCallback(() => {
    if (!enabled || !projectId) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const url = `${protocol}//${host}/ws/projects/${projectId}/progress`;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
    };

    ws.onmessage = (event) => {
      try {
        const data: ProgressEvent = JSON.parse(event.data);
        onProgress?.(data);
      } catch {
        // ignore non-JSON messages (pong, etc.)
      }
    };

    ws.onclose = () => {
      setConnected(false);
      // Auto-reconnect after 3s
      if (enabled) {
        reconnectTimerRef.current = setTimeout(connect, 3000);
      }
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [projectId, onProgress, enabled]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const sendPing = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send("ping");
    }
  }, []);

  return { connected, sendPing };
}
