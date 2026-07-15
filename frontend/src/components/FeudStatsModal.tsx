import React, { useCallback, useEffect, useState } from "react";
import {
  View, Text, StyleSheet, Modal, Pressable, ScrollView, ActivityIndicator,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { api, FeudStats } from "@/src/api";
import { colors, spacing, font } from "@/src/theme";

type Props = {
  visible: boolean;
  feudId: string;
  partyA: string;
  partyB: string;
  onClose: () => void;
};

const AGE_ORDER = ["13-17", "18-24", "25-34", "35-44", "45-54", "55-64", "65+"];
const REGION_ORDER: ("Nord" | "Centro" | "Sud")[] = ["Nord", "Centro", "Sud"];
const SEX_ORDER: { key: "F" | "M" | "other"; label: string }[] = [
  { key: "F", label: "Donne" },
  { key: "M", label: "Uomini" },
  { key: "other", label: "Altro" },
];

function pct(n: number, total: number): number {
  if (!total) return 0;
  return Math.round((n / total) * 100);
}

function BarRow({
  label,
  aCount,
  bCount,
  aColor,
  bColor,
}: { label: string; aCount: number; bCount: number; aColor: string; bColor: string }) {
  const total = aCount + bCount;
  const aPct = pct(aCount, total);
  const bPct = pct(bCount, total);
  return (
    <View style={styles.row}>
      <View style={styles.labelBox}>
        <Text style={styles.label}>{label}</Text>
        <Text style={styles.total}>{total}</Text>
      </View>
      <View style={styles.bar}>
        {total > 0 ? (
          <>
            {aCount > 0 && (
              <View style={[styles.barFill, { flex: aCount, backgroundColor: aColor }]}>
                {aPct >= 15 && <Text style={styles.barTxt}>{aPct}%</Text>}
              </View>
            )}
            {bCount > 0 && (
              <View style={[styles.barFill, { flex: bCount, backgroundColor: bColor }]}>
                {bPct >= 15 && <Text style={styles.barTxt}>{bPct}%</Text>}
              </View>
            )}
          </>
        ) : (
          <View style={[styles.barFill, { flex: 1, backgroundColor: colors.border }]}>
            <Text style={styles.barEmptyTxt}>—</Text>
          </View>
        )}
      </View>
    </View>
  );
}

export default function FeudStatsModal({ visible, feudId, partyA, partyB, onClose }: Props) {
  const [stats, setStats] = useState<FeudStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const s = await api.feudStats(feudId);
      setStats(s);
    } catch (e: any) {
      setError(e?.detail || e?.message || "Errore nel caricamento delle statistiche.");
    } finally {
      setLoading(false);
    }
  }, [feudId]);

  // Refresh EVERY time the modal opens (real-time contract).
  useEffect(() => {
    if (visible) load();
    else { setStats(null); setError(null); }
  }, [visible, load]);

  const A = stats?.sides.A;
  const B = stats?.sides.B;

  return (
    <Modal
      animationType="slide"
      transparent
      visible={visible}
      onRequestClose={onClose}
    >
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable style={styles.sheet} onPress={() => { /* consume */ }}>
          <View style={styles.handleWrap}><View style={styles.handle} /></View>
          <View style={styles.header}>
            <View style={{ flex: 1 }}>
              <Text style={styles.title}>STATISTICHE VOTO</Text>
              <Text style={styles.subtitle}>{stats ? `${stats.total_votes} voti totali` : ""}</Text>
            </View>
            <Pressable onPress={onClose} testID="stats-close" hitSlop={8}>
              <Ionicons name="close" size={24} color={colors.onSurface} />
            </Pressable>
          </View>

          {loading && (
            <View style={styles.centerBox}>
              <ActivityIndicator size="large" color={colors.brandPrimary} />
            </View>
          )}

          {!loading && error && (
            <View style={styles.centerBox}>
              <Ionicons name="alert-circle-outline" size={40} color={colors.muted} />
              <Text style={styles.errTxt}>{error}</Text>
              <Pressable onPress={load} style={styles.retry} testID="stats-retry">
                <Text style={styles.retryTxt}>RIPROVA</Text>
              </Pressable>
            </View>
          )}

          {!loading && !error && stats && A && B && (
            <ScrollView contentContainerStyle={styles.body} showsVerticalScrollIndicator={false}>
              <View style={styles.legend}>
                <View style={styles.legendItem}>
                  <View style={[styles.legendDot, { backgroundColor: colors.brandPrimary }]} />
                  <Text style={styles.legendLabel} numberOfLines={1}>
                    {partyA}
                  </Text>
                  <Text style={styles.legendCount}>{A.total}</Text>
                </View>
                <View style={styles.legendItem}>
                  <View style={[styles.legendDot, { backgroundColor: colors.brandSecondary }]} />
                  <Text style={styles.legendLabel} numberOfLines={1}>
                    {partyB}
                  </Text>
                  <Text style={styles.legendCount}>{B.total}</Text>
                </View>
              </View>

              <Text style={styles.section}>ETÀ</Text>
              {AGE_ORDER.map((k) => (
                <BarRow
                  key={k}
                  label={k}
                  aCount={A.age[k] || 0}
                  bCount={B.age[k] || 0}
                  aColor={colors.brandPrimary}
                  bColor={colors.brandSecondary}
                />
              ))}
              {((A.age.unknown || 0) + (B.age.unknown || 0)) > 0 && (
                <BarRow
                  label="N/D"
                  aCount={A.age.unknown || 0}
                  bCount={B.age.unknown || 0}
                  aColor={colors.brandPrimary}
                  bColor={colors.brandSecondary}
                />
              )}

              <Text style={styles.section}>PROVENIENZA</Text>
              {REGION_ORDER.map((k) => (
                <BarRow
                  key={k}
                  label={k}
                  aCount={A.region[k] || 0}
                  bCount={B.region[k] || 0}
                  aColor={colors.brandPrimary}
                  bColor={colors.brandSecondary}
                />
              ))}
              {((A.region.unknown || 0) + (B.region.unknown || 0)) > 0 && (
                <BarRow
                  label="N/D"
                  aCount={A.region.unknown || 0}
                  bCount={B.region.unknown || 0}
                  aColor={colors.brandPrimary}
                  bColor={colors.brandSecondary}
                />
              )}

              <Text style={styles.section}>GENERE</Text>
              {SEX_ORDER.map(({ key, label }) => (
                <BarRow
                  key={key}
                  label={label}
                  aCount={A.sex[key] || 0}
                  bCount={B.sex[key] || 0}
                  aColor={colors.brandPrimary}
                  bColor={colors.brandSecondary}
                />
              ))}
              {((A.sex.unknown || 0) + (B.sex.unknown || 0)) > 0 && (
                <BarRow
                  label="N/D"
                  aCount={A.sex.unknown || 0}
                  bCount={B.sex.unknown || 0}
                  aColor={colors.brandPrimary}
                  bColor={colors.brandSecondary}
                />
              )}

              <View style={{ height: spacing.xl }} />
            </ScrollView>
          )}
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.55)", justifyContent: "flex-end" },
  sheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    maxHeight: "85%",
    minHeight: "60%",
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    paddingBottom: spacing.md,
  },
  handleWrap: { alignItems: "center", paddingVertical: spacing.xs },
  handle: { width: 44, height: 4, borderRadius: 2, backgroundColor: colors.border },
  header: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: spacing.sm,
    borderBottomWidth: 2,
    borderColor: colors.border,
    marginBottom: spacing.sm,
  },
  title: { color: colors.onSurface, fontSize: font.sizes.xl, letterSpacing: 2, fontWeight: "500" },
  subtitle: { color: colors.muted, fontSize: font.sizes.sm, marginTop: 2 },
  centerBox: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl, gap: spacing.md, minHeight: 200 },
  errTxt: { color: colors.muted, fontSize: font.sizes.base, textAlign: "center" },
  retry: { borderWidth: 2, borderColor: colors.brandPrimary, paddingHorizontal: spacing.lg, paddingVertical: spacing.sm, backgroundColor: colors.brandPrimary },
  retryTxt: { color: colors.onBrandPrimary, letterSpacing: 2, fontWeight: "500" },
  body: { paddingTop: spacing.xs },
  legend: {
    flexDirection: "row", gap: spacing.md,
    padding: spacing.sm,
    borderWidth: 2, borderColor: colors.border,
    backgroundColor: colors.surfaceSecondary,
    marginBottom: spacing.md,
  },
  legendItem: { flex: 1, flexDirection: "row", alignItems: "center", gap: 6 },
  legendDot: { width: 12, height: 12, borderRadius: 6 },
  legendLabel: { flex: 1, color: colors.onSurface, fontSize: font.sizes.sm, fontWeight: "500" },
  legendCount: { color: colors.muted, fontSize: font.sizes.sm, fontWeight: "500" },
  section: {
    color: colors.brandPrimary, fontSize: font.sizes.sm, letterSpacing: 2, fontWeight: "500",
    marginTop: spacing.md, marginBottom: spacing.xs,
  },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm, marginBottom: spacing.xs },
  labelBox: { width: 78, flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  label: { color: colors.onSurface, fontSize: font.sizes.sm, fontWeight: "500" },
  total: { color: colors.muted, fontSize: font.sizes.xs },
  bar: { flex: 1, height: 22, flexDirection: "row", borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surfaceSecondary },
  barFill: { alignItems: "center", justifyContent: "center" },
  barTxt: { color: "#FFFFFF", fontSize: font.sizes.xs, fontWeight: "600" },
  barEmptyTxt: { color: colors.muted, fontSize: font.sizes.xs },
});
