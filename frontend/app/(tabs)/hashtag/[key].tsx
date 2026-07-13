import { useCallback, useEffect, useState } from "react";
import { View, Text, StyleSheet, FlatList, ActivityIndicator, Pressable } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { api, Feud } from "@/src/api";
import { colors, spacing, font } from "@/src/theme";
import FeudCard from "@/src/components/FeudCard";

export default function HashtagSearch() {
  const router = useRouter();
  const { key } = useLocalSearchParams<{ key: string }>();
  const [feuds, setFeuds] = useState<Feud[]>([]);
  const [display, setDisplay] = useState<string>("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!key) return;
    setLoading(true);
    try {
      const r = await api.hashtag(key);
      setFeuds(r.feuds || []);
      setDisplay(r.hashtag_display || `#${key}`);
    } catch { setFeuds([]); }
    finally { setLoading(false); }
  }, [key]);

  useEffect(() => { load(); }, [load]);

  return (
    <SafeAreaView style={styles.safe} edges={["top"]} testID="hashtag-screen">
      <View style={styles.header}>
        <View style={styles.headerTop}>
          <Pressable onPress={() => router.back()} testID="hashtag-back" style={styles.backBtn}>
            <Ionicons name="chevron-back" size={22} color={colors.brandSecondary} />
          </Pressable>
          <View style={{ flex: 1 }}>
            <Text style={styles.brand} numberOfLines={1}>{display}</Text>
            <Text style={styles.subtitle}>
              {loading ? "Ricerca in corso…" : `${feuds.length} ${feuds.length === 1 ? "faida" : "faide"} trovate`}
            </Text>
          </View>
        </View>
      </View>

      {loading ? (
        <View style={styles.center}><ActivityIndicator size="large" color={colors.brandPrimary} /></View>
      ) : feuds.length === 0 ? (
        <View style={styles.center} testID="hashtag-empty">
          <Ionicons name="pricetag-outline" size={48} color={colors.muted} />
          <Text style={styles.emptyTxt}>Nessuna faida con questo hashtag.</Text>
        </View>
      ) : (
        <FlatList
          data={feuds}
          keyExtractor={(f) => f.feud_id}
          contentContainerStyle={styles.list}
          renderItem={({ item }) => (
            <FeudCard
              feud={item}
              onPress={() => router.push(`/feud/${item.feud_id}`)}
              showArchivedBadge={false}
            />
          )}
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderBottomWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceInverse },
  headerTop: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  backBtn: { width: 40, height: 40, borderWidth: 2, borderColor: colors.brandSecondary, alignItems: "center", justifyContent: "center", backgroundColor: colors.surfaceInverse },
  brand: { color: colors.brandSecondary, fontSize: font.sizes.xxxl, letterSpacing: 1, fontWeight: "500" },
  subtitle: { color: colors.onSurfaceInverse, fontSize: font.sizes.sm, letterSpacing: 1, marginTop: 2 },
  list: { padding: spacing.md, gap: spacing.md, paddingBottom: spacing.xxxl },
  center: { flex: 1, alignItems: "center", justifyContent: "center", gap: spacing.md, padding: spacing.xl },
  emptyTxt: { color: colors.muted, fontSize: font.sizes.base, textAlign: "center" },
});
