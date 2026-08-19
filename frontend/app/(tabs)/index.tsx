import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, RefreshControl,
  ActivityIndicator, TextInput, Image, useWindowDimensions, Platform,
  PanResponder,
} from "react-native";
import { FlashList, type FlashListRef } from "@shopify/flash-list";
import { useRouter, useLocalSearchParams, useFocusEffect } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { api, Feud } from "@/src/api";
import { cachedGet } from "@/src/utils/clientCache";
import { useAuth } from "@/src/auth/AuthContext";
import { colors, spacing, font, radius } from "@/src/theme";
import FeudCard from "@/src/components/FeudCard";
import StoriesBar from "@/src/components/StoriesBar";
import { ScrollToTopButton } from "@/src/components/ScrollToTopButton";
import { FeudListSkeleton } from "@/src/components/Skeleton";

const ALL_CAT = { id: "all", label: "Tutte" };
const HYPE_CAT = { id: "hype", label: "🔥 Hype" };

export default function HomeFeed() {
  const router = useRouter();
  const params = useLocalSearchParams<{ category?: string }>();
  const { user } = useAuth();
  const [cats, setCats] = useState<{ id: string; label: string }[]>([ALL_CAT]);
  const [selected, setSelected] = useState<string>((params.category as string) || "all");
  const [feuds, setFeuds] = useState<Feud[]>([]);
  const [loading, setLoading] = useState(true);
  const [pullRefreshing, setPullRefreshing] = useState(false); // ONLY set on manual pull-to-refresh
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQ, setSearchQ] = useState("");
  const [searching, setSearching] = useState(false);

  // Keep the selected category in sync with the incoming `?category=` param.
  // Enables round-trip preservation with the Archive screen and deep-links.
  useEffect(() => {
    const incoming = (params.category as string) || null;
    if (incoming && incoming !== selected) setSelected(incoming);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.category]);

  // Auto-center the selected chip in the horizontal strip.
  const chipScrollRef = useRef<ScrollView>(null);
  const chipLayouts = useRef<Record<string, { x: number; w: number }>>({});
  // Ref + visibility flag driving the floating "back to top" pill on the feed.
  const feedListRef = useRef<FlashListRef<Feud>>(null);
  const [showTopBtn, setShowTopBtn] = useState(false);
  // Scroll offset SEPARATO per categoria: sfogliando "gossip" e tornando su
  // "tutte" volevamo che la home riaprisse alla posizione dove l'avevamo
  // lasciata in "tutte" (non a quella di "gossip"). La FlashList di per se'
  // mantiene un solo offset globale perche' e' la stessa istanza a mostrare
  // dataset diversi; qui memorizziamo un offset per chip e lo ripristiniamo
  // esplicitamente al cambio categoria. `selectedRef` serve a `handleScroll`
  // per scrivere sempre nella chiave corretta senza dipendere da closure
  // vecchie.
  const scrollByCategoryRef = useRef<Record<string, number>>({});
  const selectedRef = useRef<string>((params.category as string) || "all");
  const { width: winW } = useWindowDimensions();
  const centerChip = useCallback((id: string, animated = true) => {
    const l = chipLayouts.current[id];
    if (!l || !chipScrollRef.current) return;
    const target = Math.max(0, l.x - winW / 2 + l.w / 2);
    chipScrollRef.current.scrollTo({ x: target, animated });
  }, [winW]);
  useEffect(() => { centerChip(selected); selectedRef.current = selected; }, [selected, centerChip]);

  const load = useCallback(async (category: string) => {
    // TTL 60s: latenza percepita sul cambio chip era il bug #1 in APK
    // (~1s+ per rispuntare la lista). 60s copre il caso tipico dello
    // "sfoglio a bruciapelo" senza far invecchiare troppo il contenuto
    // — pull-to-refresh invalida comunque immediatamente. Le liste
    // /feuds e /feuds/hype sono quelle che vengono rifornite dal
    // backend molto piu' frequentemente di ogni singolo utente.
    if (category === "hype") {
      const res = await cachedGet(`feuds:hype`, 60_000, () => api.feudsHype());
      setFeuds(res.feuds);
      return;
    }
    const res = await cachedGet(
      `feuds:${category}`,
      60_000,
      () => api.feuds(category)
    );
    // NB: nessun filtro client-side sulle categorie preferite. Il backend
    // gia' rispetta `favorite_categories` dell'utente quando category === "all":
    // un secondo filtro client svuotava la home se il batch server non
    // matchava (bug osservato su APK dopo ~1min di uso, in combinazione
    // col 401 fasullo di SecureStore).
    setFeuds(res.feuds);
  }, []);

  // Prefetch silenzioso in background: scalda la cache per le categorie
  // preferite (+ hype) subito dopo aver caricato la selezione iniziale.
  // Cosi' il primo tap su una chip nel giro di ~1min restituisce dati
  // istantanei dalla cache, invece di aspettare una round-trip di rete.
  // Fire-and-forget: nessun setState finche' l'utente non seleziona
  // davvero quella chip. Concorrenza limitata a 3 in parallelo per non
  // saturare la rete su APK Android/3G.
  const prefetchCategories = useCallback(async (ids: string[]) => {
    const uniq = Array.from(new Set(ids.filter((id) => id && id !== 'all')));
    const CONCURRENCY = 3;
    let idx = 0;
    const worker = async () => {
      while (idx < uniq.length) {
        const my = idx++;
        const id = uniq[my];
        try {
          if (id === 'hype') {
            await cachedGet(`feuds:hype`, 60_000, () => api.feudsHype());
          } else {
            await cachedGet(`feuds:${id}`, 60_000, () => api.feuds(id));
          }
        } catch { /* silent: prefetch failure never blocks UI */ }
      }
    };
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, uniq.length) }, worker));
  }, []);

  useEffect(() => {
    (async () => {
      try {
        // /categories cache 10min (contenuto statico)
        const c = await cachedGet('categories', 600_000, () => api.categories());
        const favs = user?.favorite_categories || [];
        // Chip row order: [TUTTE, HYPE, ...user favorites]. HYPE is fixed
        // right after "Tutte" — it can never be hidden or reordered by the
        // user, and it deliberately doesn't appear in the profile/onboarding
        // category preferences list.
        const ordered: { id: string; label: string }[] = [ALL_CAT, HYPE_CAT];
        if (favs.length > 0) {
          for (const id of favs) {
            const found = c.categories.find((x: any) => x.id === id);
            if (found) ordered.push(found);
          }
        } else {
          for (const cat of c.categories) ordered.push(cat);
        }
        setCats(ordered);
        // Preselected category defaults to "Tutte" for every user, including
        // those with favorites (they can jump to their preferred category via
        // the chip row). This matches the user expectation that the home
        // opens on the widest possible feed.
        setSelected("all");
        await load("all");
        lastLoadAtRef.current = Date.now();
        // Non-blocking prefetch of Hype + favorite chips: warms
        // `cachedGet` so subsequent taps are instant. Never awaited.
        prefetchCategories(['hype', ...favs]);
      } finally { setLoading(false); }
    })();
  }, [load, prefetchCategories, user?.favorite_categories]);

  const onSelect = async (id: string) => {
    if (id === selected) return;
    // NB: `handleScroll` ha gia' salvato l'offset corrente per la categoria
    // che stiamo lasciando (aggiornato ad ogni scroll event). Cambiamo chip e
    // ripristiniamo l'offset salvato per la NUOVA categoria (0 la prima volta).
    setSelected(id);
    setSearchOpen(false); setSearchQ("");
    try {
      await load(id);
      const saved = scrollByCategoryRef.current[id] ?? 0;
      // 1 frame perche' React renderizzi FlashList con i nuovi feuds prima
      // dello scrollToOffset — altrimenti l'offset scatta su una lista che
      // deve ancora aggiornarsi.
      requestAnimationFrame(() => {
        try { feedListRef.current?.scrollToOffset({ offset: saved, animated: false }); } catch { /* silent */ }
      });
    } catch { /* keep the old list */ }
  };

  const onRefresh = async () => {
    setPullRefreshing(true);
    try {
      if (searchQ.trim()) {
        const r = await api.search(searchQ.trim());
        setFeuds(r.feuds);
      } else {
        await load(selected);
      }
    } finally { setPullRefreshing(false); }
  };
  // Keep the latest onRefresh in a ref so the PanResponder (created once via
  // useRef) always invokes the current callback with the up-to-date `selected`
  // category and search state — otherwise a stale closure would refresh the
  // wrong category (bug: category=X pulls to refresh but shows category=all).
  const onRefreshRef = useRef(onRefresh);
  onRefreshRef.current = onRefresh;

  // Web-only pull-to-refresh via PanResponder. RefreshControl doesn't render on
  // react-native-web, so we manually track vertical drag from the top of the
  // list and expose a live "pullProgress" (0..1) to show a growing spinner.
  const isWeb = Platform.OS === 'web';
  const scrollAtTopRef = useRef(true);
  const lastLoadAtRef = useRef<number>(0);
  const [pullProgress, setPullProgress] = useState(0);
  const pullProgressRef = useRef(0);
  const PULL_THRESHOLD = 40; // pixels — lowered so a small flick is enough
  const webPan = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => false,
      onMoveShouldSetPanResponder: (_e, g) =>
        isWeb && scrollAtTopRef.current && g.dy > 3 && Math.abs(g.dy) > Math.abs(g.dx),
      onPanResponderMove: (_e, g) => {
        if (g.dy > 0) {
          const p = Math.min(1, g.dy / PULL_THRESHOLD);
          pullProgressRef.current = p;
          setPullProgress(p);
        }
      },
      onPanResponderRelease: (_e, g) => {
        // Trigger either by pulling past the threshold OR by a quick downward
        // flick (velocity-based) — both feel immediate to the user.
        if (g.dy >= PULL_THRESHOLD || (g.dy >= 15 && g.vy >= 0.4)) {
          onRefreshRef.current();
        }
        pullProgressRef.current = 0;
        setPullProgress(0);
      },
      onPanResponderTerminate: () => {
        pullProgressRef.current = 0;
        setPullProgress(0);
      },
    })
  ).current;

  // Silent refresh whenever the tab regains focus (e.g. after visiting a feud
  // detail or another tab). No visual indicator — data is swapped in place.
  const firstFocusRef = useRef(true);
  useFocusEffect(
    useCallback(() => {
      if (firstFocusRef.current) {
        firstFocusRef.current = false;
        return;
      }
      // HYPE always refreshes on focus — the section is time-sensitive and
      // must re-rank its posts each time the user returns to the tab.
      // Other categories: 3 s throttle just to avoid rapid re-fetch during
      // modal open/close flapping. Longer throttles previously caused stale
      // vote-percentages on the preview after voting on a detail screen.
      const isHype = selected === "hype";
      if (!isHype && Date.now() - lastLoadAtRef.current < 3_000) {
        return;
      }
      let cancelled = false;
      (async () => {
        try {
          if (searchQ.trim()) {
            const r = await api.search(searchQ.trim());
            if (!cancelled) setFeuds(r.feuds);
          } else {
            await load(selected);
          }
          lastLoadAtRef.current = Date.now();
        } catch { /* silent */ }
      })();
      return () => { cancelled = true; };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selected, searchQ])
  );

  const runSearch = async (q: string) => {
    if (!q.trim()) { await load(selected); return; }
    setSearching(true);
    try {
      const r = await api.search(q.trim());
      setFeuds(r.feuds);
    } finally { setSearching(false); }
  };

  // ── Memoized list callbacks — evitano un re-render dell'intera lista
  //    ogni volta che HomeFeed si ri-renderizza (per cambio di stato non
  //    correlato ai dati della FlashList).
  const keyExtractor = useCallback((f: Feud) => f.feud_id, []);
  const renderItem = useCallback(
    ({ item }: { item: Feud }) => (
      <FeudCard feud={item} onPress={() => router.push(`/feud/${item.feud_id}`)} />
    ),
    [router]
  );
  const handleScroll = useCallback((e: any) => {
    const y = e.nativeEvent.contentOffset.y;
    scrollAtTopRef.current = y <= 4;
    setShowTopBtn(y > 600);
    // Memorizzo l'offset sotto la categoria correntemente selezionata cosi'
    // che al prossimo `onSelect(...)` possiamo tornarci esattamente. Uso il
    // ref (non `selected`) per evitare closure vecchie: la callback e'
    // memoizzata con dep vuoto.
    scrollByCategoryRef.current[selectedRef.current] = y;
  }, []);
  const ItemSeparator = useMemo(() => {
    const Sep = () => <View style={{ height: spacing.lg }} />;
    Sep.displayName = "ItemSeparator";
    return Sep;
  }, []);
  const EmptyList = useMemo(
    () => (
      <View style={styles.center} testID="home-empty">
        <Text style={styles.empty}>NESSUNA FAIDA IN QUESTA CATEGORIA.</Text>
      </View>
    ),
    []
  );

  return (
    <SafeAreaView style={styles.safe} edges={["top"]} testID="home-screen">
      <View style={styles.header}>
        <View style={styles.headerTop}>
          <View style={{ flex: 1 }}>
            <View style={styles.brandRow}>
              <Text style={styles.brand}>POPULUS</Text>
              <Image
                source={require("../../assets/images/icon-dark.png")}
                style={styles.brandLogo}
                resizeMode="contain"
              />
            </View>
            <Text style={styles.date}>{new Date().toLocaleDateString("it-IT", { day: "numeric", month: "long" }).toUpperCase()}</Text>
          </View>
          <Pressable onPress={() => router.push(`/archive?category=${selected}`)} testID="archive-toggle" style={styles.archiveBtn}>
            <Ionicons name="calendar-outline" size={22} color={colors.brandSecondary} />
          </Pressable>
          <Pressable onPress={() => setSearchOpen((v) => !v)} testID="search-toggle" style={styles.searchBtn}>
            <Ionicons name={searchOpen ? "close" : "search"} size={22} color={colors.brandSecondary} />
          </Pressable>
        </View>
        {searchOpen && (
          <View style={styles.searchWrap}>
            <TextInput
              style={styles.searchInput}
              placeholder="Cerca faide..."
              placeholderTextColor={colors.muted}
              value={searchQ}
              onChangeText={setSearchQ}
              onSubmitEditing={() => runSearch(searchQ)}
              returnKeyType="search"
              autoFocus
              testID="search-input"
            />
            {searching && <ActivityIndicator color={colors.brandSecondary} style={{ marginLeft: spacing.sm }} />}
          </View>
        )}
      </View>

      {/* Category chips first — they anchor the primary navigation
          of the home feed. */}
      <View style={styles.chipRowWrap}>
        <ScrollView
          ref={chipScrollRef}
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.chipRowContent}
          testID="category-chip-row"
        >
          {cats.map((c) => (
            <Pressable
              key={c.id}
              testID={`chip-${c.id}`}
              onPress={() => onSelect(c.id)}
              onLayout={(e) => {
                const { x, width } = e.nativeEvent.layout;
                chipLayouts.current[c.id] = { x, w: width };
                if (c.id === selected) centerChip(c.id, false);
              }}
              style={[styles.chip, selected === c.id && styles.chipActive]}
            >
              <Text style={[styles.chipText, selected === c.id && styles.chipTextActive]}>
                {c.label.toUpperCase()}
              </Text>
            </Pressable>
          ))}
        </ScrollView>
      </View>

      {/* Stories strip — placed AFTER the category chips so it doesn't
          hijack the top of the screen. Collapsed by default; opens
          into the full ring strip when tapped. Empty for anonymous
          accounts. */}
      <StoriesBar />

      {(pullRefreshing || pullProgress > 0) && (
        <View
          style={[styles.refreshPill, { opacity: pullRefreshing ? 1 : Math.max(0.2, pullProgress) }]}
          pointerEvents="none"
          testID="home-refresh-pill"
        >
          <ActivityIndicator size="small" color={colors.brandSecondary} />
        </View>
      )}

      {loading ? (
        <View style={{ flex: 1 }} testID="home-loading">
          <FeudListSkeleton count={3} />
        </View>
      ) : (
        <View style={{ flex: 1 }} {...(isWeb ? webPan.panHandlers : {})}>
        <FlashList
          ref={feedListRef}
          data={feuds}
          keyExtractor={keyExtractor}
          contentContainerStyle={{ padding: spacing.lg, paddingBottom: spacing.xxxl }}
          ItemSeparatorComponent={ItemSeparator}
          refreshControl={<RefreshControl refreshing={pullRefreshing} onRefresh={onRefresh} tintColor={colors.brandSecondary} colors={[colors.brandSecondary]} />}
          onScroll={handleScroll}
          scrollEventThrottle={100}
          ListEmptyComponent={EmptyList}
          renderItem={renderItem}
        />
        <ScrollToTopButton
          visible={showTopBtn}
          onPress={() => {
            feedListRef.current?.scrollToOffset({ offset: 0, animated: true });
          }}
          testID="home-scroll-top"
        />
        </View>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  refreshPill: {
    flexDirection: "row", alignItems: "center", justifyContent: "center",
    gap: spacing.sm, paddingVertical: spacing.xs, paddingHorizontal: spacing.sm,
    backgroundColor: colors.surfaceInverse, borderBottomWidth: 1, borderColor: colors.border,
  },
  refreshPillTxt: { color: colors.brandSecondary, fontSize: font.sizes.xs, letterSpacing: 1, fontWeight: "500" },
  header: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    paddingBottom: spacing.md,
    backgroundColor: colors.surfaceInverse,
  },
  headerTop: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  searchBtn: {
    width: 44, height: 44,
    borderWidth: 1.5, borderColor: colors.brandSecondary,
    borderRadius: radius.md,
    alignItems: "center", justifyContent: "center",
    backgroundColor: colors.surfaceInverse,
  },
  archiveBtn: {
    width: 44, height: 44,
    borderWidth: 1.5, borderColor: colors.brandSecondary,
    borderRadius: radius.md,
    alignItems: "center", justifyContent: "center",
    backgroundColor: colors.surfaceInverse,
  },
  searchWrap: { flexDirection: "row", alignItems: "center", marginTop: spacing.sm },
  searchInput: {
    flex: 1,
    borderWidth: 1.5, borderColor: colors.brandSecondary,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceInverse, color: colors.onSurfaceInverse,
    padding: spacing.sm, fontSize: font.sizes.base,
  },
  brand: {
    color: colors.onSurfaceInverse,
    fontSize: font.sizes.xxxl,
    letterSpacing: 1.5,
    fontWeight: "800",
    marginLeft: -2,
  },
  brandRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  brandLogo: { width: 32, height: 32 },
  date: {
    color: colors.brandSecondary,
    fontSize: font.sizes.xs,
    letterSpacing: 2,
    marginTop: 2,
    fontWeight: "700",
  },
  chipRowWrap: {
    height: 60,
    backgroundColor: colors.surfaceInverse,
  },
  chipRowContent: { paddingHorizontal: spacing.lg, gap: spacing.sm, alignItems: "center" },
  chip: {
    height: 40,
    paddingHorizontal: spacing.lg,
    borderWidth: 1.5,
    borderColor: colors.brandSecondary,
    borderRadius: radius.pill,
    justifyContent: "center",
    alignItems: "center",
    flexShrink: 0,
    backgroundColor: "transparent",
  },
  chipActive: { backgroundColor: colors.brandSecondary, borderColor: colors.brandSecondary },
  chipText: {
    color: colors.brandSecondary,
    fontSize: font.sizes.sm,
    letterSpacing: 1,
    fontWeight: "700",
  },
  chipTextActive: { color: colors.onBrandSecondary, fontWeight: "800" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xxl },
  empty: { fontSize: font.sizes.xl, color: colors.onSurface, letterSpacing: 1 },
});
