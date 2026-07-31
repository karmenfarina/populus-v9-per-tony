import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, FlatList,
  ActivityIndicator, useWindowDimensions,
} from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { api, Feud } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { colors, spacing, font, radius } from "@/src/theme";
import FeudCard from "@/src/components/FeudCard";
import { ScrollToTopButton } from "@/src/components/ScrollToTopButton";

const ALL_CAT = { id: "all", label: "Tutte" };

type DateEntry = { date: string; count: number };

/** Return an ISO YYYY-MM-DD label localized:
 *  - "OGGI" (today) — should almost never appear in archive but kept for safety
 *  - "IERI" (yesterday)
 *  - "MAR 08/07" (weekday abbrev + dd/mm)
 */
function labelForDate(iso: string): { top: string; bottom: string } {
  const d = new Date(`${iso}T12:00:00Z`);
  const today = new Date();
  const yStr = new Date(today.getTime() - 24 * 3600 * 1000).toISOString().slice(0, 10);
  const tStr = today.toISOString().slice(0, 10);
  if (iso === tStr) return { top: "OGGI", bottom: "" };
  if (iso === yStr) return { top: "IERI", bottom: "" };
  const wd = d.toLocaleDateString("it-IT", { weekday: "short" }).toUpperCase().replace(".", "");
  const dm = `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}`;
  return { top: wd, bottom: dm };
}

