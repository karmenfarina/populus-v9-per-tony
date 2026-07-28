import React, { useCallback, useEffect, useRef, useState } from "react";
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
import { colors, font, spacing, radius } from "@/src/theme";

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
  // Timestamp (ms) of the last successful fetch. Used to decide whether
  // to reuse the cached result on the next modal open or refetch. Kept
  // in a ref because we don't need it to trigger re-renders.
  const lastFetchedAtRef = useRef<number>(0);
  // Cache TTL: 60 seconds. Under this threshold the modal reopens
  // instantly with the previous result (fast reading, no LLM latency).
  // Beyond it, we auto-refresh so slow re-openers still see a fresh
  // synthesis. The user can always force a refresh via the ↻ button.
  const CACHE_TTL_MS = 60_000;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r: any = await api.feudAiSummary(feudId);
      setData(r);
      lastFetchedAtRef.current = Date.now();
    } catch (e: any) {
      setError(e?.detail || "Sintesi non disponibile. Riprova.");
    } finally {
      setLoading(false);
    }
  }, [feudId]);

  // Smart auto-refresh on open:
  //   • First open (no cache) → fetch.
  //   • Cache older than CACHE_TTL_MS → fetch.
  //   • Otherwise keep the cached result so re-reading is instant.
  // The manual ↻ button always forces a fresh call regardless of cache.
  useEffect(() => {
    if (!visible) return;
    const age = Date.now() - lastFetchedAtRef.current;
    const stale = !data || age > CACHE_TTL_MS;
    if (stale && !loading) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, feudId]);

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
          <View style={styles.handleWrap}><View style={styles.handle} /></View>
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
              style={[styles.headerBtn, styles.headerBtnRefresh, loading ? styles.iconBtnDisabled : null]}
              testID="ai-summary-refresh"
              hitSlop={6}
              accessibilityLabel="Rigenera sintesi"
            >
              <Ionicons name="refresh" size={18} color={colors.brandPrimary} />
            </Pressable>
            <Pressable
              onPress={onClose}
              style={[styles.headerBtn, styles.headerBtnClose]}
              testID="ai-summary-close"
              hitSlop={6}
            >
              <Ionicons name="close" size={20} color={colors.onSurface} />
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
                <View style={styles.factionRow} testID="ai-summary-side-a">
                  <View style={[styles.factionAccent, { backgroundColor: colors.brandPrimary }]} />
                  <View style={[styles.factionIcon, { backgroundColor: `${colors.brandPrimary}22`, borderColor: `${colors.brandPrimary}55` }]}>
                    <Ionicons name="person-outline" size={22} color={colors.brandPrimary} />
                  </View>
                  <View style={styles.factionBody}>
                    <Text style={[styles.factionTitle, { color: colors.brandPrimary }]} numberOfLines={2}>
                      {"FAZIONE ROSSA · "}
                      <Text style={styles.factionQuote}>{`"${partyA || "A"}"`}</Text>
                    </Text>
                    {data!.side_a.length === 0 ? (
                      <Text style={styles.hint}>Nessuna argomentazione rilevata.</Text>
                    ) : (
                      data!.side_a.map((b, i) => (
                        <Text key={`a-${i}`} style={styles.factionText}>{b}</Text>
                      ))
                    )}
                  </View>
                </View>

                <View style={styles.factionDivider} />

                {/* Team B block */}
                <View style={styles.factionRow} testID="ai-summary-side-b">
                  <View style={[styles.factionAccent, { backgroundColor: colors.brandSecondary }]} />
                  <View style={[styles.factionIcon, { backgroundColor: `${colors.brandSecondary}22`, borderColor: `${colors.brandSecondary}55` }]}>
                    <Ionicons name="person-outline" size={22} color={colors.brandSecondary} />
                  </View>
                  <View style={styles.factionBody}>
                    <Text style={[styles.factionTitle, { color: colors.brandSecondary }]} numberOfLines={2}>
                      {"FAZIONE GIALLA · "}
                      <Text style={styles.factionQuote}>{`"${partyB || "B"}"`}</Text>
                    </Text>
                    {data!.side_b.length === 0 ? (
                      <Text style={styles.hint}>Nessuna argomentazione rilevata.</Text>
                    ) : (
                      data!.side_b.map((b, i) => (
                        <Text key={`b-${i}`} style={styles.factionText}>{b}</Text>
                      ))
                    )}
                  </View>
                </View>

                {/* Common ground — only rendered when the AI found some */}
                {data!.common.length > 0 ? (
                  <>
                    <View style={styles.factionDivider} />
                    <View style={styles.factionRow} testID="ai-summary-common">
                      <View style={styles.commonIconWrap}>
                        <Ionicons name="checkmark-circle-outline" size={26} color={colors.onSurface} />
                      </View>
                      <View style={[styles.factionBody, { paddingLeft: 0 }]}>
                        <Text style={styles.commonTitle}>PUNTO IN COMUNE</Text>
                        {data!.common.map((b, i) => (
                          <Text key={`c-${i}`} style={styles.commonText}>{b}</Text>
                        ))}
                      </View>
                    </View>
                  </>
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
    backgroundColor: "rgba(0,0,0,0.55)",
    justifyContent: "flex-end",
  },
  sheet: {
    backgroundColor: colors.surface,
    maxHeight: "88%",
    borderTopLeftRadius: radius.xl,
    borderTopRightRadius: radius.xl,
  },
  handleWrap: { alignItems: "center", paddingVertical: spacing.xs },
  handle: { width: 44, height: 4, borderRadius: 2, backgroundColor: colors.muted, opacity: 0.5 },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.md,
  },
  title: { color: colors.onSurface, fontSize: font.sizes.lg, fontWeight: "800", letterSpacing: 1 },
  subtitle: { color: colors.muted, fontSize: font.sizes.sm, marginTop: 2 },
  headerBtn: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1.5,
  },
  headerBtnRefresh: { borderColor: colors.brandPrimary, backgroundColor: "transparent" },
  headerBtnClose: { borderColor: "transparent", backgroundColor: colors.surfaceTertiary },
  iconBtnDisabled: { opacity: 0.4 },
  center: { alignItems: "center", justifyContent: "center", padding: spacing.lg, gap: spacing.sm },
  hint: { color: colors.muted, fontSize: font.sizes.sm, textAlign: "center" },
  emptyTitle: { color: colors.onSurface, fontSize: font.sizes.base, fontWeight: "700" },
  emptyHint: { color: colors.muted, fontSize: font.sizes.sm, textAlign: "center", lineHeight: 18 },

  // ---- Faction blocks: horizontal layout with left color bar + icon + text
  factionRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing.md,
    paddingVertical: spacing.sm,
  },
  factionAccent: {
    width: 3,
    borderRadius: 2,
    alignSelf: "stretch",
    minHeight: 60,
  },
  factionIcon: {
    width: 44,
    height: 44,
    borderRadius: radius.sm,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  factionBody: { flex: 1, paddingLeft: spacing.xs, gap: 6 },
  factionTitle: {
    fontSize: font.sizes.sm,
    fontWeight: "800",
    letterSpacing: 1,
    lineHeight: 18,
  },
  factionQuote: {
    color: colors.onSurface,
    fontWeight: "700",
    letterSpacing: 0.3,
  },
  factionText: {
    color: colors.muted,
    fontSize: font.sizes.sm,
    lineHeight: 20,
  },
  factionDivider: {
    height: StyleSheet.hairlineWidth,
    backgroundColor: colors.border,
    marginVertical: spacing.xs,
  },

  // ---- Common ground: check icon + title + italic body
  commonIconWrap: {
    width: 44,
    height: 44,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    alignItems: "center",
    justifyContent: "center",
    marginLeft: 3, // align with the accent bar of the faction rows
  },
  commonTitle: {
    color: colors.onSurface,
    fontSize: font.sizes.sm,
    fontWeight: "800",
    letterSpacing: 1,
  },
  commonText: {
    color: colors.muted,
    fontSize: font.sizes.sm,
    lineHeight: 20,
    fontStyle: "italic",
  },

  errorBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    padding: spacing.sm,
    backgroundColor: `${colors.error}22`,
    borderWidth: 1,
    borderColor: colors.error,
    borderRadius: radius.sm,
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
