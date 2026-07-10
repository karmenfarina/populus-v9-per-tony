import { useCallback, useEffect, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, FlatList, RefreshControl,
  ActivityIndicator, ImageBackground,
} from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { api, Feud } from "@/src/api";
import { colors, spacing, font } from "@/src/theme";

const ALL_CAT = { id: "all", label: "Tutte" };

export default function HomeFeed() {
  const router = useRouter();
  const [cats, setCats] = useState<{ id: string; label: string }[]>([ALL_CAT]);
  const [selected, setSelected] = useState<string>("all");
  const [feuds, setFeuds] = useState<Feud[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (category: string) => {
    const res = await api.feuds(category);
    setFeuds(res.feuds);
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const c = await api.categories();
        setCats([ALL_CAT, ...c.categories]);
        await load("all");
      } finally { setLoading(false); }
    })();
  }, [load]);

  const onSelect = async (id: string) => {
    setSelected(id); setLoading(true);
    try { await load(id); } finally { setLoading(false); }
  };

  const onRefresh = async () => {
    setRefreshing(true);
    try { await load(selected); } finally { setRefreshing(false); }
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]} testID="home-screen">
      <View style={styles.header}>
        <Text style={styles.brand}>POPULUS</Text>
        <Text style={styles.date}>{new Date().toLocaleDateString("it-IT", { day: "numeric", month: "long" }).toUpperCase()}</Text>
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

function FeudCard({ feud, onPress }: { feud: Feud; onPress: () => void }) {
  const revealed = feud.revealed;
  return (
    <Pressable style={styles.card} onPress={onPress} testID={`feud-card-${feud.feud_id}`}>
      <ImageBackground source={{ uri: feud.image_url }} style={styles.cardImage}>
        <LinearGradient
          colors={["rgba(0,0,0,0)", "rgba(0,0,0,0.85)"]}
          style={StyleSheet.absoluteFill}
        />
        <View style={styles.cardImageContent}>
          <Text style={styles.cardCat}>{feud.category_label.toUpperCase()}</Text>
          <Text style={styles.cardTitle} numberOfLines={3}>{feud.title}</Text>
        </View>
      </ImageBackground>
      <View style={styles.splitRow}>
        <View style={[styles.splitHalf, { backgroundColor: colors.brandPrimary }]}>
          <Text style={styles.splitPct}>{revealed ? `${feud.pct_a}%` : "?"}</Text>
          <Text style={styles.splitLabel} numberOfLines={2}>{feud.party_a}</Text>
        </View>
        <View style={[styles.splitHalf, { backgroundColor: colors.brandSecondary }]}>
          <Text style={[styles.splitPct, { color: colors.onBrandSecondary }]}>{revealed ? `${feud.pct_b}%` : "?"}</Text>
          <Text style={[styles.splitLabel, { color: colors.onBrandSecondary }]} numberOfLines={2}>{feud.party_b}</Text>
        </View>
      </View>
      <View style={styles.cardFooter}>
        <Text style={styles.cardFooterText}>{revealed ? `${feud.total_votes} VOTI` : "VOTA PER VEDERE"}</Text>
        <Text style={styles.cardFooterText}>APRI ›</Text>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: { paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.md, borderBottomWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceInverse },
  brand: { color: colors.onSurfaceInverse, fontSize: font.sizes.xxxl, letterSpacing: 2, fontWeight: "500" },
  date: { color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 2, marginTop: 2 },
  chipRowWrap: { height: 56, backgroundColor: colors.surfaceInverse, borderBottomWidth: 2, borderColor: colors.border },
  chipRowContent: { paddingHorizontal: spacing.lg, gap: spacing.sm, alignItems: "center" },
  chip: { height: 36, paddingHorizontal: spacing.md, borderWidth: 2, borderColor: colors.brandSecondary, justifyContent: "center", alignItems: "center", flexShrink: 0, backgroundColor: colors.surfaceInverse },
  chipActive: { backgroundColor: colors.brandSecondary, borderColor: colors.brandSecondary },
  chipText: { color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 1 },
  chipTextActive: { color: colors.onBrandSecondary, fontWeight: "500" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xxl },
  empty: { fontSize: font.sizes.xl, color: colors.onSurface, letterSpacing: 1 },
  card: { borderWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceSecondary },
  cardImage: { height: 200, justifyContent: "flex-end" },
  cardImageContent: { padding: spacing.md, gap: spacing.xs },
  cardCat: { color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 2 },
  cardTitle: { color: "#FFFFFF", fontSize: font.sizes.xxl, letterSpacing: 0.5, fontWeight: "500", lineHeight: 28 },
  splitRow: { flexDirection: "row", borderTopWidth: 2, borderColor: colors.border },
  splitHalf: { flex: 1, paddingVertical: spacing.md, alignItems: "center" },
  splitPct: { color: colors.onBrandPrimary, fontSize: font.sizes.xxl, fontWeight: "500", letterSpacing: 1 },
  splitLabel: { color: colors.onBrandPrimary, fontSize: font.sizes.sm, letterSpacing: 1, textAlign: "center", marginTop: 2, paddingHorizontal: spacing.xs },
  cardFooter: { flexDirection: "row", justifyContent: "space-between", padding: spacing.sm, borderTopWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceInverse },
  cardFooterText: { color: colors.onSurfaceInverse, fontSize: font.sizes.sm, letterSpacing: 1 },
});
