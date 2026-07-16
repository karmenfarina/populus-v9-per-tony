import React, { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  Pressable,
  FlatList,
  Image,
  StyleSheet,
  ActivityIndicator,
  RefreshControl,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useFocusEffect, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { api, Conversation } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { useMessaging } from "@/src/messaging/MessagingContext";
import { colors, spacing, font } from "@/src/theme";

function relative(ts: string | null): string {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    const diff = Date.now() - d.getTime();
    if (diff < 60_000) return "ora";
    if (diff < 3600_000) return `${Math.floor(diff / 60_000)} min`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3600_000)} h`;
    if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)} g`;
    return d.toLocaleDateString("it-IT", { day: "numeric", month: "short" });
  } catch {
    return "";
  }
}

export default function MessagesListScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const { refresh: refreshBadge, subscribe, connected } = useMessaging();
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await api.conversations();
      setConvs(r.conversations || []);
    } catch {
      /* silent */
    }
  }, []);

  useEffect(() => {
    if (!user || user.is_anonymous) return;
    let mounted = true;
    (async () => {
      setLoading(true);
      await load();
      if (mounted) setLoading(false);
    })();
    return () => {
      mounted = false;
    };
  }, [user, load]);

  useFocusEffect(
    useCallback(() => {
      if (!user || user.is_anonymous) return;
      load();
      refreshBadge();
    }, [user, load, refreshBadge]),
  );

  // Live updates: reload conversation list on any relevant ws event.
  useEffect(() => {
    const unsub = subscribe((ev) => {
      if (
        ev.type === "message.new" ||
        ev.type === "message.sent" ||
        ev.type === "message.read" ||
        ev.type === "message.deleted"
      ) {
        load();
      }
    });
    return () => unsub();
  }, [subscribe, load]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await load();
    await refreshBadge();
    setRefreshing(false);
  }, [load, refreshBadge]);

  if (!user || user.is_anonymous) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]}>
        <View style={styles.header}>
          <Text style={styles.headerTitle}>MESSAGGI</Text>
        </View>
        <View style={styles.emptyBox}>
          <Ionicons name="lock-closed-outline" size={64} color={colors.muted} />
          <Text style={styles.emptyTitle}>ACCESSO BLOCCATO</Text>
          <Text style={styles.emptyBody}>
            La chat è disponibile solo per gli utenti registrati. Gli utenti anonimi non possono inviare né ricevere messaggi.
          </Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]} testID="messages-screen">
      <View style={styles.header}>
        <Text style={styles.headerTitle}>MESSAGGI</Text>
        <View style={styles.headerStatus}>
          <View style={[styles.dot, connected ? styles.dotOn : styles.dotOff]} />
          <Text style={styles.headerStatusTxt}>{connected ? "LIVE" : "OFFLINE"}</Text>
        </View>
      </View>
      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.brandPrimary} />
        </View>
      ) : (
        <FlatList
          data={convs}
          keyExtractor={(c) => c.conversation_id}
          contentContainerStyle={convs.length === 0 ? styles.emptyBox : { paddingBottom: spacing.xxl }}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.brandPrimary} />
          }
          ListEmptyComponent={
            <>
              <Ionicons name="chatbubble-ellipses-outline" size={64} color={colors.muted} />
              <Text style={styles.emptyTitle}>NESSUNA CONVERSAZIONE</Text>
              <Text style={styles.emptyBody}>
                Vai sul profilo di un utente e tocca &quot;Invia messaggio&quot; per iniziare una conversazione.
              </Text>
            </>
          }
          renderItem={({ item }) => (
            <Pressable
              onPress={() => router.push({ pathname: "/messages/[userId]", params: { userId: item.other_user.user_id } })}
              style={styles.row}
              testID={`convo-${item.other_user.user_id}`}
            >
              <View style={styles.avatarWrap}>
                {item.other_user.photo_data ? (
                  <Image
                    source={{ uri: `data:image/jpeg;base64,${item.other_user.photo_data}` }}
                    style={styles.avatar}
                  />
                ) : (
                  <View style={[styles.avatar, styles.avatarPh]}>
                    <Ionicons name="person" size={28} color={colors.muted} />
                  </View>
                )}
              </View>
              <View style={styles.rowBody}>
                <View style={styles.rowHead}>
                  <Text
                    style={[styles.nick, item.unread > 0 && styles.nickBold]}
                    numberOfLines={1}
                  >
                    @{item.other_user.nickname}
                  </Text>
                  <Text style={styles.time}>{relative(item.last_message_at)}</Text>
                </View>
                <View style={styles.rowFoot}>
                  <Text
                    style={[styles.preview, item.unread > 0 && styles.previewBold]}
                    numberOfLines={1}
                  >
                    {item.last_sender_id === user.user_id ? "Tu: " : ""}
                    {item.last_message_preview || "…"}
                  </Text>
                  {item.unread > 0 && (
                    <View style={styles.badge}>
                      <Text style={styles.badgeTxt}>{item.unread > 99 ? "99+" : item.unread}</Text>
                    </View>
                  )}
                </View>
              </View>
            </Pressable>
          )}
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    backgroundColor: colors.surfaceInverse,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: 2,
    borderColor: colors.border,
  },
  headerTitle: { color: colors.brandSecondary, fontSize: font.sizes.lg, letterSpacing: 3, fontWeight: "500" },
  headerStatus: { flexDirection: "row", alignItems: "center", gap: 6 },
  headerStatusTxt: { color: colors.onSurfaceInverse, fontSize: font.sizes.xs, letterSpacing: 1 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  dotOn: { backgroundColor: colors.success },
  dotOff: { backgroundColor: colors.muted },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  emptyBox: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xxl, gap: spacing.md },
  emptyTitle: { fontSize: font.sizes.lg, letterSpacing: 2, color: colors.onSurface, fontWeight: "500" },
  emptyBody: { fontSize: font.sizes.sm, color: colors.muted, textAlign: "center", lineHeight: 20 },
  row: {
    flexDirection: "row",
    padding: spacing.md,
    gap: spacing.md,
    borderBottomWidth: 1,
    borderColor: colors.surfaceTertiary,
    backgroundColor: colors.surfaceSecondary,
  },
  avatarWrap: { width: 56, height: 56 },
  avatar: { width: 56, height: 56, borderRadius: 28, backgroundColor: colors.surfaceTertiary },
  avatarPh: { alignItems: "center", justifyContent: "center" },
  rowBody: { flex: 1, gap: 4 },
  rowHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  nick: { fontSize: font.sizes.base, color: colors.onSurface, flexShrink: 1 },
  nickBold: { fontWeight: "700" },
  time: { fontSize: font.sizes.xs, color: colors.muted, marginLeft: spacing.sm },
  rowFoot: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: spacing.sm },
  preview: { flex: 1, color: colors.muted, fontSize: font.sizes.sm },
  previewBold: { color: colors.onSurface, fontWeight: "500" },
  badge: {
    minWidth: 20,
    height: 20,
    borderRadius: 10,
    backgroundColor: colors.brandPrimary,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 6,
  },
  badgeTxt: { color: colors.onBrandPrimary, fontSize: 11, fontWeight: "700" },
});
