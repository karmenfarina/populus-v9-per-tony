import { useCallback, useEffect, useMemo, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, FlatList,
  ActivityIndicator,
} from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { api, Feud } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { colors, spacing, font } from "@/src/theme";
import FeudCard from "@/src/components/FeudCard";

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
  const params = useLocalSearchParams<{ category?: string }>();
  const initialCat = (params.category as string) || "all";
  const [cats, setCats] = useState<{ id: string; label: string }[]>([ALL_CAT]);
  const [category, setCategory] = useState<string>(initialCat);
  const [dates, setDates] = useState<DateEntry[]>([]);
  const [loadingDates, setLoadingDates] = useState(true);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [feuds, setFeuds] = useState<Feud[]>([]);
  const [loadingFeuds, setLoadingFeuds] = useState(false);

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

  // Load available dates when category changes
  const loadDates = useCallback(async (cat: string) => {
    setLoadingDates(true);
    setSelectedDate(null);
    setFeuds([]);
    try {
      const r = await api.archiveDates(cat);
      const list: DateEntry[] = r.dates || [];
      setDates(list);
      if (list.length > 0) setSelectedDate(list[0].date);
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
          <Pressable onPress={() => router.back()} testID="archive-back" style={styles.backBtn}>
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
            data={feuds}
            keyExtractor={(f) => f.feud_id}
            contentContainerStyle={{ padding: spacing.lg, paddingBottom: spacing.xxxl }}
            ItemSeparatorComponent={() => <View style={{ height: spacing.lg }} />}
            ListEmptyComponent={
              <View style={styles.center} testID="archive-empty-feuds">
                <Text style={styles.empty}>NESSUNA FAIDA IN QUESTA CATEGORIA.</Text>
              </View>
            }
            renderItem={({ item }) => (
              <FeudCard
                feud={item}
                showArchivedBadge
                onPress={() => router.push(`/feud/${item.feud_id}`)}
              />
            )}
          />
        )
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: { paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.md, borderBottomWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceInverse },
  headerTop: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  backBtn: { width: 44, height: 44, borderWidth: 2, borderColor: colors.brandSecondary, alignItems: "center", justifyContent: "center", backgroundColor: colors.surfaceInverse },
  brand: { color: colors.onSurfaceInverse, fontSize: font.sizes.xxxl, letterSpacing: 2, fontWeight: "500" },
  subtitle: { color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 2, marginTop: 2 },
  chipRowWrap: { height: 56, backgroundColor: colors.surfaceInverse, borderBottomWidth: 2, borderColor: colors.border },
  chipRowContent: { paddingHorizontal: spacing.lg, gap: spacing.sm, alignItems: "center" },
  chip: { height: 36, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: colors.brandSecondary, justifyContent: "center", alignItems: "center", flexShrink: 0, backgroundColor: colors.surfaceInverse },
  chipActive: { backgroundColor: colors.brandSecondary, borderColor: colors.brandSecondary },
  chipText: { color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 1 },
  chipTextActive: { color: colors.onBrandSecondary, fontWeight: "500" },
  datesLoading: { paddingVertical: spacing.lg, alignItems: "center" },
  datesRowWrap: { paddingVertical: spacing.md, backgroundColor: colors.surfaceSecondary, borderBottomWidth: 2, borderColor: colors.border },
  datesRowContent: { paddingHorizontal: spacing.lg, gap: spacing.sm, alignItems: "center" },
  dateBox: { position: "relative", minWidth: 72, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderWidth: 2, borderColor: colors.border, backgroundColor: colors.surface, alignItems: "center" },
  dateBoxActive: { backgroundColor: colors.onSurface, borderColor: colors.onSurface },
  dateTop: { color: colors.onSurface, fontSize: font.sizes.sm, letterSpacing: 1, fontWeight: "500" },
  dateTopActive: { color: colors.brandSecondary },
  dateBottom: { color: colors.muted, fontSize: font.sizes.xs, marginTop: 2, letterSpacing: 1 },
  dateBottomActive: { color: "#FFFFFF" },
  dateCountBadge: { position: "absolute", top: -8, right: -8, minWidth: 20, height: 20, paddingHorizontal: 4, borderWidth: 2, borderColor: colors.onSurface, backgroundColor: colors.brandPrimary, alignItems: "center", justifyContent: "center" },
  dateCountBadgeActive: { backgroundColor: colors.brandSecondary, borderColor: colors.brandSecondary },
  dateCountTxt: { color: colors.onBrandPrimary, fontSize: 10, fontWeight: "500", letterSpacing: 0.5 },
  dateCountTxtActive: { color: colors.onBrandSecondary },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xxl },
  empty: { fontSize: font.sizes.xl, color: colors.onSurface, letterSpacing: 1, textAlign: "center" },
  emptyDatesBox: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xxl, gap: spacing.sm },
  emptyDatesTitle: { fontSize: font.sizes.xxl, letterSpacing: 2, color: colors.onSurface, fontWeight: "500" },
  emptyDatesTxt: { fontSize: font.sizes.base, color: colors.muted, textAlign: "center", lineHeight: 20 },
});
