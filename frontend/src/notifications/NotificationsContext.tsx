import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { AppState } from "react-native";
import { api } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { registerForPush } from "@/src/notifications/push";

/**
 * Lightweight polling context that exposes the unread notification count.
 * - Polls every 30s while the app is foregrounded.
 * - Refreshes immediately when the app returns to the foreground.
 * - Skips polling for anonymous / unauthenticated users.
 * - `refresh()` can be called by screens (e.g. Notifications page) to force
 *   an immediate re-check after a `mark-read`.
 */
type Ctx = { unread: number; refresh: () => Promise<void> };
const NotificationsContext = createContext<Ctx>({ unread: 0, refresh: async () => {} });

export function NotificationsProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const [unread, setUnread] = useState(0);
  const tick = useRef<any>(null);

  const refresh = useCallback(async () => {
    if (!user) { setUnread(0); return; }
    try {
      const r = await api.notificationsUnreadCount();
      setUnread(r?.count || 0);
    } catch { /* silent */ }
  }, [user]);

  useEffect(() => {
    refresh();
    // Best-effort mobile push registration for the logged-in user. Runs on
    // every auth transition — tokens can rotate so re-registration is safe.
    if (user && !user.is_anonymous) {
      registerForPush();
    }
    tick.current = setInterval(refresh, 30_000);
    const sub = AppState.addEventListener("change", (state) => {
      if (state === "active") {
        refresh();
        if (user && !user.is_anonymous) registerForPush();
      }
    });
    return () => {
      if (tick.current) clearInterval(tick.current);
      sub.remove();
    };
  }, [refresh, user]);

  return (
    <NotificationsContext.Provider value={{ unread, refresh }}>
      {children}
    </NotificationsContext.Provider>
  );
}

export function useNotifications() {
  return useContext(NotificationsContext);
}
