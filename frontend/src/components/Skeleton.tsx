import { useEffect, useRef } from "react";
import { Animated, StyleSheet, View, ViewStyle, StyleProp } from "react-native";
import { colors, radius, spacing } from "@/src/theme";

/**
 * Skeleton loader di base con shimmer sottile (fade-in/out
 * dell'opacità). Preferito rispetto a un ActivityIndicator perché
 * comunica meglio la struttura del contenuto in arrivo e riduce la
 * percezione di attesa.
 *
 * Nessuna dipendenza esterna: usa Animated.loop così restiamo su
 * driver nativo (useNativeDriver: true) e non tocchiamo il main JS.
 * Basso rischio: componente puro, non altera lo stato dei parent.
 */
export function SkeletonBlock({
  width,
  height,
  style,
  radius: r = radius.sm,
}: {
  width?: number | `${number}%`;
  height?: number;
  style?: StyleProp<ViewStyle>;
  radius?: number;
}) {
  const opacity = useRef(new Animated.Value(0.5)).current;
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, {
          toValue: 1,
          duration: 700,
          useNativeDriver: true,
        }),
        Animated.timing(opacity, {
          toValue: 0.5,
          duration: 700,
          useNativeDriver: true,
        }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [opacity]);
  return (
    <Animated.View
      style={[
        {
          width: (width as any) ?? "100%",
          height: height ?? 12,
          borderRadius: r,
          backgroundColor: colors.surfaceTertiary,
          opacity,
        },
        style,
      ]}
    />
  );
}

/**
 * Skeleton per una card di feud, allineato al layout di FeudCard:
 * area immagine + titolo + sotto-riga + due barre laterali (pct).
 */
export function FeudCardSkeleton() {
  return (
    <View style={styles.card}>
      <SkeletonBlock height={180} radius={radius.md} style={{ marginBottom: spacing.md }} />
      <SkeletonBlock width={"90%"} height={18} style={{ marginBottom: spacing.sm }} />
      <SkeletonBlock width={"60%"} height={14} style={{ marginBottom: spacing.md }} />
      <View style={styles.row}>
        <SkeletonBlock width={"48%"} height={28} radius={radius.sm} />
        <SkeletonBlock width={"48%"} height={28} radius={radius.sm} />
      </View>
    </View>
  );
}

/** Placeholder list — n righe di FeudCardSkeleton. */
export function FeudListSkeleton({ count = 3 }: { count?: number }) {
  return (
    <View style={{ paddingHorizontal: spacing.lg, paddingTop: spacing.md }}>
      {Array.from({ length: count }).map((_, i) => (
        <FeudCardSkeleton key={i} />
      ))}
    </View>
  );
}

/** Skeleton per una riga di notifica (avatar + due righe di testo). */
export function NotificationRowSkeleton() {
  return (
    <View style={styles.notifRow}>
      <SkeletonBlock width={40} height={40} radius={20} />
      <View style={{ flex: 1, marginLeft: spacing.md }}>
        <SkeletonBlock width={"80%"} height={14} style={{ marginBottom: 6 }} />
        <SkeletonBlock width={"40%"} height={12} />
      </View>
    </View>
  );
}

export function NotificationListSkeleton({ count = 6 }: { count?: number }) {
  return (
    <View style={{ paddingTop: spacing.sm }}>
      {Array.from({ length: count }).map((_, i) => (
        <NotificationRowSkeleton key={i} />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    marginBottom: spacing.lg,
    padding: spacing.md,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
  },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
  },
  notifRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
});
