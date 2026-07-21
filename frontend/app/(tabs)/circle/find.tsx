import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  TextInput,
  FlatList,
  Image,
  ActivityIndicator,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { api } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { useSmartBack } from "@/src/utils/useSmartBack";
import { colors, font, spacing } from "@/src/theme";

/**
 * Dedicated "find friends" screen. Reachable via the `+` button in the
 * top-right of the owner's own Cerchia. Debounced live search against
 * the `/users/search` backend endpoint; each result exposes an
 * `AGGIUNGI` / `NELLA CERCHIA` toggle. Tapping a row opens the user's
 * public profile.
 *
 * Users already in the viewer's circle come back pre-toggled so the
 * button flips to the "remove" affordance immediately.
 */

type Row = {
  user_id: string;
  nickname: string;
  display_name?: string | null;
  photo_data?: string | null;
  in_my_circle?: boolean;
  is_me?: boolean;
};

const DEBOUNCE_MS = 260;

export default function CircleFindScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const goBack = useSmartBack(user ? `/circle/${user.user_id}` : "/profile");

  const [q, setQ] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [pending, setPending] = useState<Record<string, boolean>>({});
  // Circle members that we know MY user already has — used to render the
  // toggle button in the correct state on first render.
  const [myCircleIds, setMyCircleIds] = useState<Set<string>>(new Set());

  // Bootstrap my own circle so results can be pre-annotated with the
  // in_my_circle flag without waiting for an extra roundtrip per row.
  useEffect(() => {
    if (!user?.user_id) return;
    let cancelled = false;
    (async () => {
      try {
        const c: any = await api.circleGet(user.user_id);
        if (!cancelled) {
          setMyCircleIds(new Set((c?.members || []).map((m: any) => m.user_id)));
        }
      } catch { /* silent */ }
    })();
    return () => { cancelled = true; };
  }, [user?.user_id]);

  // Debounced search. We do NOT trigger a request for empty queries —
  // this keeps the empty state clean and avoids listing "recently
  // active users" that wouldn't have privacy consent anyway.
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    const trimmed = q.trim();
    if (!trimmed) { setRows([]); setLoading(false); return; }
    setLoading(true);
    timer.current = setTimeout(async () => {
      try {
        const r: any = await api.searchUsers(trimmed, 30);
        const list: Row[] = (r?.users || []).map((u: any) => ({
          user_id: u.user_id,
          nickname: u.nickname,
          display_name: u.display_name,
          photo_data: u.photo_data ?? null,
          is_me: u.user_id === user?.user_id,
          in_my_circle: myCircleIds.has(u.user_id),
        }));
        setRows(list);
      } catch {
        setRows([]);
      } finally {
        setLoading(false);
      }
    }, DEBOUNCE_MS);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [q, user?.user_id, myCircleIds]);

  const toggleAdd = useCallback(async (row: Row) => {
    if (row.is_me || pending[row.user_id]) return;
    const wasIn = !!row.in_my_circle;
    setPending((p) => ({ ...p, [row.user_id]: true }));
    // Optimistic swap on the row + the master set so a subsequent search
    // reuses the correct state.
    setRows((prev) => prev.map((x) => x.user_id === row.user_id ? { ...x, in_my_circle: !wasIn } : x));
    setMyCircleIds((prev) => {
      const next = new Set(prev);
      if (wasIn) next.delete(row.user_id); else next.add(row.user_id);
      return next;
    });
    try {
      if (wasIn) await api.circleRemove(row.user_id);
      else await api.circleAdd(row.user_id);
    } catch {
      // Rollback both stores on failure.
      setRows((prev) => prev.map((x) => x.user_id === row.user_id ? { ...x, in_my_circle: wasIn } : x));
      setMyCircleIds((prev) => {
        const next = new Set(prev);
        if (wasIn) next.add(row.user_id); else next.delete(row.user_id);
        return next;
      });
    } finally {
      setPending((p) => { const { [row.user_id]: _, ...rest } = p; return rest; });
    }
  }, [pending]);

  const openProfile = (uid: string) => {
    router.push({ pathname: "/user/[id]", params: { id: uid, from: "/circle/find" } });
  };

  const renderRow = ({ item }: { item: Row }) => {
    const busy = !!pending[item.user_id];
    return (
      <View style={styles.row} testID={`find-row-${item.user_id}`}>
        <Pressable
          onPress={() => openProfile(item.user_id)}
          style={styles.rowLeft}
          testID={`find-open-profile-${item.user_id}`}
        >
          {item.photo_data ? (
            <Image source={{ uri: item.photo_data }} style={styles.avatar} />
          ) : (
            <View style={[styles.avatar, styles.avatarFallback]}>
              <Ionicons name="person" size={22} color={colors.muted} />
            </View>
          )}
          <View style={{ flex: 1 }}>
            <Text style={styles.nick} numberOfLines={1}>
              @{(item.nickname || "").replace(/\s+/g, "")}
              {item.is_me ? <Text style={styles.meTag}>  · TU</Text> : null}
            </Text>
            {item.display_name ? (
              <Text style={styles.dispname} numberOfLines={1}>{item.display_name}</Text>
            ) : null}
          </View>
        </Pressable>
        {!item.is_me ? (
          <Pressable
            onPress={() => toggleAdd(item)}
            disabled={busy}
            style={[
              styles.addBtn,
              item.in_my_circle ? styles.addBtnOn : null,
              busy ? styles.addBtnBusy : null,
            ]}
            testID={`find-add-${item.user_id}`}
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

  const emptyState = useMemo(() => {
    if (loading) return null;
    if (!q.trim()) {
      return (
        <View style={styles.empty} testID="find-hint">
          <Ionicons name="search" size={40} color={colors.muted} />
          <Text style={styles.emptyTitle}>Cerca nuovi amici</Text>
          <Text style={styles.emptyHint}>
            {"Digita il nickname o parte di esso (es. \"mario\") per trovare persone da aggiungere alla tua Cerchia del Gossip."}
          </Text>
        </View>
      );
    }
    if (rows.length === 0) {
      return (
        <View style={styles.empty} testID="find-empty">
          <Ionicons name="alert-circle-outline" size={40} color={colors.muted} />
          <Text style={styles.emptyTitle}>Nessun risultato</Text>
          <Text style={styles.emptyHint}>
            {"Nessun utente corrisponde a \"" + q.trim() + "\". Prova con un'altra parola."}
          </Text>
        </View>
      );
    }
    return null;
  }, [loading, q, rows.length]);

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <View style={styles.header}>
        <Pressable onPress={goBack} style={styles.headerBtn} testID="find-back" hitSlop={8}>
          <Ionicons name="chevron-back" size={24} color={colors.onSurface} />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>CERCA AMICI</Text>
          <Text style={styles.subtitle}>Aggiungi persone alla tua Cerchia del Gossip</Text>
        </View>
      </View>

      <View style={styles.searchWrap}>
        <Ionicons name="search" size={18} color={colors.muted} />
        <TextInput
          value={q}
          onChangeText={setQ}
          placeholder="Cerca per nickname…"
          placeholderTextColor={colors.muted}
          style={styles.searchInput}
          autoCapitalize="none"
          autoCorrect={false}
          returnKeyType="search"
          testID="find-search-input"
        />
        {q.length > 0 ? (
          <Pressable onPress={() => setQ("")} testID="find-clear" hitSlop={8}>
            <Ionicons name="close-circle" size={18} color={colors.muted} />
          </Pressable>
        ) : null}
      </View>

      {loading ? (
        <View style={styles.loadingWrap}>
          <ActivityIndicator color={colors.brandPrimary} />
        </View>
      ) : null}

      <FlatList
        data={rows}
        keyExtractor={(x) => x.user_id}
        renderItem={renderRow}
        contentContainerStyle={{ paddingBottom: spacing.xl }}
        ListEmptyComponent={emptyState}
        testID="find-list"
        keyboardShouldPersistTaps="handled"
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    gap: spacing.sm,
    borderBottomWidth: 2,
    borderBottomColor: colors.border,
  },
  headerBtn: { padding: spacing.xs },
  title: { color: colors.onSurface, fontSize: font.sizes.lg, fontWeight: "700", letterSpacing: 2 },
  subtitle: { color: colors.muted, fontSize: font.sizes.xs, marginTop: 2 },
  searchWrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    marginHorizontal: spacing.lg,
    marginTop: spacing.md,
    paddingHorizontal: spacing.md,
    paddingVertical: 10,
    borderWidth: 2,
    borderColor: colors.border,
    backgroundColor: colors.surfaceSecondary,
  },
  searchInput: {
    flex: 1,
    color: colors.onSurface,
    fontSize: font.sizes.base,
    padding: 0,
  },
  loadingWrap: { paddingVertical: spacing.md, alignItems: "center" },
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    gap: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  rowLeft: { flexDirection: "row", alignItems: "center", flex: 1, gap: spacing.sm },
  avatar: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.surfaceSecondary },
  avatarFallback: { alignItems: "center", justifyContent: "center" },
  nick: { color: colors.onSurface, fontSize: font.sizes.base, fontWeight: "600" },
  meTag: { color: colors.brandPrimary, fontSize: font.sizes.xs, fontWeight: "700", letterSpacing: 1 },
  dispname: { color: colors.muted, fontSize: font.sizes.sm, marginTop: 2 },
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
  empty: {
    alignItems: "center",
    padding: spacing.xl,
    gap: spacing.sm,
    marginTop: spacing.xl,
  },
  emptyTitle: { color: colors.onSurface, fontSize: font.sizes.lg, fontWeight: "600" },
  emptyHint: { color: colors.muted, fontSize: font.sizes.sm, textAlign: "center", lineHeight: 20 },
});
