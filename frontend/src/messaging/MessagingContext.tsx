import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { AppState } from "react-native";
import { api, getToken, ChatMessage } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";

/**
 * Global messaging state:
 * - Maintains an authenticated WebSocket for real-time delivery.
 * - Exposes an unread badge count that automatically updates on `message.new`
 *   / `message.read` events.
 * - Publishes typed events to per-screen subscribers so chat pages can react
 *   without maintaining their own socket.
 *
 * NOTE: WS is skipped entirely for anonymous or unauthenticated users.
 * The socket auto-reconnects with exponential backoff (max 30s).
 */

type WsEvent =
  | { type: "hello"; user_id: string }
  | { type: "message.new"; message: ChatMessage }
  | { type: "message.sent"; message: ChatMessage }
  | { type: "message.read"; conversation_id: string; message_ids: string[]; read_at: string }
  | { type: "message.reaction"; message: ChatMessage }
  | { type: "message.deleted"; message: ChatMessage };

type Listener = (ev: WsEvent) => void;

type Ctx = {
  unread: number;
  connected: boolean;
  refresh: () => Promise<void>;
  subscribe: (fn: Listener) => () => void;
};

const MessagingContext = createContext<Ctx>({
  unread: 0,
  connected: false,
  refresh: async () => {},
  subscribe: () => () => {},
});

function wsUrlFor(base: string, token: string): string {
  // Convert http(s):// → ws(s)://
  const u = base.replace(/^http/, "ws");
  return `${u}/api/ws/messages?token=${encodeURIComponent(token)}`;
}

export function MessagingProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const [unread, setUnread] = useState(0);
  const [connected, setConnected] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);
  const listenersRef = useRef<Set<Listener>>(new Set());
  const retryRef = useRef<number>(0);
  const reconnectTimerRef = useRef<any>(null);
  const pingTimerRef = useRef<any>(null);
  const pollTimerRef = useRef<any>(null);

  const canUse = !!user && !user.is_anonymous;

  const refresh = useCallback(async () => {
    if (!canUse) {
      setUnread(0);
      return;
    }
    try {
      const r = await api.messagesUnreadCount();
      setUnread(r?.count || 0);
    } catch {
      /* silent */
    }
  }, [canUse]);

  const clearTimers = () => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (pingTimerRef.current) {
      clearInterval(pingTimerRef.current);
      pingTimerRef.current = null;
    }
  };

  const closeSocket = useCallback(() => {
    clearTimers();
    const s = socketRef.current;
    socketRef.current = null;
    setConnected(false);
    try {
      s?.close();
    } catch {
      /* ignore */
    }
  }, []);

  const connect = useCallback(async () => {
    if (!canUse) return;
    const base = process.env.EXPO_PUBLIC_BACKEND_URL;
    if (!base) return;
    const token = await getToken();
    if (!token) return;
    // Close existing socket first.
    closeSocket();
    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrlFor(base, token));
    } catch {
      scheduleReconnect();
      return;
    }
    socketRef.current = ws;
    ws.onopen = () => {
      retryRef.current = 0;
      setConnected(true);
      // Keepalive ping every 25s (in case proxies drop idle sockets).
      pingTimerRef.current = setInterval(() => {
        try {
          ws.send("ping");
        } catch {
          /* ignore */
        }
      }, 25_000);
    };
    ws.onmessage = (ev) => {
      if (!ev?.data || ev.data === "pong") return;
      try {
        const data = typeof ev.data === "string" ? JSON.parse(ev.data) : ev.data;
        handleEvent(data);
      } catch {
        /* ignore bad payload */
      }
    };
    ws.onerror = () => {
      /* onclose will schedule reconnect */
    };
    ws.onclose = () => {
      setConnected(false);
      socketRef.current = null;
      clearTimers();
      scheduleReconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canUse, closeSocket]);

  const scheduleReconnect = useCallback(() => {
    if (!canUse) return;
    if (reconnectTimerRef.current) return;
    const attempt = Math.min(retryRef.current, 5);
    const delay = Math.min(2 ** attempt * 1000, 30_000);
    retryRef.current += 1;
    reconnectTimerRef.current = setTimeout(() => {
      reconnectTimerRef.current = null;
      connect();
    }, delay);
  }, [canUse, connect]);

  const handleEvent = useCallback(
    (ev: WsEvent) => {
      if (ev.type === "message.new") {
        // Only count messages addressed to us.
        if (user && ev.message.recipient_id === user.user_id && !ev.message.read_at) {
          setUnread((u) => u + 1);
        }
      } else if (ev.type === "message.read") {
        // Recipient side read our messages — no unread change for us.
      }
      listenersRef.current.forEach((fn) => {
        try {
          fn(ev);
        } catch {
          /* listener error */
        }
      });
    },
    [user],
  );

  const subscribe = useCallback((fn: Listener) => {
    listenersRef.current.add(fn);
    return () => {
      listenersRef.current.delete(fn);
    };
  }, []);

  useEffect(() => {
    if (!canUse) {
      closeSocket();
      setUnread(0);
      return;
    }
    refresh();
    connect();
    // Foreground polling fallback (in case WS drops silently).
    pollTimerRef.current = setInterval(refresh, 30_000);
    const sub = AppState.addEventListener("change", (state) => {
      if (state === "active") {
        refresh();
        if (!socketRef.current) connect();
      }
    });
    return () => {
      sub.remove();
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
      closeSocket();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canUse, user?.user_id]);

  const value = useMemo<Ctx>(
    () => ({ unread, connected, refresh, subscribe }),
    [unread, connected, refresh, subscribe],
  );

  return <MessagingContext.Provider value={value}>{children}</MessagingContext.Provider>;
}

export function useMessaging() {
  return useContext(MessagingContext);
}
