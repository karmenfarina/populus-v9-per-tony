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
  Modal,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useFocusEffect, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { api, Conversation } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { useMessaging } from "@/src/messaging/MessagingContext";
import { colors, spacing, font, radius } from "@/src/theme";

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
  // Long-press → confirm-delete modal. Holds the conversation the user
  // is about to clear so we can show the target nickname and pass the
  // right id to the API. `null` means the modal is closed.
  const [confirmDelete, setConfirmDelete] = useState<Conversation | null>(null);
  const [deleting, setDeleting] = useState(false);

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
      let cancelled = false;
      (async () => {
        try {
          const r = await api.conversations();
          if (cancelled) return;
          const list = r.conversations || [];
          setConvs(list);
          // Self-heal: if the visible list is empty but the tab badge still
          // reports unread messages, those are orphans (deleted/blocked
          // conversations left messages behind). Sweep them so the badge
          // clears the moment the user reaches the messages screen.
          if (list.length === 0) {
            try {
              await api.messagesMarkAllRead();
            } catch { /* silent */ }
          }
        } catch { /* silent */ }
        if (!cancelled) refreshBadge();
      })();
      return () => { cancelled = true; };
    }, [user, refreshBadge]),
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

  const deleteChat = useCallback(async () => {
    if (!confirmDelete || deleting) return;
    setDeleting(true);
    const target = confirmDelete.other_user.user_id;
    // Optimistic: remove the row immediately so the UI feels snappy.
    // If the request fails we re-load to restore the true state.
    setConvs((prev) => prev.filter((c) => c.other_user.user_id !== target));
    try {
      await api.clearConversation(target);
      await refreshBadge();
    } catch {
      // Restore on failure.
      await load();
    } finally {
      setDeleting(false);
      setConfirmDelete(null);
    }
  }, [confirmDelete, deleting, refreshBadge, load]);

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
            <View style={{ alignItems: "center", gap: spacing.md }}>
              <Ionicons name="chatbubble-ellipses-outline" size={64} color={colors.muted} />
              <Text style={styles.emptyTitle}>NESSUNA CONVERSAZIONE</Text>
              <Text style={styles.emptyBody}>
                Vai sul profilo di un utente e tocca &quot;Invia messaggio&quot; per iniziare una conversazione.
              </Text>
            </View>
          }
          renderItem={({ item }) => (
            <Pressable
              onPress={() => router.push({ pathname: "/messages/[userId]", params: { userId: item.other_user.user_id, from: "/messages" } })}
              onLongPress={() => setConfirmDelete(item)}
              delayLongPress={350}
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

      {/* Long-press delete confirmation. Uses the same styling as other
          destructive confirmations in the app (see /circle/[userId].tsx). */}
      <Modal
        visible={confirmDelete !== null}
        transparent
        animationType="fade"
        onRequestClose={() => setConfirmDelete(null)}
      >
        <Pressable style={styles.confirmBackdrop} onPress={() => !deleting && setConfirmDelete(null)}>
          <Pressable style={styles.confirmCard} onPress={(e) => e.stopPropagation()}>
            <View style={styles.confirmIconWrap}>
              <Ionicons name="trash-outline" size={26} color={colors.error} />
            </View>
            <Text style={styles.confirmTitle}>Elimina chat</Text>
            <Text style={styles.confirmBody}>
              Tutti i messaggi con <Text style={styles.confirmNick}>@{confirmDelete?.other_user.nickname || "questo utente"}</Text> spariranno dalla tua lista. La chat resterà visibile all&apos;altro utente. Se ricevi un nuovo messaggio, la chat ricomparirà.
            </Text>
            <View style={styles.confirmBtnRow}>
              <Pressable
                onPress={() => setConfirmDelete(null)}
                disabled={deleting}
                style={[styles.confirmBtn, styles.confirmBtnGhost]}
                testID="delete-chat-cancel"
              >
                <Text style={styles.confirmBtnGhostTxt}>ANNULLA</Text>
              </Pressable>
              <Pressable
                onPress={deleteChat}
                disabled={deleting}
                style={[styles.confirmBtn, styles.confirmBtnDanger]}
                testID="delete-chat-confirm"
              >
                {deleting ? (
                  <ActivityIndicator color="#fff" size="small" />
                ) : (
                  <Text style={styles.confirmBtnDangerTxt}>ELIMINA</Text>
                )}
              </Pressable>
            </View>
          </Pressable>
        </Pressable>
      </Modal>
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
    paddingVertical: spacing.lg,
  },
  headerTitle: { color: colors.onSurface, fontSize: font.sizes.xxxl, letterSpacing: 1.5, fontWeight: "800" },
  headerStatus: { flexDirection: "row", alignItems: "center", gap: 6 },
  headerStatusTxt: { color: colors.muted, fontSize: font.sizes.xs, letterSpacing: 0.5, fontWeight: "600" },
  dot: { width: 8, height: 8, borderRadius: 4 },
  dotOn: { backgroundColor: colors.success },
  dotOff: { backgroundColor: colors.muted },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  emptyBox: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xxl, gap: spacing.md },
  emptyTitle: { fontSize: font.sizes.lg, letterSpacing: 1.5, color: colors.onSurface, fontWeight: "800" },
  emptyBody: { fontSize: font.sizes.sm, color: colors.muted, textAlign: "center", lineHeight: 20 },
  row: {
    flexDirection: "row",
    marginHorizontal: spacing.md,
    marginTop: spacing.sm,
    padding: spacing.md,
    gap: spacing.md,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 12,
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

  // ==================================================================
  // Confirm delete modal — matches /circle/[userId].tsx so all
  // destructive confirmations across the app look identical.
  // ==================================================================
  confirmBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.65)",
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: spacing.xl,
  },
  confirmCard: {
    width: "100%",
    maxWidth: 340,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.lg,
    padding: spacing.xl,
    gap: spacing.md,
    alignItems: "center",
  },
  confirmIconWrap: {
    width: 56, height: 56,
    borderRadius: 28,
    borderWidth: 1.5, borderColor: colors.error,
    alignItems: "center", justifyContent: "center",
    marginBottom: spacing.xs,
  },
  confirmTitle: {
    color: colors.onSurface,
    fontSize: font.sizes.xl,
    fontWeight: "800",
    letterSpacing: 0.5,
    textAlign: "center",
  },
  confirmBody: {
    color: colors.muted,
    fontSize: font.sizes.base,
    lineHeight: 22,
    textAlign: "center",
  },
  confirmNick: {
    fontWeight: "800",
    color: colors.brandPrimary,
  },
  confirmBtnRow: {
    flexDirection: "row",
    gap: spacing.sm,
    marginTop: spacing.sm,
    width: "100%",
  },
  confirmBtn: {
    flex: 1,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    alignItems: "center",
    borderRadius: radius.pill,
  },
  confirmBtnGhost: {
    borderWidth: 1.5,
    borderColor: colors.borderStrong,
    backgroundColor: "transparent",
  },
  confirmBtnGhostTxt: {
    color: colors.onSurface,
    fontWeight: "800",
    fontSize: font.sizes.sm,
    letterSpacing: 1,
  },
  confirmBtnDanger: {
    backgroundColor: colors.error,
  },
  confirmBtnDangerTxt: {
    color: "#FFFFFF",
    fontWeight: "800",
    fontSize: font.sizes.sm,
    letterSpacing: 1,
  },
});
