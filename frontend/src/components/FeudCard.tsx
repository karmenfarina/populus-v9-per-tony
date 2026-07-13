import { View, Text, StyleSheet, Pressable, ImageBackground } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Feud } from "@/src/api";
import { colors, spacing, font } from "@/src/theme";

export default function FeudCard({ feud, onPress, showArchivedBadge = false }: {
  feud: Feud;
  onPress: () => void;
  showArchivedBadge?: boolean;
}) {
  const revealed = feud.revealed;
  return (
    <Pressable style={styles.card} onPress={onPress} testID={`feud-card-${feud.feud_id}`}>
      <ImageBackground source={{ uri: feud.image_url }} style={styles.cardImage}>
        <LinearGradient
          colors={["rgba(0,0,0,0)", "rgba(0,0,0,0.85)"]}
          style={StyleSheet.absoluteFill}
        />
        {showArchivedBadge && (
          <View style={styles.archivedBadge} testID={`archived-${feud.feud_id}`}>
            <Text style={styles.archivedBadgeTxt}>ARCHIVIATA</Text>
          </View>
        )}
        <View style={styles.cardImageContent}>
          <Text style={styles.cardCat}>{feud.category_label.toUpperCase()}</Text>
          <Text style={styles.cardTitle} numberOfLines={3}>{feud.title}</Text>
        </View>
      </ImageBackground>
      <View style={styles.splitRow}>
        <View style={[styles.splitHalf, { backgroundColor: colors.brandPrimary }]}>
          {revealed && <Text style={styles.splitPct}>{feud.pct_a}%</Text>}
          <Text style={styles.splitLabel} numberOfLines={2}>{feud.party_a}</Text>
        </View>
        <View style={[styles.splitHalf, { backgroundColor: colors.brandSecondary }]}>
          {revealed && <Text style={[styles.splitPct, { color: colors.onBrandSecondary }]}>{feud.pct_b}%</Text>}
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
  card: { borderWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceSecondary },
  cardImage: { height: 200, justifyContent: "flex-end" },
  cardImageContent: { padding: spacing.md, gap: spacing.xs },
  cardCat: { color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 2 },
  cardTitle: { color: "#FFFFFF", fontSize: font.sizes.xxl, letterSpacing: 0.5, fontWeight: "500", lineHeight: 28 },
  splitRow: { flexDirection: "row", borderTopWidth: 2, borderColor: colors.border },
  splitHalf: { flex: 1, paddingVertical: spacing.md, alignItems: "center", justifyContent: "center" },
  splitPct: { color: colors.onBrandPrimary, fontSize: font.sizes.xxl, fontWeight: "500", letterSpacing: 1 },
  splitLabel: { color: colors.onBrandPrimary, fontSize: font.sizes.sm, letterSpacing: 1, textAlign: "center", marginTop: 2, paddingHorizontal: spacing.xs },
  cardFooter: { flexDirection: "row", justifyContent: "space-between", padding: spacing.sm, borderTopWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceInverse },
  cardFooterText: { color: colors.onSurfaceInverse, fontSize: font.sizes.sm, letterSpacing: 1 },
  archivedBadge: { position: "absolute", top: spacing.sm, right: spacing.sm, backgroundColor: colors.brandSecondary, paddingHorizontal: spacing.sm, paddingVertical: 4, borderWidth: 2, borderColor: colors.onSurface, zIndex: 2 },
  archivedBadgeTxt: { color: colors.onBrandSecondary, fontSize: font.sizes.xs, letterSpacing: 2, fontWeight: "500" },
});
