import React, { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  Modal,
  Pressable,
  ActivityIndicator,
  ScrollView,
  StyleSheet,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { api } from "@/src/api";
import { colors, font, spacing } from "@/src/theme";

type AiSummary = {
  side_a: string[];
  side_b: string[];
  common: string[];
  empty?: boolean;
  generated_at?: string;
  party_a?: string;
  party_b?: string;
};

type Props = {
  visible: boolean;
  feudId: string;
  partyA: string;
  partyB: string;
  onClose: () => void;
};

/**
 * "Sintesi del pensiero" — on-demand AI synthesis of the arguments each
 * faction is making, plus points of agreement when the AI finds them.
 *
 * A single tap on the primary action generates a fresh analysis from the
 * currently-visible comments; another tap re-runs it (comments arrive
 * continuously so each regeneration can sharpen the read on the room).
 */
export default function AiFactionSummaryModal({
  visible,
  feudId,
  partyA,
  partyB,
  onClose,
}: Props) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<AiSummary | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r: any = await api.feudAiSummary(feudId);
      setData(r);
    } catch (e: any) {
      setError(e?.detail || "Sintesi non disponibile. Riprova.");
    } finally {
      setLoading(false);
    }
  }, [feudId]);

  // Auto-run on first open. Subsequent opens keep the last result so the
  // user can compare before manually refreshing with the top-right icon.
  useEffect(() => {
    if (!visible) return;
    if (!data && !loading) load();
  }, [visible, data, loading, load]);

  const showEmpty = !loading && !error && data?.empty;
  const showResult = !loading && !error && data && !data.empty;

  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={onClose}
      testID="ai-summary-modal"
    >
      <View style={styles.backdrop}>
        <View style={styles.sheet}>
          <View style={styles.header}>
            <View style={{ flex: 1 }}>
              <Text style={styles.title}>SINTESI DEL PENSIERO</Text>
              <Text style={styles.subtitle}>
                {"Cosa sostiene ogni fazione, secondo l'IA"}
              </Text>
            </View>
            <Pressable
              onPress={load}
              disabled={loading}
              style={[styles.iconBtn, loading ? styles.iconBtnDisabled : null]}
              testID="ai-summary-refresh"
              hitSlop={6}
              accessibilityLabel="Rigenera sintesi"
            >
              <Ionicons name="refresh" size={20} color={colors.brandPrimary} />
            </Pressable>
            <Pressable
              onPress={onClose}
              style={styles.iconBtn}
              testID="ai-summary-close"
              hitSlop={6}
            >
              <Ionicons name="close" size={22} color={colors.onSurface} />
            </Pressable>
          </View>

          <ScrollView contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
            {loading ? (
              <View style={styles.center}>
                <ActivityIndicator color={colors.brandPrimary} />
                <Text style={styles.hint}>Analizzo i commenti…</Text>
              </View>
            ) : null}

            {error ? (
              <View style={styles.errorBox} testID="ai-summary-error">
                <Ionicons name="alert-circle" size={16} color={colors.error} />
                <Text style={styles.errorTxt}>{error}</Text>
              </View>
            ) : null}

            {showEmpty ? (
              <View style={styles.center} testID="ai-summary-empty">
                <Ionicons name="cafe-outline" size={40} color={colors.muted} />
                <Text style={styles.emptyTitle}>Ancora troppi pochi commenti</Text>
                <Text style={styles.emptyHint}>
                  {"Torna qui dopo un po' di discussione: l'IA avrà più materiale per una sintesi accurata."}
                </Text>
              </View>
            ) : null}

            {showResult ? (
              <>
                {/* Team A block */}
                <View style={[styles.block, styles.blockA]} testID="ai-summary-side-a">
                  <Text style={[styles.blockTitle, { color: colors.brandPrimary }]}>
                    TEAM {partyA?.toUpperCase() || "A"}
                  </Text>
                  {data!.side_a.length === 0 ? (
                    <Text style={styles.hint}>Nessuna argomentazione rilevata.</Text>
                  ) : (
                    data!.side_a.map((b, i) => (
                      <View key={`a-${i}`} style={styles.bulletRow}>
                        <Text style={[styles.bulletDot, { color: colors.brandPrimary }]}>●</Text>
                        <Text style={styles.bulletTxt}>{b}</Text>
                      </View>
                    ))
                  )}
                </View>

                {/* Team B block */}
                <View style={[styles.block, styles.blockB]} testID="ai-summary-side-b">
                  <Text style={[styles.blockTitle, { color: colors.brandSecondary }]}>
                    TEAM {partyB?.toUpperCase() || "B"}
                  </Text>
                  {data!.side_b.length === 0 ? (
                    <Text style={styles.hint}>Nessuna argomentazione rilevata.</Text>
                  ) : (
                    data!.side_b.map((b, i) => (
                      <View key={`b-${i}`} style={styles.bulletRow}>
                        <Text style={[styles.bulletDot, { color: colors.brandSecondary }]}>●</Text>
                        <Text style={styles.bulletTxt}>{b}</Text>
                      </View>
                    ))
                  )}
                </View>

                {/* Common ground — only rendered when the AI found some */}
                {data!.common.length > 0 ? (
                  <View style={[styles.block, styles.blockCommon]} testID="ai-summary-common">
                    <View style={styles.commonHeader}>
                      <Ionicons name="git-merge" size={16} color={colors.onSurface} />
                      <Text style={styles.blockTitle}>ENTRAMBE LE FAZIONI CONCORDANO</Text>
                    </View>
                    {data!.common.map((b, i) => (
                      <View key={`c-${i}`} style={styles.bulletRow}>
                        <Text style={styles.bulletDot}>●</Text>
                        <Text style={styles.bulletTxt}>{b}</Text>
                      </View>
                    ))}
                  </View>
                ) : null}

                <Text style={styles.footerHint}>
                  ⚠︎ Sintesi generata automaticamente dai commenti visibili.
                  Può contenere imprecisioni. Tap ↻ per rigenerare.
                </Text>
              </>
            ) : null}
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.5)",
    justifyContent: "flex-end",
  },
  sheet: {
    backgroundColor: colors.surface,
    maxHeight: "88%",
    borderTopWidth: 3,
    borderTopColor: colors.border,
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: 2,
    borderBottomColor: colors.border,
  },
  title: { color: colors.onSurface, fontSize: font.sizes.base, fontWeight: "700", letterSpacing: 1 },
  subtitle: { color: colors.muted, fontSize: font.sizes.xs, marginTop: 2 },
  iconBtn: { padding: 6 },
  iconBtnDisabled: { opacity: 0.4 },
  center: { alignItems: "center", justifyContent: "center", padding: spacing.lg, gap: spacing.sm },
  hint: { color: colors.muted, fontSize: font.sizes.sm, textAlign: "center" },
  emptyTitle: { color: colors.onSurface, fontSize: font.sizes.base, fontWeight: "600" },
  emptyHint: { color: colors.muted, fontSize: font.sizes.sm, textAlign: "center", lineHeight: 18 },
  block: {
    padding: spacing.md,
    borderWidth: 2,
    gap: spacing.sm,
  },
  blockA: { borderColor: colors.brandPrimary, backgroundColor: `${colors.brandPrimary}12` },
  blockB: { borderColor: colors.brandSecondary, backgroundColor: `${colors.brandSecondary}12` },
  blockCommon: { borderColor: colors.border, backgroundColor: colors.surfaceSecondary },
  commonHeader: { flexDirection: "row", alignItems: "center", gap: 6 },
  blockTitle: { color: colors.onSurface, fontSize: font.sizes.sm, fontWeight: "700", letterSpacing: 1 },
  bulletRow: { flexDirection: "row", gap: spacing.sm, paddingLeft: spacing.xs },
  bulletDot: { color: colors.onSurface, fontSize: font.sizes.base, lineHeight: 20 },
  bulletTxt: { color: colors.onSurface, fontSize: font.sizes.sm, lineHeight: 20, flex: 1 },
  errorBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    padding: spacing.sm,
    backgroundColor: `${colors.error}22`,
    borderWidth: 1,
    borderColor: colors.error,
  },
  errorTxt: { color: colors.error, fontSize: font.sizes.sm, flex: 1 },
  footerHint: {
    color: colors.muted,
    fontSize: font.sizes.xs,
    fontStyle: "italic",
    textAlign: "center",
    marginTop: spacing.sm,
    lineHeight: 16,
  },
});
