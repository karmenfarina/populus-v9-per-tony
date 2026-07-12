import { useCallback, useEffect, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, FlatList, RefreshControl,
  ActivityIndicator, TextInput, Image,
} from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { api, Feud } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { colors, spacing, font } from "@/src/theme";
import FeudCard from "@/src/components/FeudCard";

const ALL_CAT = { id: "all", label: "Tutte" };

export default function HomeFeed() {
  const router = useRouter();
  const { user } = useAuth();
  const [cats, setCats] = useState<{ id: string; label: string }[]>([ALL_CAT]);
  const [selected, setSelected] = useState<string>("all");
  const [feuds, setFeuds] = useState<Feud[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQ, setSearchQ] = useState("");
  const [searching, setSearching] = useState(false);

  const load = useCallback(async (category: string) => {
    const res = await api.feuds(category);
    setFeuds(res.feuds);
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const c = await api.categories();
        const favs = user?.favorite_categories || [];
        // Reorder: Tutte first, then favorites in the order the user picked them,
        // then the remaining categories.
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
        // Preselect the first favorite category if any, else "all"
        const initial = favs[0] && c.categories.some((x: any) => x.id === favs[0]) ? favs[0] : "all";
        setSelected(initial);
        await load(initial);
      } finally { setLoading(false); }
    })();
  }, [load, user?.favorite_categories]);

  const onSelect = async (id: string) => {
    setSelected(id); setLoading(true);
    setSearchOpen(false); setSearchQ("");
    try { await load(id); } finally { setLoading(false); }
  };

  const onRefresh = async () => {
    setRefreshing(true);
    try {
      if (searchQ.trim()) {
        const r = await api.search(searchQ.trim());
        setFeuds(r.feuds);
      } else {
        await load(selected);
      }
    } finally { setRefreshing(false); }
  };

  const runSearch = async (q: string) => {
    if (!q.trim()) { await load(selected); return; }
    setSearching(true);
    try {
      const r = await api.search(q.trim());
      setFeuds(r.feuds);
    } finally { setSearching(false); }
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]} testID="home-screen">
      <View style={styles.header}>
        <View style={styles.headerTop}>
          <View style={{ flex: 1 }}>
            <View style={styles.brandRow}>
              <Text style={styles.brand}>POPULUS</Text>
              <Image
                source={require("../../assets/images/icon.png")}
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

      <View style={styles.chipRowWrap}>
        <ScrollView
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
              style={[styles.chip, selected === c.id && styles.chipActive]}
            >
              <Text style={[styles.chipText, selected === c.id && styles.chipTextActive]}>
                {c.label.toUpperCase()}
              </Text>
            </Pressable>
          ))}
        </ScrollView>
      </View>

      {loading ? (
        <View style={styles.center} testID="home-loading">
          <ActivityIndicator size="large" color={colors.brandPrimary} />
        </View>
      ) : (
        <FlatList
          data={feuds}
          keyExtractor={(f) => f.feud_id}
          contentContainerStyle={{ padding: spacing.lg, paddingBottom: spacing.xxxl }}
          ItemSeparatorComponent={() => <View style={{ height: spacing.lg }} />}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.brandPrimary} />}
          ListEmptyComponent={
            <View style={styles.center} testID="home-empty">
              <Text style={styles.empty}>NESSUNA FAIDA IN QUESTA CATEGORIA.</Text>
            </View>
          }
          renderItem={({ item }) => (
            <FeudCard feud={item} onPress={() => router.push(`/feud/${item.feud_id}`)} />
          )}
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: { paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.md, borderBottomWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceInverse },
  headerTop: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  searchBtn: { width: 44, height: 44, borderWidth: 2, borderColor: colors.brandSecondary, alignItems: "center", justifyContent: "center", backgroundColor: colors.surfaceInverse },
  archiveBtn: { width: 44, height: 44, borderWidth: 2, borderColor: colors.brandSecondary, alignItems: "center", justifyContent: "center", backgroundColor: colors.surfaceInverse },
  searchWrap: { flexDirection: "row", alignItems: "center", marginTop: spacing.sm },
  searchInput: { flex: 1, borderWidth: 2, borderColor: colors.brandSecondary, backgroundColor: colors.surfaceInverse, color: colors.onSurfaceInverse, padding: spacing.sm, fontSize: font.sizes.base },
  brand: { color: colors.onSurfaceInverse, fontSize: font.sizes.xxxl, letterSpacing: 2, fontWeight: "500" },
  brandRow: { flexDirection: "row", alignItems: "center", gap: spacing.xs },
  brandLogo: { width: 32, height: 32 },
  date: { color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 2, marginTop: 2 },
  chipRowWrap: { height: 56, backgroundColor: colors.surfaceInverse, borderBottomWidth: 2, borderColor: colors.border },
  chipRowContent: { paddingHorizontal: spacing.lg, gap: spacing.sm, alignItems: "center" },
  chip: { height: 36, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: colors.brandSecondary, justifyContent: "center", alignItems: "center", flexShrink: 0, backgroundColor: colors.surfaceInverse },
  chipActive: { backgroundColor: colors.brandSecondary, borderColor: colors.brandSecondary },
  chipText: { color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 1 },
  chipTextActive: { color: colors.onBrandSecondary, fontWeight: "500" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xxl },
  empty: { fontSize: font.sizes.xl, color: colors.onSurface, letterSpacing: 1 },
});
