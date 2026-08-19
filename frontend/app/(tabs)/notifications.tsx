import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View, Text, StyleSheet, Pressable, RefreshControl,
} from "react-native";
import { FlashList, type FlashListRef } from "@shopify/flash-list";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { api } from "@/src/api";
import { colors, spacing, font, radius } from "@/src/theme";
import { ScrollToTopButton } from "@/src/components/ScrollToTopButton";
import { NotificationListSkeleton } from "@/src/components/Skeleton";
import { useNotifications } from "@/src/notifications/NotificationsContext";
import { useAuth } from "@/src/auth/AuthContext";

type Notif = {
  notif_id: string;
  type: string;
  title: string;
  body: string;
  feud_id?: string | null;
  comment_id?: string | null;
  side?: "A" | "B" | null;
  read: boolean;
  created_at: string;
};

function formatWhen(iso: string): string {
  try {
    const then = new Date(iso).getTime();
    const diff = Math.max(0, Date.now() - then);
    const m = Math.floor(diff / 60000);
    if (m < 1) return "adesso";
    if (m < 60) return `${m} min fa`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h} h fa`;
    const d = Math.floor(h / 24);
    return `${d} g fa`;
  } catch { return ""; }
}

const ICONS: Record<string, keyof typeof Ionicons.glyphMap> = {
  reply: "chatbubble-ellipses",
  mention: "at",
  new_feud: "flame",
  vote_result: "checkmark-done",
};

export default function NotificationsScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const { refresh: refreshBadge } = useNotifications();
  const [items, setItems] = useState<Notif[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  // Floating "back to top" pill on the notifications list.
  const notifListRef = useRef<FlashListRef<Notif>>(null);
  const [showTopBtn, setShowTopBtn] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await api.notifications();
      setItems(r?.notifications || []);
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    (async () => {
      await load();
      setLoading(false);
    })();
  }, [load]);

  // On focus we refresh the list (so we see any brand-new notifications
  // that arrived while the user was elsewhere) BUT we deliberately do NOT
  // mark anything as read here — the user's explicit request is that a
  // notification's red-border "unread" indicator only clears when they
  // TAP that specific notification, not when they merely open the screen.
  // The per-item mark-read is fired in the row's onPress below.
  useFocusEffect(
    useCallback(() => {
      let cancelled = false;
      (async () => {
        await load();
        if (cancelled) return;
        // Keep the bell-icon tab badge in sync with the actual unread
        // count on the server (which only changes now when the user taps
        // an individual notification below).
        refreshBadge();
      })();
      return () => { cancelled = true; };
    }, [load, refreshBadge])
  );

  const onRefresh = async () => {
    setRefreshing(true);
    try { await load(); } finally { setRefreshing(false); }
  };

  const NotifSeparator = useMemo(() => {
    const S = () => <View style={styles.sep} />;
    S.displayName = "NotifSep";
    return S;
  }, []);

  if (!user) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]} testID="notif-noauth">
        <View style={styles.center}><Text style={styles.emptyTxt}>Devi essere loggato per vedere le notifiche.</Text></View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]} testID="notif-screen">
      <View style={styles.header}>
        <Text style={styles.brand}>NOTIFICHE</Text>
        <Text style={styles.subtitle}>{items.length === 0 ? "Nessuna novità" : `${items.length} eventi recenti`}</Text>
      </View>

      {loading ? (
        <NotificationListSkeleton count={6} />
      ) : items.length === 0 ? (
        <View style={styles.center} testID="notif-empty">
          <Ionicons name="notifications-off-outline" size={54} color={colors.muted} />
          <Text style={styles.emptyTxt}>Niente per ora. Torna più tardi.</Text>
        </View>
      ) : (
        <FlashList
          ref={notifListRef}
          data={items}
          keyExtractor={(i) => i.notif_id}
          contentContainerStyle={styles.list}
          ItemSeparatorComponent={NotifSeparator}
          onScroll={(e) => setShowTopBtn(e.nativeEvent.contentOffset.y > 500)}
          scrollEventThrottle={120}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.brandSecondary} />}
          renderItem={({ item }) => {
            const iconName = ICONS[item.type] || "notifications";
            return (
              <Pressable
                testID={`notif-${item.notif_id}`}
                onPress={() => {
                  // 1) Optimistically flip THIS notification to "read" in
                  //    local state so the red border disappears the moment
                  //    the user taps it.
                  // 2) Persist the change to the server (per-item endpoint)
                  //    so the same notification does not come back with a
                  //    red border after a screen refresh, and so the bell
                  //    tab-badge decrements to reflect the new state.
                  // 3) Deep-link into whatever the notification is about.
                  if (!item.read) {
                    setItems((prev) => prev.map((n) =>
                      n.notif_id === item.notif_id ? { ...n, read: true } : n
                    ));
                    // Fire-and-forget: no need to block navigation. The
                    // .then() refreshes the bell badge count so the
                    // number on the tab bar updates without waiting for
                    // the next full focus cycle.
                    api.notificationMarkOneRead(item.notif_id)
                      .then(() => { refreshBadge(); })
                      .catch(() => { /* silent */ });
                  }
                  if (item.feud_id) {
                    // Deep-link into the feud so the specific comment (and its
                    // reply thread) is opened automatically without extra taps.
                    // We ALSO carry `from=notifications` so the feud detail's
                    // top-left back arrow returns to /notifications instead
                    // of dumping the user on the home tab (feud/[id]'s
                    // `goBack` reads this param — same pattern used by
                    // top/archive/messages entrypoints).
                    const q = new URLSearchParams();
                    q.set("from", "notifications");
                    if (item.comment_id) q.set("comment", item.comment_id);
                    if (item.side) q.set("side", item.side);
                    // Nonce guarantees each tap produces a UNIQUE URL,
                    // even for the same notification. Without it, expo-
                    // router would deduplicate a re-tap to the same
                    // feud/comment and the receiving screen wouldn't
                    // fire its scroll-to-comment effect a second time.
                    q.set("t", String(Date.now()));
                    router.push(`/feud/${item.feud_id}?${q.toString()}`);
                  } else if (item.type === "badge") {
                    // Badge notifications (new/changed) → open the user's own
                    // profile so they can see the current badge and the full
                    // majority/minority stats behind it.
                    router.push("/profile");
                  }
                }}
                style={[styles.row, !item.read && styles.rowUnread]}
              >
                <View style={styles.iconWrap}>
                  <Ionicons name={iconName} size={22} color={colors.brandPrimary} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.rowTitle} numberOfLines={2}>{item.title}</Text>
                  {!!item.body && <Text style={styles.rowBody} numberOfLines={3}>{item.body}</Text>}
                  <Text style={styles.rowMeta}>{formatWhen(item.created_at)}</Text>
                </View>
                {!item.read && <View style={styles.dot} />}
              </Pressable>
            );
          }}
        />
      )}
      <ScrollToTopButton
        visible={showTopBtn}
        onPress={() => notifListRef.current?.scrollToOffset({ offset: 0, animated: true })}
        testID="notif-scroll-top"
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: { paddingHorizontal: spacing.lg, paddingVertical: spacing.lg, backgroundColor: colors.surfaceInverse },
  brand: { color: colors.onSurface, fontSize: font.sizes.xxxl, letterSpacing: 1.5, fontWeight: "800" },
  subtitle: { color: colors.muted, fontSize: font.sizes.sm, letterSpacing: 0.5, marginTop: 4, fontWeight: "600" },
  list: { padding: spacing.md, paddingBottom: spacing.xxxl, gap: spacing.sm },
  sep: { height: spacing.sm },
  row: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing.md,
    padding: spacing.md,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
  },
  rowUnread: {
    // Distinct red border + soft red tint so the user can tell at a
    // glance which notifications they haven't opened yet.
    backgroundColor: "rgba(255,69,58,0.10)",
    borderColor: colors.brandPrimary,
    borderWidth: 2,
  },
  iconWrap: {
    width: 44,
    height: 44,
    borderWidth: 1.5,
    borderColor: colors.brandPrimary,
    borderRadius: radius.sm,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "transparent",
  },
  rowTitle: { color: colors.onSurface, fontSize: font.sizes.base, fontWeight: "700" },
  rowBody: { color: colors.muted, fontSize: font.sizes.sm, marginTop: 4, lineHeight: 20 },
  rowMeta: { color: colors.muted, fontSize: font.sizes.xs, marginTop: 6, letterSpacing: 0.5 },
  dot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.brandPrimary, marginTop: 8 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", gap: spacing.md, padding: spacing.xl },
  emptyTxt: { color: colors.muted, fontSize: font.sizes.base, textAlign: "center" },
});
