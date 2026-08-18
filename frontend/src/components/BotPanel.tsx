/**
 * BotPanel — admin control surface for the 100-persona bot fleet.
 *
 * Renders:
 *  - a big master ON/OFF switch (with derived status pill)
 *  - a numeric stepper "bot online" (0..100)
 *  - +1 / +5 / -1 / -5 / MAX / RESET quick buttons
 *  - a "burst" button that triggers an immediate activity flush
 *  - a "recap" section with last tick / last burst timestamps
 *
 * All state comes from `/api/admin/bots/state` and is refreshed via
 * `useFocusEffect` in the parent admin screen. This component is a
 * pure controlled input: parent owns state + mutations.
 */
import React, { useMemo } from "react";
import { View, Text, StyleSheet, Pressable, ActivityIndicator, ScrollView, Switch } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, spacing, font, radius } from "@/src/theme";

type BotState = {
  enabled: boolean;
  active_count: number;
  reported_active: number;
  total_bots: number;
  last_tick_at: string | null;
  last_burst_at: string | null;
};

type Props = {
  adminKey: string;
  state: BotState | null;
  loading: boolean;
  busy: boolean;
  error: string | null;
  draftCount: number;
  setDraftCount: (n: number) => void;
  onReload: () => void;
  onToggle: (next: boolean) => void;
  onCommit: (n: number) => void;
  onBurst: () => void;
};

function _fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("it-IT", {
      day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
    });
  } catch { return "—"; }
}

