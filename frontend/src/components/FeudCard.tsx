import { memo } from "react";
import { View, Text, StyleSheet, Pressable } from "react-native";
import { Image } from "expo-image";
import { LinearGradient } from "expo-linear-gradient";
import { Feud } from "@/src/api";
import { colors, spacing, font, radius } from "@/src/theme";

function formatRelativeTime(iso?: string): string {
  if (!iso) return "";
  // Backend sometimes returns naive ISO (no Z / offset) but the value is
  // actually UTC. Force UTC interpretation when timezone info is missing.
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(iso);
  const normalized = hasTz ? iso : `${iso}Z`;
  const then = new Date(normalized).getTime();
  if (isNaN(then)) return "";
  const diffSec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (diffSec < 60) return "ORA";
  const min = Math.floor(diffSec / 60);
  if (min < 60) return `${min} MIN FA`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} H FA`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d} G FA`;
  // Older than a week → show absolute short date (DD/MM)
  const dt = new Date(iso);
  const dd = String(dt.getDate()).padStart(2, "0");
  const mm = String(dt.getMonth() + 1).padStart(2, "0");
  return `${dd}/${mm}`;
}

export default memo(function FeudCard({ feud, onPress, showArchivedBadge = false }: {
  feud: Feud;
  onPress: () => void;
  /** Kept for backwards compatibility. Since the "ARCHIVIATA" tag was replaced
   *  with the plain absolute date, this flag only forces the DD/MM date badge
   *  regardless of how recent the feud is. */
  showArchivedBadge?: boolean;
}) {
  const revealed = feud.revealed;
  const timeLabel = formatRelativeTime(feud.created_at);
  // When the caller signals "this is an old/archived post" (e.g. from the
  // archive tab), render the short absolute date (DD/MM) instead of the
  // relative time. For very recent items we still fall back to timeLabel.
  const dateOnly = (() => {
    if (!feud.created_at) return timeLabel;
    const dt = new Date(/[zZ]$|[+-]\d{2}:?\d{2}$/.test(feud.created_at) ? feud.created_at : `${feud.created_at}Z`);
    if (isNaN(dt.getTime())) return timeLabel;
    const dd = String(dt.getDate()).padStart(2, "0");
    const mm = String(dt.getMonth() + 1).padStart(2, "0");
    return `${dd}/${mm}`;
  })();
  const badgeLabel = showArchivedBadge ? dateOnly : timeLabel;
  return (
    <Pressable style={styles.card} onPress={onPress} testID={`feud-card-${feud.feud_id}`}>
      <View style={styles.cardImage}>
        {/* expo-image: cache disco+memoria automatica, decoding progressivo,
            trasparente per view background. */}
        <Image
          source={{ uri: feud.image_url }}
          style={StyleSheet.absoluteFill}
          contentFit="cover"
          transition={150}
          cachePolicy="memory-disk"
          recyclingKey={feud.feud_id}
        />
        <LinearGradient
          colors={["rgba(0,0,0,0)", "rgba(0,0,0,0.85)"]}
          style={StyleSheet.absoluteFill}
        />
        {badgeLabel ? (
          <View style={styles.timeBadge} testID={`feud-time-${feud.feud_id}`}>
            <Text style={styles.timeBadgeTxt}>{badgeLabel}</Text>
          </View>
        ) : null}
        <View style={styles.cardImageContent}>
          {/* `category_label` should always be set by the backend but we
              guard defensively: if any older / manually-inserted feud
              is missing the label, we fall back to the raw `category`
              id upper-cased instead of crashing the entire feed. */}
          <Text style={styles.cardCat}>
            {(feud.category_label || feud.category || "").toString().toUpperCase()}
          </Text>
          <Text style={styles.cardTitle} numberOfLines={3}>{feud.title}</Text>
        </View>
      </View>
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
        <Text style={styles.cardFooterText}>
          {revealed
            ? `${feud.total_votes} VOTI`
            : (feud.total_votes > 0
                ? `${feud.total_votes} VOTI · VOTA PER VEDERE %`
                : "VOTA PER VEDERE")}
          {typeof feud.hype_comments === "number" && feud.hype_comments > 0
            ? `  ·  💬 ${feud.hype_comments}`
            : ""}
        </Text>
        <Text style={styles.cardFooterOpen}>APRI ›</Text>
      </View>
    </Pressable>
  );
}, (prev, next) => {
  // Ricorda: memo evita re-render se le prop non cambiano. Confrontiamo
  // solo i campi rilevanti per il rendering (evita comparazioni profonde).
  const a = prev.feud, b = next.feud;
  return (
    prev.onPress === next.onPress &&
    prev.showArchivedBadge === next.showArchivedBadge &&
    a.feud_id === b.feud_id &&
    a.image_url === b.image_url &&
    a.title === b.title &&
    a.party_a === b.party_a &&
    a.party_b === b.party_b &&
    a.category_label === b.category_label &&
    a.revealed === b.revealed &&
    a.total_votes === b.total_votes &&
    a.pct_a === b.pct_a &&
    a.pct_b === b.pct_b &&
    a.created_at === b.created_at &&
    a.hype_comments === b.hype_comments
  );
});

const styles = StyleSheet.create({
  card: {
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    overflow: "hidden",
  },
  cardImage: { height: 210, justifyContent: "flex-end", position: "relative" },
  cardImageContent: { padding: spacing.md, gap: spacing.xs },
  cardCat: {
    color: colors.brandSecondary,
    fontSize: font.sizes.xs,
    letterSpacing: 2,
    fontWeight: "700",
  },
  cardTitle: {
    color: "#FFFFFF",
    fontSize: font.sizes.xxl,
    letterSpacing: 0.3,
    fontWeight: "800",
    lineHeight: 30,
  },
  splitRow: { flexDirection: "row" },
  splitHalf: {
    flex: 1,
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.sm,
    alignItems: "center",
    justifyContent: "center",
    minHeight: 56,
  },
  splitPct: {
    color: colors.onBrandPrimary,
    fontSize: font.sizes.xl,
    fontWeight: "800",
    letterSpacing: 0.5,
  },
  splitLabel: {
    color: colors.onBrandPrimary,
    fontSize: font.sizes.sm,
    letterSpacing: 0.3,
    textAlign: "center",
    marginTop: 2,
    paddingHorizontal: spacing.xs,
    fontWeight: "600",
  },
  cardFooter: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm + 2,
    backgroundColor: colors.surfaceInverse,
  },
  cardFooterText: {
    color: colors.muted,
    fontSize: font.sizes.sm,
    letterSpacing: 1,
    fontWeight: "600",
  },
  cardFooterOpen: {
    color: colors.brandSecondary,
    fontSize: font.sizes.sm,
    letterSpacing: 1,
    fontWeight: "700",
  },
  archivedBadge: { position: "absolute", top: spacing.sm, right: spacing.sm, backgroundColor: colors.brandSecondary, paddingHorizontal: spacing.sm, paddingVertical: 4, borderRadius: radius.sm, zIndex: 2 },
  archivedBadgeTxt: { color: colors.onBrandSecondary, fontSize: font.sizes.xs, letterSpacing: 2, fontWeight: "800" },
  timeBadge: {
    position: "absolute",
    top: spacing.sm,
    right: spacing.sm,
    backgroundColor: "rgba(0,0,0,0.65)",
    paddingHorizontal: spacing.sm + 2,
    paddingVertical: 4,
    borderRadius: radius.sm,
    zIndex: 2,
  },
  timeBadgeTxt: {
    color: "#FFFFFF",
    fontSize: font.sizes.xs,
    letterSpacing: 1,
    fontWeight: "700",
  },
});