export default function ArchiveScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const params = useLocalSearchParams<{ category?: string; date?: string }>();
  const initialCat = (params.category as string) || "all";
  const initialDate = (params.date as string) || null;
  const [cats, setCats] = useState<{ id: string; label: string }[]>([ALL_CAT]);
  const [category, setCategory] = useState<string>(initialCat);
  const [dates, setDates] = useState<DateEntry[]>([]);
  const [loadingDates, setLoadingDates] = useState(true);
  const [selectedDate, setSelectedDate] = useState<string | null>(initialDate);
  const [feuds, setFeuds] = useState<Feud[]>([]);
  const [loadingFeuds, setLoadingFeuds] = useState(false);

  // Keep local state in sync with the incoming `category` param. Without this,
  // subsequent visits to /archive?category=X don't update the selection because
  // useState(initialCat) only runs at mount and the tab screen is kept alive.
  useEffect(() => {
    const incoming = (params.category as string) || "all";
    setCategory((prev) => (prev === incoming ? prev : incoming));
  }, [params.category]);

  // Same for `date` — when returning from a feud detail we get pushed back
  // with the exact archive day the user was viewing.
  useEffect(() => {
    const incoming = (params.date as string) || null;
    if (incoming) {
      setSelectedDate((prev) => (prev === incoming ? prev : incoming));
    }
  }, [params.date]);

  // Auto-center the selected category chip in the horizontal strip.
  const chipScrollRef = useRef<ScrollView>(null);
  const chipLayouts = useRef<Record<string, { x: number; w: number }>>({});
  // Floating "back to top" pill on the archive feuds list.
  const archiveListRef = useRef<FlatList<Feud>>(null);
  const [showTopBtn, setShowTopBtn] = useState(false);
  // When the user taps the floating pill we run an animated scrollToOffset.
  // React Native keeps firing `onScroll` during that animation with the
  // intermediate positions — many of which are still above the 600px
  // threshold. Without this gate, `setShowTopBtn(true)` would be called
  // mid-animation and the pill would flicker back on the moment the
  // internal suppress timer in <ScrollToTopButton /> expires. We lift the
  // gate for a comfortable 1.2s so even a long list has time to finish
  // its glide.
  const suppressScrollUpdatesRef = useRef(false);
  const { width: winW } = useWindowDimensions();
  const centerChip = useCallback((id: string, animated = true) => {
    const l = chipLayouts.current[id];
    if (!l || !chipScrollRef.current) return;
    const target = Math.max(0, l.x - winW / 2 + l.w / 2);
    chipScrollRef.current.scrollTo({ x: target, animated });
  }, [winW]);
  useEffect(() => { centerChip(category); }, [category, centerChip]);

  // Load categories once (ordered by favorites like home)
  useEffect(() => {
    (async () => {
      try {
        const c = await api.categories();
        const favs = user?.favorite_categories || [];
        const favIds = new Set(favs);
        const ordered: { id: string; label: string }[] = [ALL_CAT];
        for (const id of favs) {
          const found = c.categories.find((x: any) => x.id === id);
          if (found) ordered.push(found);
        }
        for (const cat of c.categories) {
          if (!favIds.has(cat.id)) ordered.push(cat);
        }
        setCats(ordered);
      } catch { /* ignore */ }
    })();
  }, [user?.favorite_categories]);

  // Load available dates when category changes. If we already had a date
  // selected in the previous category, try to keep it (or snap to the
  // nearest available one) so the user's temporal context is preserved
  // while browsing categories.
  const selectedDateRef = useRef<string | null>(null);
  useEffect(() => { selectedDateRef.current = selectedDate; }, [selectedDate]);

  const loadDates = useCallback(async (cat: string) => {
    setLoadingDates(true);
    setFeuds([]);
    const prevSelected = selectedDateRef.current;
    try {
      const r = await api.archiveDates(cat);
      const list: DateEntry[] = r.dates || [];
      setDates(list);
      if (list.length === 0) {
        setSelectedDate(null);
        return;
      }
      // Attempt: exact match first, then the temporally nearest date. Falls
      // back to the newest date if there is no previous selection.
      let target: string | null = null;
      if (prevSelected && list.some((d) => d.date === prevSelected)) {
        target = prevSelected;
      } else if (prevSelected) {
        // Snap to the closest date by absolute time diff.
        const prevMs = new Date(prevSelected).getTime();
        let bestDiff = Infinity;
        for (const d of list) {
          const diff = Math.abs(new Date(d.date).getTime() - prevMs);
          if (diff < bestDiff) { bestDiff = diff; target = d.date; }
        }
      }
      if (!target) target = list[0].date;
      setSelectedDate(target);
    } catch { setDates([]); }
    finally { setLoadingDates(false); }
  }, []);

  useEffect(() => { loadDates(category); }, [category, loadDates]);

  // Load feuds when date changes
  const loadFeudsFor = useCallback(async (date: string, cat: string) => {
    setLoadingFeuds(true);
    try {
      const r = await api.archiveFeuds(date, cat);
      setFeuds(r.feuds || []);
    } catch { setFeuds([]); }
    finally { setLoadingFeuds(false); }
  }, []);

  useEffect(() => {
    if (selectedDate) loadFeudsFor(selectedDate, category);
  }, [selectedDate, category, loadFeudsFor]);

  const emptyDates = useMemo(() => !loadingDates && dates.length === 0, [loadingDates, dates]);

  return (
    <SafeAreaView style={styles.safe} edges={["top"]} testID="archive-screen">
      <View style={styles.header}>
        <View style={styles.headerTop}>
          <Pressable
            onPress={() => router.replace(`/?category=${category}`)}
            testID="archive-back"
            style={styles.backBtn}
          >
            <Ionicons name="chevron-back" size={22} color={colors.brandSecondary} />
          </Pressable>
          <View style={{ flex: 1 }}>
            <Text style={styles.brand}>ARCHIVIO</Text>
            <Text style={styles.subtitle}>ULTIMI 7 GIORNI</Text>
          </View>
        </View>
      </View>

      <View style={styles.chipRowWrap}>
        <ScrollView
          ref={chipScrollRef}
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.chipRowContent}
          testID="archive-cat-chip-row"
        >
          {cats.map((c) => (
            <Pressable
              key={c.id}
              testID={`archive-chip-${c.id}`}
              onPress={() => setCategory(c.id)}
              onLayout={(e) => {
                const { x, width } = e.nativeEvent.layout;
                chipLayouts.current[c.id] = { x, w: width };
                if (c.id === category) centerChip(c.id, false);
              }}
              style={[styles.chip, category === c.id && styles.chipActive]}
            >
              <Text style={[styles.chipText, category === c.id && styles.chipTextActive]}>
                {c.label.toUpperCase()}
              </Text>
            </Pressable>
          ))}
        </ScrollView>
      </View>

      {/* Date picker */}
      {loadingDates ? (
        <View style={styles.datesLoading}>
          <ActivityIndicator color={colors.brandPrimary} />
        </View>
      ) : emptyDates ? (
        <View style={styles.emptyDatesBox} testID="archive-empty-dates">
          <Ionicons name="calendar-outline" size={48} color={colors.muted} />
          <Text style={styles.emptyDatesTitle}>NESSUN ARCHIVIO</Text>
          <Text style={styles.emptyDatesTxt}>
            Non ci sono faide più vecchie di 24 ore in questa sezione.
          </Text>
        </View>
      ) : (
        <View style={styles.datesRowWrap}>
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.datesRowContent}
            testID="archive-dates-row"
          >
            {dates.map((d) => {
              const isSel = d.date === selectedDate;
              const lbl = labelForDate(d.date);
              return (
                <Pressable
                  key={d.date}
                  onPress={() => setSelectedDate(d.date)}
                  testID={`archive-date-${d.date}`}
                  style={[styles.dateBox, isSel && styles.dateBoxActive]}
                >
                  <Text style={[styles.dateTop, isSel && styles.dateTopActive]}>{lbl.top}</Text>
                  {lbl.bottom ? (
                    <Text style={[styles.dateBottom, isSel && styles.dateBottomActive]}>{lbl.bottom}</Text>
                  ) : null}
                  <View style={[styles.dateCountBadge, isSel && styles.dateCountBadgeActive]}>
                    <Text style={[styles.dateCountTxt, isSel && styles.dateCountTxtActive]}>{d.count}</Text>
                  </View>
                </Pressable>
              );
            })}
          </ScrollView>
        </View>
      )}

      {/* Feuds list for selected date */}
      {!emptyDates && (
        loadingFeuds ? (
          <View style={styles.center} testID="archive-loading">
            <ActivityIndicator size="large" color={colors.brandPrimary} />
          </View>
        ) : (
          <FlatList
            ref={archiveListRef}
            data={feuds}
            keyExtractor={(f) => f.feud_id}
            contentContainerStyle={{ padding: spacing.lg, paddingBottom: spacing.xxxl }}
            ItemSeparatorComponent={() => <View style={{ height: spacing.lg }} />}
            onScroll={(e) => {
              if (suppressScrollUpdatesRef.current) return;
              setShowTopBtn(e.nativeEvent.contentOffset.y > 600);
            }}
            onMomentumScrollEnd={(e) => {
              // Fires whenever inertial or programmatic-animated scrolling
              // comes to a rest. This is the ONLY reliable signal that our
              // scrollToOffset(0) animation is truly finished — a fixed
              // setTimeout underestimates the duration on long lists and
              // causes the pill to flicker back on if the animation is
              // still gliding when the gate re-opens.
              suppressScrollUpdatesRef.current = false;
              setShowTopBtn(e.nativeEvent.contentOffset.y > 600);
            }}
            onScrollEndDrag={(e) => {
              // Belt-and-braces: when the user aborts a programmatic scroll
              // by grabbing the list, resync immediately.
              suppressScrollUpdatesRef.current = false;
              setShowTopBtn(e.nativeEvent.contentOffset.y > 600);
            }}
            scrollEventThrottle={120}
            ListEmptyComponent={
              <View style={styles.center} testID="archive-empty-feuds">
                <Text style={styles.empty}>NESSUNA FAIDA IN QUESTA CATEGORIA.</Text>
              </View>
            }
            renderItem={({ item }) => (
              <FeudCard
                feud={item}
                showArchivedBadge
                onPress={() =>
                  router.push({
                    pathname: "/feud/[id]",
                    params: {
                      id: item.feud_id,
                      from: "archive",
                      archiveCat: category,
                      archiveDate: selectedDate || "",
                    },
                  })
                }
              />
            )}
          />
        )
      )}
      <ScrollToTopButton
        visible={showTopBtn}
        onPress={() => {
          // 1) Immediately hide the pill (parent state + component's own
          //    suppress kick in). 2) Freeze the onScroll gate so events
          //    fired during the animated glide can't re-flip the state.
          //    3) Start the animated scroll. The gate is released by
          //    onMomentumScrollEnd (which fires exactly when the glide
          //    settles) OR — as a fallback for very short lists where
          //    the animation never enters momentum phase — after a
          //    generous 3 s timeout.
          suppressScrollUpdatesRef.current = true;
          setShowTopBtn(false);
          archiveListRef.current?.scrollToOffset({ offset: 0, animated: true });
          setTimeout(() => { suppressScrollUpdatesRef.current = false; }, 3000);
        }}
        testID="archive-scroll-top"
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    paddingBottom: spacing.md,
    backgroundColor: colors.surfaceInverse,
  },
  headerTop: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  backBtn: {
    width: 48,
    height: 48,
    borderWidth: 2,
    borderColor: colors.brandSecondary,
    borderRadius: radius.md,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.surfaceInverse,
  },
  brand: {
    color: colors.onSurfaceInverse,
    fontSize: font.sizes.xxxl,
    letterSpacing: 1.5,
    fontWeight: "800",
  },
  subtitle: {
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
  datesLoading: { paddingVertical: spacing.lg, alignItems: "center" },
  datesRowWrap: {
    paddingTop: spacing.lg,
    paddingBottom: spacing.md,
    backgroundColor: colors.surfaceInverse,
  },
  datesRowContent: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm, // room for the badge overhang
    gap: spacing.sm,
    alignItems: "center",
  },
  dateBox: {
    position: "relative",
    minWidth: 76,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    borderWidth: 1.5,
    borderColor: colors.border,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceSecondary,
    alignItems: "center",
  },
  dateBoxActive: { borderColor: colors.brandSecondary, backgroundColor: colors.surfaceInverse },
  dateTop: {
    color: colors.muted,
    fontSize: font.sizes.base,
    letterSpacing: 1,
    fontWeight: "800",
  },
  dateTopActive: { color: colors.brandSecondary },
  dateBottom: {
    color: colors.muted,
    fontSize: font.sizes.xs,
    marginTop: 4,
    letterSpacing: 0.5,
    fontWeight: "600",
  },
  dateBottomActive: { color: "#FFFFFF" },
  dateCountBadge: {
    position: "absolute",
    top: -8,
    right: -6,
    minWidth: 22,
    height: 22,
    paddingHorizontal: 6,
    borderRadius: 11,
    backgroundColor: colors.brandPrimary,
    alignItems: "center",
    justifyContent: "center",
    // Subtle black outline so the pill "cuts" cleanly out of the card edge
    borderWidth: 2,
    borderColor: colors.surfaceInverse,
  },
  dateCountBadgeActive: { backgroundColor: colors.brandPrimary, borderColor: colors.surfaceInverse },
  dateCountTxt: { color: "#FFFFFF", fontSize: 10, fontWeight: "800", letterSpacing: 0.3 },
  dateCountTxtActive: { color: "#FFFFFF" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xxl },
  empty: { fontSize: font.sizes.xl, color: colors.onSurface, letterSpacing: 1, textAlign: "center" },
  emptyDatesBox: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xxl, gap: spacing.sm },
  emptyDatesTitle: { fontSize: font.sizes.xxl, letterSpacing: 2, color: colors.onSurface, fontWeight: "800" },
  emptyDatesTxt: { fontSize: font.sizes.base, color: colors.muted, textAlign: "center", lineHeight: 20 },
});
