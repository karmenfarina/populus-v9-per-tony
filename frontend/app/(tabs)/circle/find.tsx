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
import { useFocusEffect, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { api } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { navStack } from "@/src/utils/navStack";
import { cachedGet, invalidateCache } from "@/src/utils/clientCache";
import { colors, font, spacing, radius } from "@/src/theme";

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
  reasons?: string[];
};

const REASON_LABEL: Record<string, string> = {
  chat: "Chat",
  commenti: "Ha risposto ai tuoi commenti",
};

const DEBOUNCE_MS = 260;

export default function CircleFindScreen() {
  const router = useRouter();
  const { user } = useAuth();
  // Anonymous users are not allowed to use user search — the backend
  // returns an empty list for them anyway and they can't be added to
  // any circle. Rather than render a broken/empty state, show a clear
  // notice and a shortcut back to the profile.
  const isAnon = user?.is_anonymous === true || (user as any)?.auth_provider === 'anonymous';
  // Custom back: go DIRECTLY to the profile (not to the Cerchia) so
  // the user doesn't have to walk through the intermediate Cerchia
  // screen every time they close the search sheet. The "+" button
  // that opens this screen lives on the Cerchia header, but from a
  // UX perspective this feels like a modal — closing it should return
  // to the tab root.
  const goBack = useCallback(() => {
    // Also purge our custom nav-stack entry for this screen so a
    // subsequent Profile → Cerchia navigation doesn't accidentally
    // return here via chain-back.
    try {
      navStack.popAndPeek();
    } catch { /* noop */ }
    router.replace("/profile");
  }, [router]);

  const [q, setQ] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [pending, setPending] = useState<Record<string, boolean>>({});
  // Circle members that we know MY user already has — used to render the
  // toggle button in the correct state on first render.
  const [myCircleIds, setMyCircleIds] = useState<Set<string>>(new Set());
  // Curated suggestions surfaced when the user has NOT typed anything.
  // Reasons per row (chat / friends-of-friends / co-commenters) come
  // straight from the backend so the client stays dumb.
  const [suggested, setSuggested] = useState<Row[]>([]);
  const [loadingSug, setLoadingSug] = useState(true);

  // Bootstrap my own circle AND fetch curated suggestions in parallel so
  // the empty state has actionable rows immediately on mount.
  //
  // Wrapped in `useFocusEffect` (not `useEffect`) so returning to this
  // tab after adding/removing someone elsewhere always refreshes the
  // AGGIUNGI/NELLA CERCHIA button state — without this, stale rows
  // linger with the wrong toggle label.
  useFocusEffect(
    useCallback(() => {
      if (!user?.user_id) return;
      // Skip network work entirely for anonymous accounts — the whole
      // feature is disabled below anyway, and the backend would just
      // return empty arrays.
      if (isAnon) return;
      let cancelled = false;
      (async () => {
        setLoadingSug(true);
        try {
          const [c, s]: any[] = await Promise.all([
            api.circleGet(user.user_id),
            // Cache 60s: suggerimenti Cerchia sono un ranking pesante
            // lato server. Il refetch avviene comunque a ogni focus
            // effettivo del tab (useFocusEffect) per l'invalidazione
            // hard su add/remove.
            cachedGet('circle:suggestions:15', 60_000, () => api.circleSuggestions(15)),
          ]);
          if (cancelled) return;
          const ids: Set<string> = new Set((c?.members || []).map((m: any) => m.user_id));
          setMyCircleIds(ids);
          setSuggested((s?.users || []).map((u: any) => ({
            user_id: u.user_id,
            nickname: u.nickname,
            display_name: u.display_name,
            photo_data: u.photo_data ?? null,
            is_me: u.user_id === user.user_id,
            in_my_circle: ids.has(u.user_id),
            reasons: u.reasons || [],
          })));
          // Also refresh in_my_circle flag on any currently-visible search
          // results so a returning user sees the correct button label
          // even if they don't touch the search field.
          setRows((prev) => prev.map((x) => ({ ...x, in_my_circle: ids.has(x.user_id) })));
        } catch { /* silent */ }
        finally { if (!cancelled) setLoadingSug(false); }
      })();
      return () => { cancelled = true; };
    }, [user?.user_id, isAnon]),
  );

  // Keep a live ref of myCircleIds so the debounced search reads the
  // latest membership WITHOUT registering it as an effect dependency.
  // Without this, every add/remove flip would re-fire the network call.
  const myCircleIdsRef = useRef<Set<string>>(new Set());
  useEffect(() => { myCircleIdsRef.current = myCircleIds; }, [myCircleIds]);

  // Debounced search. We do NOT trigger a request for empty queries —
  // this keeps the empty state clean and avoids listing "recently
  // active users" that wouldn't have privacy consent anyway.
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    const trimmed = q.trim();
    if (!trimmed || isAnon) { setRows([]); setLoading(false); return; }
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
          in_my_circle: myCircleIdsRef.current.has(u.user_id),
        }));
        setRows(list);
      } catch {
        setRows([]);
      } finally {
        setLoading(false);
      }
    }, DEBOUNCE_MS);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [q, user?.user_id, isAnon]);

  const toggleAdd = useCallback(async (row: Row) => {
    if (row.is_me || pending[row.user_id]) return;
    const wasIn = !!row.in_my_circle;
    setPending((p) => ({ ...p, [row.user_id]: true }));
    // Optimistic swap on BOTH the current search-result rows AND the
    // suggestions list, so the same person can't appear twice with
    // out-of-sync buttons.
    setRows((prev) => prev.map((x) => x.user_id === row.user_id ? { ...x, in_my_circle: !wasIn } : x));
    setSuggested((prev) => prev.map((x) => x.user_id === row.user_id ? { ...x, in_my_circle: !wasIn } : x));
    setMyCircleIds((prev) => {
      const next = new Set(prev);
      if (wasIn) next.delete(row.user_id); else next.add(row.user_id);
      return next;
    });
    try {
      if (wasIn) await api.circleRemove(row.user_id);
      else await api.circleAdd(row.user_id);
      // Ranking suggerimenti dipende dai membri Cerchia: invalida la
      // cache così la prossima aperture non ripropone la persona
      // appena aggiunta/rimossa.
      invalidateCache('circle:suggestions');
    } catch {
      // Rollback all three stores on failure.
      setRows((prev) => prev.map((x) => x.user_id === row.user_id ? { ...x, in_my_circle: wasIn } : x));
      setSuggested((prev) => prev.map((x) => x.user_id === row.user_id ? { ...x, in_my_circle: wasIn } : x));
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
            {item.reasons && item.reasons.length > 0 ? (
              <View style={styles.reasonRow}>
                {item.reasons.map((r) => (
                  <View key={r} style={styles.reasonChip}>
                    <Text style={styles.reasonTxt}>{REASON_LABEL[r] || r}</Text>
                  </View>
                ))}
              </View>
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
      // When there's nothing typed, prefer the curated Suggeriti list.
      // If suggestions haven't loaded yet OR came back empty, fall back
      // to the generic hint.
      if (loadingSug) {
        return (
          <View style={styles.empty} testID="find-loading-suggestions">
            <ActivityIndicator color={colors.brandPrimary} />
          </View>
        );
      }
      if (suggested.length > 0) return null; // will be handled by the list header
      return (
        <View style={styles.empty} testID="find-hint">
          <Ionicons name="search-outline" size={40} color={colors.muted} />
          <Text style={styles.emptyTitle}>Cerca nuovi amici</Text>
          <Text style={styles.emptyHint}>
            {"Digita il nickname o il nome (es. \"mario\") per trovare persone da aggiungere alla tua Cerchia del Gossip."}
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
            {"Nessun utente corrisponde a \"" + q.trim() + "\"."}
          </Text>
        </View>
      );
    }
    return null;
  }, [loading, loadingSug, q, rows.length, suggested.length]);

  // Data + section header wiring: if the user is idle (empty query) show
  // suggestions; as soon as they type, replace the list with search
  // results. This keeps the whole feature on a single FlatList so
  // scrolling behaves consistently.
  const listData = q.trim() ? rows : suggested;
  const listHeader = !q.trim() && suggested.length > 0 ? (
    <View style={styles.sectionHeader}>
      <Ionicons name="sparkles" size={14} color={colors.brandPrimary} />
      <Text style={styles.sectionHeaderTxt}>SUGGERITI PER TE</Text>
    </View>
  ) : null;

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <View style={styles.header}>
        {/* Only the chevron itself is pressable — no hitSlop that could
            overlap the title view next to it, otherwise stray taps on the
            title get interpreted as a back-press and boot the user out
            of the search page unexpectedly. */}
        <Pressable onPress={goBack} style={styles.backBtn} testID="find-back">
          <Ionicons name="chevron-back" size={24} color={colors.onSurface} />
        </Pressable>
        {/* Title area is a plain View (NOT a Pressable) so tapping the
            "CERCA AMICI" text does nothing. `pointerEvents="none"` on the
            container removes it from the hit-testing tree entirely on web,
            defense-in-depth against overlay quirks. */}
        <View style={styles.headerTitleBox} pointerEvents="none">
          <Text style={styles.title}>CERCA AMICI</Text>
          <Text style={styles.subtitle}>Aggiungi persone alla tua Cerchia del Gossip</Text>
        </View>
      </View>

      {isAnon ? (
        <View style={styles.empty} testID="find-anon-blocked">
          <Ionicons name="lock-closed-outline" size={44} color={colors.muted} />
          <Text style={styles.emptyTitle}>Funzione non disponibile</Text>
          <Text style={styles.emptyHint}>
            {"La ricerca amici non è disponibile per i profili anonimi.\n\nCrea un account per aggiungere persone alla tua Cerchia del Gossip."}
          </Text>
          <Pressable onPress={goBack} style={styles.anonBackBtn} testID="find-anon-back">
            <Text style={styles.anonBackTxt}>TORNA AL PROFILO</Text>
          </Pressable>
        </View>
      ) : (
        <>
          <View style={styles.searchWrap}>
        <View style={styles.searchIconSlot}>
          <Ionicons name="search-outline" size={20} color={colors.muted} />
        </View>
        <TextInput
          value={q}
          onChangeText={setQ}
          placeholder="Cerca per nickname o nome…"
          placeholderTextColor={colors.muted}
          style={styles.searchInput}
          autoCapitalize="none"
          autoCorrect={false}
          returnKeyType="search"
          testID="find-search-input"
        />
        {q.length > 0 ? (
          <Pressable onPress={() => setQ("")} testID="find-clear">
            <Ionicons name="close-circle" size={20} color={colors.muted} />
          </Pressable>
        ) : null}
      </View>

      {loading ? (
        <View style={styles.loadingWrap}>
          <ActivityIndicator color={colors.brandPrimary} />
        </View>
      ) : null}

      <FlatList
        data={listData}
        keyExtractor={(x) => x.user_id}
        renderItem={renderRow}
        contentContainerStyle={{ paddingBottom: spacing.xl }}
        ListHeaderComponent={listHeader}
        ListEmptyComponent={emptyState}
        testID="find-list"
        keyboardShouldPersistTaps="handled"
      />
        </>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
  },
  backBtn: {
    width: 40,
    height: 40,
    alignItems: "center",
    justifyContent: "center",
  },
  headerTitleBox: {
    flex: 1,
    marginLeft: spacing.sm,
  },
  title: { color: colors.onSurface, fontSize: font.sizes.lg, fontWeight: "800", letterSpacing: 1.5 },
  subtitle: { color: colors.muted, fontSize: font.sizes.sm, marginTop: 4, fontWeight: "600" },
  searchWrap: {
    flexDirection: "row",
    alignItems: "center",
    marginHorizontal: spacing.lg,
    marginTop: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: 10,
    borderWidth: 1.5,
    borderColor: colors.brandSecondary,
    borderRadius: radius.md,
    backgroundColor: "transparent",
  },
  searchIconSlot: {
    width: 24,
    alignItems: "center",
    justifyContent: "center",
    marginRight: spacing.sm,
  },
  searchInput: {
    flex: 1,
    color: colors.onSurface,
    fontSize: font.sizes.base,
    padding: 0,
  },
  sectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.lg,
    paddingBottom: spacing.sm,
  },
  sectionHeaderTxt: {
    color: colors.brandPrimary,
    fontSize: font.sizes.xs,
    fontWeight: "800",
    letterSpacing: 2,
  },
  reasonRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 4,
    marginTop: 4,
  },
  reasonChip: {
    backgroundColor: colors.brandSecondary,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 999,
  },
  reasonTxt: {
    color: colors.onBrandSecondary,
    fontSize: 10,
    fontWeight: "700",
    letterSpacing: 0.5,
  },
  loadingWrap: { paddingVertical: spacing.md, alignItems: "center" },
  row: {
    flexDirection: "row",
    alignItems: "center",
    marginHorizontal: spacing.lg,
    marginTop: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    gap: spacing.md,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceSecondary,
  },
  rowLeft: { flexDirection: "row", alignItems: "center", flex: 1, gap: spacing.md },
  avatar: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.surfaceTertiary },
  avatarFallback: { alignItems: "center", justifyContent: "center" },
  nick: { color: colors.brandSecondary, fontSize: font.sizes.base, fontWeight: "800" },
  meTag: { color: colors.brandPrimary, fontSize: font.sizes.xs, fontWeight: "700", letterSpacing: 1 },
  dispname: { color: colors.muted, fontSize: font.sizes.sm, marginTop: 2 },
  addBtn: {
    flexDirection: "row", alignItems: "center", gap: 4,
    backgroundColor: colors.brandPrimary,
    paddingHorizontal: 12, paddingVertical: 8,
    borderRadius: 999,
  },
  addBtnOn: { backgroundColor: colors.brandSecondary },
  addBtnBusy: { opacity: 0.55 },
  addBtnTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.xs, fontWeight: "800", letterSpacing: 0.5 },
  addBtnTxtOn: { color: colors.onBrandSecondary },
  empty: {
    alignItems: "center",
    padding: spacing.xl,
    gap: spacing.sm,
    marginTop: spacing.xl,
  },
  emptyTitle: { color: colors.onSurface, fontSize: font.sizes.lg, fontWeight: "600" },
  emptyHint: { color: colors.muted, fontSize: font.sizes.sm, textAlign: "center", lineHeight: 20 },
  anonBackBtn: {
    marginTop: spacing.lg,
    backgroundColor: colors.brandPrimary,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderRadius: 4,
  },
  anonBackTxt: {
    color: colors.onBrandPrimary,
    fontSize: font.sizes.sm,
    fontWeight: "700",
    letterSpacing: 1.5,
  },
});
