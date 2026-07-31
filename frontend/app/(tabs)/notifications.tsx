import { useCallback, useEffect, useRef, useState } from "react";
import {
  View, Text, StyleSheet, FlatList, ActivityIndicator, Pressable, RefreshControl,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { api } from "@/src/api";
import { colors, spacing, font, radius } from "@/src/theme";
import { ScrollToTopButton } from "@/src/components/ScrollToTopButton";
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
  const notifListRef = useRef<FlatList<Notif>>(null);
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

  // Whenever the screen gains focus we do two things IN PARALLEL:
  //   1. Fetch the latest notifications so the list renders the current
  //      unread state — this preserves the red border/tinted background
  //      on any notification the user hasn't opened yet.
  //   2. Fire the "mark all read" call on the server in the background.
  //      We deliberately do NOT reload the list after that call resolves:
  //      if we did, the freshly-fetched items would come back with
  //      `read: true` and the visual "new" indicator would disappear the
  //      instant the user opens the screen — before they've had a chance
  //      to look at what's new. The tab-badge refresh is enough to clear
  //      the numeric counter on the bell icon.
  useFocusEffect(
    useCallback(() => {
      let cancelled = false;
      (async () => {
        await load();
        if (cancelled) return;
        // Fire-and-forget the server-side mark-read so the badge count
        // on the tab bar drops to 0 without altering the on-screen
        // items' `read` flag mid-view.
        api.notificationsMarkRead()
          .then(() => { if (!cancelled) refreshBadge(); })
          .catch(() => { /* silent */ });
      })();
      return () => { cancelled = true; };
    }, [load, refreshBadge])
  );

  const onRefresh = async () => {
    setRefreshing(true);
    try { await load(); } finally { setRefreshing(false); }
  };

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
        <View style={styles.center}><ActivityIndicator size="large" color={colors.brandPrimary} /></View>
      ) : items.length === 0 ? (
        <View style={styles.center} testID="notif-empty">
          <Ionicons name="notifications-off-outline" size={54} color={colors.muted} />
          <Text style={styles.emptyTxt}>Niente per ora. Torna più tardi.</Text>
        </View>
      ) : (
        <FlatList
          ref={notifListRef}
          data={items}
          keyExtractor={(i) => i.notif_id}
          contentContainerStyle={styles.list}
          ItemSeparatorComponent={() => <View style={styles.sep} />}
          onScroll={(e) => setShowTopBtn(e.nativeEvent.contentOffset.y > 500)}
          scrollEventThrottle={120}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.brandSecondary} />}
          renderItem={({ item }) => {
            const iconName = ICONS[item.type] || "notifications";
            return (
              <Pressable
                testID={`notif-${item.notif_id}`}
                onPress={() => {
                  if (item.feud_id) {
                    // Deep-link into the feud so the specific comment (and its
                    // reply thread) is opened automatically without extra taps.
                    const q = new URLSearchParams();
                    if (item.comment_id) q.set("comment", item.comment_id);
                    if (item.side) q.set("side", item.side);
                    const qs = q.toString();
                    router.push(`/feud/${item.feud_id}${qs ? `?${qs}` : ""}`);
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