export default function BotPanel(props: Props) {
  const {
    adminKey, state, loading, busy, error,
    draftCount, setDraftCount,
    onReload, onToggle, onCommit, onBurst,
  } = props;

  const dirty = useMemo(() => {
    if (!state) return false;
    return Math.round(draftCount) !== Math.round(state.active_count || 0);
  }, [state, draftCount]);

  if (!adminKey) {
    return (
      <View style={styles.centerFill}>
        <Text style={{ color: colors.muted }}>Inserisci prima la chiave admin.</Text>
      </View>
    );
  }

  if (loading && !state) {
    return (
      <View style={styles.centerFill}>
        <ActivityIndicator size="large" color={colors.brandPrimary} />
      </View>
    );
  }

  if (error && !state) {
    return (
      <View style={styles.centerFill}>
        <Text style={styles.err}>{error}</Text>
        <Pressable onPress={onReload} style={styles.smallBtn} testID="bots-retry">
          <Text style={styles.smallBtnTxt}>RIPROVA</Text>
        </Pressable>
      </View>
    );
  }

  if (!state) return null;

  const bump = (delta: number) => {
    const next = Math.max(0, Math.min(100, Math.round(draftCount) + delta));
    setDraftCount(next);
  };

  return (
    <ScrollView contentContainerStyle={styles.content} testID="admin-bots-panel">
      {/* MASTER SWITCH */}
      <View style={styles.card}>
        <View style={styles.rowBetween}>
          <View style={{ flex: 1 }}>
            <Text style={styles.h1}>ATTIVITÀ BOT</Text>
            <Text style={styles.muted}>
              {state.enabled ? "I bot sono online e interagiscono con la piattaforma."
                             : "I bot sono offline. Nessun voto o commento verrà generato."}
            </Text>
          </View>
          <Switch
            value={!!state.enabled}
            onValueChange={onToggle}
            disabled={busy}
            trackColor={{ false: colors.border || "#333", true: colors.brandPrimary }}
            thumbColor={state.enabled ? colors.onBrandPrimary || "#fff" : "#eee"}
            testID="bots-toggle"
          />
        </View>
        <View style={[styles.pill, state.enabled ? styles.pillOn : styles.pillOff]}>
          <View style={[styles.dot, { backgroundColor: state.enabled ? "#4ade80" : "#f87171" }]} />
          <Text style={styles.pillTxt}>
            {state.reported_active} / {state.total_bots} BOT ONLINE
          </Text>
        </View>
      </View>

      {/* COUNTER */}
      <View style={styles.card}>
        <Text style={styles.h1}>BOT ATTIVI</Text>
        <Text style={styles.muted}>
          Aumenta o diminuisci quanti dei 100 bot sono online. La riduzione avviene in modo
          bilanciato (per ideologia, argomento e livello di attività).
        </Text>

        <View style={styles.counterRow}>
          <Pressable
            style={[styles.stepBtn, busy && styles.stepBtnDisabled]}
            onPress={() => bump(-5)}
            disabled={busy}
            testID="bots-minus-5"
          >
            <Text style={styles.stepBtnTxt}>−5</Text>
          </Pressable>
          <Pressable
            style={[styles.stepBtn, busy && styles.stepBtnDisabled]}
            onPress={() => bump(-1)}
            disabled={busy}
            testID="bots-minus-1"
          >
            <Text style={styles.stepBtnTxt}>−1</Text>
          </Pressable>
          <View style={styles.counterBubble}>
            <Text style={styles.counterNum} testID="bots-draft-count">{Math.round(draftCount)}</Text>
            <Text style={styles.counterLabel}>attivi</Text>
          </View>
          <Pressable
            style={[styles.stepBtn, busy && styles.stepBtnDisabled]}
            onPress={() => bump(1)}
            disabled={busy}
            testID="bots-plus-1"
          >
            <Text style={styles.stepBtnTxt}>+1</Text>
          </Pressable>
          <Pressable
            style={[styles.stepBtn, busy && styles.stepBtnDisabled]}
            onPress={() => bump(5)}
            disabled={busy}
            testID="bots-plus-5"
          >
            <Text style={styles.stepBtnTxt}>+5</Text>
          </Pressable>
        </View>

        <View style={styles.presetRow}>
          <Pressable
            style={[styles.presetBtn, busy && styles.stepBtnDisabled]}
            onPress={() => setDraftCount(0)}
            disabled={busy}
            testID="bots-preset-0"
          >
            <Text style={styles.presetTxt}>0</Text>
          </Pressable>
          <Pressable
            style={[styles.presetBtn, busy && styles.stepBtnDisabled]}
            onPress={() => setDraftCount(25)}
            disabled={busy}
            testID="bots-preset-25"
          >
            <Text style={styles.presetTxt}>25</Text>
          </Pressable>
          <Pressable
            style={[styles.presetBtn, busy && styles.stepBtnDisabled]}
            onPress={() => setDraftCount(50)}
            disabled={busy}
            testID="bots-preset-50"
          >
            <Text style={styles.presetTxt}>50</Text>
          </Pressable>
          <Pressable
            style={[styles.presetBtn, busy && styles.stepBtnDisabled]}
            onPress={() => setDraftCount(75)}
            disabled={busy}
            testID="bots-preset-75"
          >
            <Text style={styles.presetTxt}>75</Text>
          </Pressable>
          <Pressable
            style={[styles.presetBtn, busy && styles.stepBtnDisabled]}
            onPress={() => setDraftCount(100)}
            disabled={busy}
            testID="bots-preset-100"
          >
            <Text style={styles.presetTxt}>100</Text>
          </Pressable>
        </View>

        <Pressable
          style={[
            styles.primaryBtn,
            (!dirty || busy) && styles.primaryBtnDisabled,
          ]}
          onPress={() => onCommit(draftCount)}
          disabled={!dirty || busy}
          testID="bots-commit"
        >
          {busy ? (
            <ActivityIndicator size="small" color={colors.onBrandPrimary || "#fff"} />
          ) : (
            <Text style={styles.primaryBtnTxt}>
              {dirty ? "APPLICA" : "SALVATO"}
            </Text>
          )}
        </Pressable>
      </View>

      {/* BURST + RECAP */}
      <View style={styles.card}>
        <Text style={styles.h1}>ATTIVITÀ IMMEDIATA</Text>
        <Text style={styles.muted}>
          Forza un ciclo di attività ora (i bot voteranno e commenteranno sui post più recenti).
        </Text>
        <Pressable
          style={[styles.secondaryBtn, busy && styles.stepBtnDisabled]}
          onPress={onBurst}
          disabled={busy || !state.enabled}
          testID="bots-burst"
        >
          <Ionicons name="flash-outline" size={18} color={colors.brandSecondary} />
          <Text style={styles.secondaryBtnTxt}>
            {state.enabled ? "ESEGUI ORA" : "PRIMA ATTIVA I BOT"}
          </Text>
        </Pressable>

        <View style={styles.metaRow}>
          <Text style={styles.metaLbl}>Ultimo ciclo</Text>
          <Text style={styles.metaVal}>{_fmtDate(state.last_tick_at)}</Text>
        </View>
        <View style={styles.metaRow}>
          <Text style={styles.metaLbl}>Ultimo burst</Text>
          <Text style={styles.metaVal}>{_fmtDate(state.last_burst_at)}</Text>
        </View>
        <Text style={styles.note}>
          I bot non compaiono in Analytics né in Demografia. Il ciclo automatico gira ogni 30 min.
        </Text>
      </View>

      {error ? (
        <View style={[styles.card, { borderColor: "#ef4444" }]}>
          <Text style={styles.err}>{error}</Text>
        </View>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: spacing.md,
    paddingBottom: spacing.xl,
    gap: spacing.md,
  },
  centerFill: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: spacing.lg,
    gap: spacing.md,
  },
  card: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius?.lg || 16,
    padding: spacing.md,
    gap: spacing.sm,
    borderWidth: 1,
    borderColor: colors.border,
  },
  rowBetween: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: spacing.md,
  },
  h1: {
    color: colors.onSurface,
    fontSize: font.sizes.lg,
    fontWeight: "700",
    letterSpacing: 0.5,
  },
  muted: {
    color: colors.muted,
    fontSize: font.sizes.sm,
    lineHeight: 18,
  },
  pill: {
    alignSelf: "flex-start",
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
    borderRadius: radius?.pill || 999,
    marginTop: spacing.xs,
  },
  pillOn: { backgroundColor: "rgba(74, 222, 128, 0.15)" },
  pillOff: { backgroundColor: "rgba(248, 113, 113, 0.12)" },
  pillTxt: {
    color: colors.onSurface,
    fontSize: font.sizes.xs,
    fontWeight: "700",
    letterSpacing: 1,
  },
  dot: { width: 8, height: 8, borderRadius: 4 },
  counterRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: spacing.sm,
    marginTop: spacing.sm,
  },
  stepBtn: {
    minWidth: 48,
    height: 48,
    paddingHorizontal: 10,
    borderRadius: radius?.md || 12,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
  },
  stepBtnDisabled: { opacity: 0.5 },
  stepBtnTxt: {
    color: colors.onSurface,
    fontSize: font.sizes.lg,
    fontWeight: "700",
  },
  counterBubble: {
    flex: 1,
    minHeight: 68,
    borderRadius: radius?.lg || 16,
    backgroundColor: colors.surface,
    borderWidth: 2,
    borderColor: colors.brandPrimary,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: spacing.sm,
  },
  counterNum: {
    color: colors.brandPrimary,
    fontSize: 34,
    fontWeight: "900",
    lineHeight: 38,
  },
  counterLabel: {
    color: colors.muted,
    fontSize: font.sizes.xs,
    letterSpacing: 1,
    textTransform: "uppercase",
  },
  presetRow: {
    flexDirection: "row",
    gap: 6,
    marginTop: spacing.xs,
  },
  presetBtn: {
    flex: 1,
    paddingVertical: 8,
    borderRadius: radius?.pill || 999,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
  },
  presetTxt: {
    color: colors.onSurface,
    fontSize: font.sizes.sm,
    fontWeight: "700",
  },
  primaryBtn: {
    marginTop: spacing.sm,
    height: 46,
    borderRadius: radius?.pill || 999,
    backgroundColor: colors.brandPrimary,
    alignItems: "center",
    justifyContent: "center",
  },
  primaryBtnDisabled: { opacity: 0.4 },
  primaryBtnTxt: {
    color: colors.onBrandPrimary,
    fontSize: font.sizes.base,
    fontWeight: "800",
    letterSpacing: 1,
  },
  secondaryBtn: {
    height: 44,
    borderRadius: radius?.pill || 999,
    borderWidth: 1,
    borderColor: colors.brandSecondary,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
  },
  secondaryBtnTxt: {
    color: colors.brandSecondary,
    fontSize: font.sizes.sm,
    fontWeight: "800",
    letterSpacing: 1,
  },
  metaRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: 4,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  metaLbl: { color: colors.muted, fontSize: font.sizes.xs, letterSpacing: 0.5 },
  metaVal: { color: colors.onSurface, fontSize: font.sizes.sm, fontWeight: "600" },
  note: {
    color: colors.muted,
    fontSize: font.sizes.xs,
    lineHeight: 16,
    marginTop: spacing.sm,
    fontStyle: "italic",
  },
  err: {
    color: colors.error,
    fontSize: font.sizes.sm,
    textAlign: "center",
  },
  smallBtn: {
    marginTop: spacing.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    borderRadius: radius?.pill || 999,
    backgroundColor: colors.brandPrimary,
  },
  smallBtnTxt: {
    color: colors.onBrandPrimary,
    fontWeight: "700",
    letterSpacing: 1,
    fontSize: font.sizes.sm,
  },
});
