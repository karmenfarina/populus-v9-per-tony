import React, { useCallback, useState } from "react";
import {
  View,
  Text,
  Pressable,
  FlatList,
  Image,
  StyleSheet,
  TextInput,
  ActivityIndicator,
  Switch,
  Alert,
  Modal,
  Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { api } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { useSmartBack } from "@/src/utils/useSmartBack";
import { colors, spacing, font } from "@/src/theme";

type Member = {
  user_id: string;
  nickname: string;
  primary_photo_id?: string | null;
  photo_data?: string | null;
  display_name?: string | null;
  is_me?: boolean;
  in_my_circle?: boolean;
};

/**
 * Cerchia del Gossip — friend-circle browser.
 *
 * Row actions are intentionally minimal:
 *   • Tapping the row opens that user's public profile (works for
 *     the viewer's own row as well).
 *   • When browsing someone else's circle, each row that is NOT the
 *     viewer themselves also shows an "AGGIUNGI/NELLA CERCHIA" button.
 *   • When browsing your OWN circle, each row shows a small × button
 *     to remove that member.
 *
 * Server-side ordering:
 *   1. The viewer themselves (if they belong to the circle) first.
 *   2. Members that are ALSO in the viewer's own circle, most-recent
 *      interaction first.
 *   3. Everyone else, most-recent interaction first.
 */
export default function CircleScreen() {
  const { userId } = useLocalSearchParams<{ userId: string }>();
  const router = useRouter();
  const { user } = useAuth();

  const [members, setMembers] = useState<Member[]>([]);
  const [count, setCount] = useState(0);
  const [max, setMax] = useState(45);
  const [isOwner, setIsOwner] = useState(false);
  const [privateCircle, setPrivateCircle] = useState(false);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  // Members currently mid-flight for an add/remove call. Prevents double
  // taps and lets us render a subtle disabled state on the button.
  const [pending, setPending] = useState<Record<string, boolean>>({});

  const goBack = useSmartBack(userId === user?.user_id ? "/profile" : `/user/${userId}`);

  const load = useCallback(async () => {
    if (!userId) return;
    setLoading(true);
    try {
      const r = await api.circleGet(userId, q.trim() || undefined);
      setCount(r.count || 0);
      setMax(r.max || 45);
      setIsOwner(!!r.is_owner);
      setPrivateCircle(!!r.private);
      setMembers(r.members || []);
    } catch { /* silent */ }
    finally { setLoading(false); }
  }, [userId, q]);

  // Load whenever the screen gains focus. This also handles the initial
  // mount and, crucially, returning from a profile where the user tapped
  // Add/Remove from Circle so the list, count and privacy flag stay in
  // sync in real time.
  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const togglePrivacy = async (v: boolean) => {
    // Optimistic switch — flip local then persist.
    setPrivateCircle(v);
    try {
      await api.circleSetPrivacy(v);
    } catch {
      setPrivateCircle(!v); // rollback
      Alert.alert("Errore", "Impossibile aggiornare la privacy.");
    }
  };

  // Confirmation dialog state — used to remove someone from MY circle.
  // A custom in-app modal is preferred over `window.confirm()` on web
  // because the browser dialog surfaces the site URL in its header,
  // which leaks the app hostname and looks unpolished.
  const [confirmRemove, setConfirmRemove] = useState<Member | null>(null);

  // Owner-only: hard removal from MY circle (used only when viewing my
  // own circle). Removes locally on success and syncs the count chip.
  const runRemove = async (m: Member) => {
    setPending((p) => ({ ...p, [m.user_id]: true }));
    try {
      await api.circleRemove(m.user_id);
      setMembers((prev) => prev.filter((x) => x.user_id !== m.user_id));
      setCount((c) => Math.max(0, c - 1));
    } catch (e: any) {
      Alert.alert("Errore", e?.detail || "Impossibile rimuovere");
    } finally {
      setPending((p) => { const { [m.user_id]: _, ...rest } = p; return rest; });
    }
  };

  const removeMember = (m: Member) => {
    // Uniform in-app modal on both web and native so the confirmation
    // is minimal and chrome-free (no site URL, no OS-alert header).
    setConfirmRemove(m);
  };

  // Non-owner view: tap "AGGIUNGI/NELLA CERCHIA" to toggle whether the
  // row's user belongs to MY circle. Updates the row flag optimistically
  // and rolls back on failure.
  const toggleInMyCircle = async (m: Member) => {
    if (m.is_me || pending[m.user_id]) return;
    const wasIn = !!m.in_my_circle;
    setPending((p) => ({ ...p, [m.user_id]: true }));
    setMembers((prev) => prev.map((x) => x.user_id === m.user_id ? { ...x, in_my_circle: !wasIn } : x));
    try {
      if (wasIn) await api.circleRemove(m.user_id);
      else await api.circleAdd(m.user_id);
    } catch (e: any) {
      // Rollback UI on failure.
      setMembers((prev) => prev.map((x) => x.user_id === m.user_id ? { ...x, in_my_circle: wasIn } : x));
      Alert.alert(wasIn ? "Errore" : "Impossibile aggiungere", e?.detail || "Riprova");
    } finally {
      setPending((p) => { const { [m.user_id]: _, ...rest } = p; return rest; });
    }
  };

  const renderMember = ({ item }: { item: Member }) => {
    const showAddBtn = !isOwner && !item.is_me;
    const busy = !!pending[item.user_id];
    return (
      <View style={styles.row} testID={`circle-row-${item.user_id}`}>
        <Pressable
          onPress={() =>
            router.push({
              pathname: "/user/[id]",
              params: { id: item.user_id, from: `/circle/${userId}` },
            })
          }
          style={styles.rowLeft}
          testID={`circle-open-profile-${item.user_id}`}
        >
          {item.photo_data ? (
            <Image source={{ uri: item.photo_data }} style={styles.avatar} />
          ) : (
            <View style={[styles.avatar, styles.avatarFallback]}>
              <Ionicons name="person" size={22} color={colors.muted} />
            </View>
          )}
          <View style={{ flex: 1 }}>
            <Text style={styles.nick}>
              @{item.nickname}
              {item.is_me ? <Text style={styles.meTag}>  · TU</Text> : null}
            </Text>
            {item.display_name ? <Text style={styles.dispname}>{item.display_name}</Text> : null}
          </View>
        </Pressable>
        {isOwner ? (
          <Pressable
            onPress={() => removeMember(item)}
            disabled={busy}
            style={[styles.iconBtn, busy ? styles.iconBtnBusy : null]}
            testID={`circle-remove-${item.user_id}`}
            hitSlop={6}
          >
            <Ionicons name="close" size={20} color={colors.error} />
          </Pressable>
        ) : null}
        {showAddBtn ? (
          <Pressable
            onPress={() => toggleInMyCircle(item)}
            disabled={busy}
            style={[
              styles.addBtn,
              item.in_my_circle ? styles.addBtnOn : null,
              busy ? styles.addBtnBusy : null,
            ]}
            testID={`circle-add-${item.user_id}`}
            hitSlop={6}
          >
            <Ionicons
              name={item.in_my_circle ? "checkmark-circle" : "person-add"}
              size={14}
              color={item.in_my_circle ? colors.onBrandSecondary : colors.onBrandPrimary}
            />
            <Text style={[styles.addBtnTxt, item.in_my_circle ? styles.addBtnTxtOn : null]}>
              {item.in_my_circle ? "NELLA CERCHIA" : "AGGIUNGI"}
            </Text>
          </Pressable>
        ) : null}
      </View>
    );
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Pressable onPress={goBack} style={styles.backBtn} testID="circle-back">
          <Ionicons name="chevron-back" size={22} color={colors.onSurfaceInverse} />
        </Pressable>
        <Text style={styles.title}>CERCHIA</Text>
        <View style={styles.backBtn} />
      </View>

      <View style={styles.subhead}>
        <Text style={styles.subheadTxt}>
          {count} / {max}
        </Text>
        {isOwner ? (
          <View style={styles.privRow}>
            <Text style={styles.privLabel}>Cerchia privata</Text>
            <Switch
              value={privateCircle}
              onValueChange={togglePrivacy}
              testID="circle-privacy-switch"
            />
          </View>
        ) : null}
      </View>

      <View style={styles.searchWrap}>
        <Ionicons name="search" size={16} color={colors.muted} />
        <TextInput
          value={q}
          onChangeText={setQ}
          placeholder="Cerca amico..."
          placeholderTextColor={colors.muted}
          style={styles.searchInput}
          autoCapitalize="none"
          testID="circle-search-input"
        />
      </View>

      {privateCircle && !isOwner ? (
        <View style={styles.emptyBox} testID="circle-private-notice">
          <Ionicons name="lock-closed" size={38} color={colors.muted} />
          <Text style={styles.emptyTitle}>Cerchia privata</Text>
          <Text style={styles.emptySub}>
            Questo utente ha nascosto la propria cerchia agli altri.
          </Text>
        </View>
      ) : loading ? (
        <View style={styles.emptyBox}><ActivityIndicator color={colors.brandPrimary} /></View>
      ) : members.length === 0 ? (
        <View style={styles.emptyBox} testID="circle-empty">
          <Ionicons name="people-outline" size={38} color={colors.muted} />
          <Text style={styles.emptyTitle}>
            {q ? "Nessun risultato" : (isOwner ? "La tua cerchia è vuota" : "Nessuno nella cerchia")}
          </Text>
          {isOwner && !q ? (
            <Text style={styles.emptySub}>
              Aggiungi amici dai loro profili per farli comparire qui.
            </Text>
          ) : null}
        </View>
      ) : (
        <FlatList
          data={members}
          keyExtractor={(m) => m.user_id}
          renderItem={renderMember}
          contentContainerStyle={{ paddingBottom: spacing.xl }}
          testID="circle-list"
        />
      )}

      {/* Minimal in-app confirmation modal — no browser chrome, no
          site URL header, just the question and two actions. */}
      <Modal
        transparent
        animationType="fade"
        visible={!!confirmRemove}
        onRequestClose={() => setConfirmRemove(null)}
        testID="circle-remove-confirm-modal"
      >
        <Pressable
          style={styles.confirmBackdrop}
          onPress={() => setConfirmRemove(null)}
        >
          <Pressable style={styles.confirmCard} onPress={() => { /* swallow */ }}>
            <Text style={styles.confirmTitle}>Rimuovi dalla cerchia</Text>
            <Text style={styles.confirmBody}>
              Rimuovere <Text style={styles.confirmNick}>@{confirmRemove?.nickname}</Text> dalla tua cerchia?
            </Text>
            <View style={styles.confirmBtnRow}>
              <Pressable
                onPress={() => setConfirmRemove(null)}
                style={[styles.confirmBtn, styles.confirmBtnGhost]}
                testID="circle-remove-cancel"
              >
                <Text style={styles.confirmBtnGhostTxt}>ANNULLA</Text>
              </Pressable>
              <Pressable
                onPress={() => {
                  const m = confirmRemove;
                  setConfirmRemove(null);
                  if (m) runRemove(m);
                }}
                style={[styles.confirmBtn, styles.confirmBtnDanger]}
                testID="circle-remove-confirm"
              >
                <Text style={styles.confirmBtnDangerTxt}>RIMUOVI</Text>
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
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    backgroundColor: colors.brandPrimary,
    paddingHorizontal: spacing.md, paddingVertical: spacing.md,
    borderBottomWidth: 2, borderColor: colors.border,
  },
  backBtn: { width: 32, height: 32, alignItems: "center", justifyContent: "center" },
  title: { color: colors.onBrandPrimary, fontSize: font.sizes.xl, letterSpacing: 3, fontWeight: "500" },
  subhead: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: spacing.lg, paddingVertical: spacing.md,
    borderBottomWidth: 1, borderColor: colors.border,
  },
  subheadTxt: { color: colors.onSurface, fontSize: font.sizes.base, fontWeight: "600" },
  privRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  privLabel: { color: colors.onSurface, fontSize: font.sizes.sm },
  searchWrap: {
    flexDirection: "row", alignItems: "center", gap: spacing.sm,
    borderWidth: 1, borderColor: colors.border,
    marginHorizontal: spacing.md, marginTop: spacing.md,
    paddingHorizontal: spacing.md, paddingVertical: 8,
    backgroundColor: colors.surfaceSecondary,
  },
  searchInput: { flex: 1, color: colors.onSurface, fontSize: font.sizes.base, padding: 0 },
  row: {
    flexDirection: "row", alignItems: "center", gap: spacing.sm,
    paddingHorizontal: spacing.md, paddingVertical: spacing.sm,
    borderBottomWidth: 1, borderColor: colors.border,
  },
  rowLeft: { flexDirection: "row", alignItems: "center", flex: 1, gap: spacing.sm },
  avatar: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.surfaceSecondary },
  avatarFallback: { alignItems: "center", justifyContent: "center" },
  nick: { color: colors.onSurface, fontSize: font.sizes.base, fontWeight: "600" },
  meTag: { color: colors.brandPrimary, fontSize: font.sizes.xs, fontWeight: "700", letterSpacing: 1 },
  dispname: { color: colors.muted, fontSize: font.sizes.sm, marginTop: 2 },
  iconBtn: { padding: 8 },
  iconBtnBusy: { opacity: 0.4 },
  addBtn: {
    flexDirection: "row", alignItems: "center", gap: 4,
    backgroundColor: colors.brandPrimary,
    paddingHorizontal: 10, paddingVertical: 6,
    borderRadius: 999,
  },
  addBtnOn: { backgroundColor: colors.brandSecondary },
  addBtnBusy: { opacity: 0.55 },
  addBtnTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.xs, fontWeight: "700", letterSpacing: 0.5 },
  addBtnTxtOn: { color: colors.onBrandSecondary },
  emptyBox: { alignItems: "center", justifyContent: "center", padding: spacing.xl, gap: spacing.sm },
  emptyTitle: { color: colors.onSurface, fontSize: font.sizes.lg, fontWeight: "600", marginTop: spacing.sm },
  emptySub: { color: colors.muted, fontSize: font.sizes.sm, textAlign: "center" },
  // In-app confirmation modal (used for the "remove from circle" action).
  confirmBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.55)",
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: spacing.xl,
  },
  confirmCard: {
    width: "100%",
    maxWidth: 340,
    backgroundColor: colors.surface,
    borderWidth: 2,
    borderColor: colors.border,
    padding: spacing.lg,
    gap: spacing.md,
  },
  confirmTitle: {
    color: colors.onSurface,
    fontSize: font.sizes.lg,
    fontWeight: "700",
    letterSpacing: 1,
  },
  confirmBody: {
    color: colors.onSurface,
    fontSize: font.sizes.base,
    lineHeight: 20,
  },
  confirmNick: {
    fontWeight: "700",
    color: colors.brandPrimary,
  },
  confirmBtnRow: {
    flexDirection: "row",
    gap: spacing.sm,
    marginTop: spacing.sm,
    justifyContent: "flex-end",
  },
  confirmBtn: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    minWidth: 96,
    alignItems: "center",
  },
  confirmBtnGhost: {
    borderWidth: 2,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  confirmBtnGhostTxt: {
    color: colors.onSurface,
    fontWeight: "700",
    fontSize: font.sizes.sm,
    letterSpacing: 1,
  },
  confirmBtnDanger: {
    backgroundColor: colors.error,
  },
  confirmBtnDangerTxt: {
    color: "#FFFFFF",
    fontWeight: "700",
    fontSize: font.sizes.sm,
    letterSpacing: 1,
  },
});
