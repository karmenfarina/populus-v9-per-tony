import { useCallback, useEffect, useState } from "react";
import {
  View, Text, StyleSheet, FlatList, ActivityIndicator, Pressable, RefreshControl,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { api } from "@/src/api";
import { colors, spacing, font } from "@/src/theme";
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

  // Whenever the screen gains focus, mark all as read and refresh both the
  // list and the tab badge — so the little red dot goes away as soon as the
  // user sees the notifications.
  useFocusEffect(
    useCallback(() => {
      let cancelled = false;
      (async () => {
        try { await api.notificationsMarkRead(); } catch { /* silent */ }
        await load();
        if (!cancelled) refreshBadge();
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
          data={items}
          keyExtractor={(i) => i.notif_id}
          contentContainerStyle={styles.list}
          ItemSeparatorComponent={() => <View style={styles.sep} />}
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
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderBottomWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceInverse },
  brand: { color: colors.brandSecondary, fontSize: font.sizes.xxxl, letterSpacing: 2, fontWeight: "500" },
  subtitle: { color: colors.onSurfaceInverse, fontSize: font.sizes.sm, letterSpacing: 1, marginTop: 2 },
  list: { paddingBottom: spacing.xxxl },
  sep: { height: 1, backgroundColor: colors.border },
  row: { flexDirection: "row", alignItems: "flex-start", gap: spacing.md, padding: spacing.md, backgroundColor: colors.surface },
  rowUnread: { backgroundColor: "rgba(240,26,26,0.04)" },
  iconWrap: { width: 40, height: 40, borderWidth: 2, borderColor: colors.brandPrimary, alignItems: "center", justifyContent: "center" },
  rowTitle: { color: colors.onSurface, fontSize: font.sizes.base, fontWeight: "500" },
  rowBody: { color: colors.muted, fontSize: font.sizes.sm, marginTop: 2 },
  rowMeta: { color: colors.muted, fontSize: font.sizes.xs, marginTop: 4, letterSpacing: 1 },
  dot: { width: 10, height: 10, borderRadius: 5, backgroundColor: colors.brandPrimary, marginTop: 6 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", gap: spacing.md, padding: spacing.xl },
  emptyTxt: { color: colors.muted, fontSize: font.sizes.base, textAlign: "center" },
});
