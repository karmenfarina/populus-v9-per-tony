import React, { useEffect, useRef, useState } from "react";
import { View, Text, StyleSheet, Animated, Platform } from "react-native";
import { networkStatus } from "@/src/api";
import { colors, font, spacing } from "@/src/theme";

/**
 * Banner "Connessione lenta / assente" che compare in cima all'app quando
 * il wrapper `request()` deve fare retry o si arrende.
 *
 * Design:
 *  - Non si mostra dopo il primo errore isolato (spesso è un glitch di 200ms).
 *    Aspetta il SECONDO fallimento consecutivo entro 8s prima di apparire.
 *  - Si nasconde automaticamente 2s dopo un `recovered` (una richiesta è
 *    andata a buon fine dopo un errore precedente) oppure dopo 15s di
 *    silenzio (nessun nuovo evento).
 *  - Animazione: slide + fade (250ms), rispetta safe-area sopra.
 *  - Non blocca l'interazione. È puramente informativo.
 */
export default function NetworkBanner({ topInset = 0 }: { topInset?: number }) {
  const [visible, setVisible] = useState(false);
  const [mode, setMode] = useState<"slow" | "recovered">("slow");
  const anim = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    let errorsInWindow = 0;
    let lastErrorAt = 0;
    let silenceTimer: ReturnType<typeof setTimeout> | null = null;
    let hideTimer: ReturnType<typeof setTimeout> | null = null;

    const clearHide = () => {
      if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    };
    const resetSilence = () => {
      if (silenceTimer) clearTimeout(silenceTimer);
      silenceTimer = setTimeout(() => {
        // 15s senza nuovi eventi → auto-nascondi
        setVisible(false);
      }, 15_000);
    };

    const unsub = networkStatus.subscribe((e) => {
      const now = Date.now();
      if (e.kind === "error") {
        if (now - lastErrorAt > 8_000) errorsInWindow = 0;
        errorsInWindow += 1;
        lastErrorAt = now;
        // Alza il banner solo dal 2º errore in poi (evita rumore).
        if (errorsInWindow >= 2) {
          clearHide();
          setMode("slow");
          setVisible(true);
          resetSilence();
        }
      } else if (e.kind === "recovered") {
        errorsInWindow = 0;
        if (visible) {
          setMode("recovered");
          clearHide();
          hideTimer = setTimeout(() => setVisible(false), 2_000);
        }
      }
    });
    return () => {
      unsub();
      if (silenceTimer) clearTimeout(silenceTimer);
      if (hideTimer) clearTimeout(hideTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    Animated.timing(anim, {
      toValue: visible ? 1 : 0,
      duration: 250,
      useNativeDriver: Platform.OS !== "web",
    }).start();
  }, [visible, anim]);

  // Anche quando "non visibile", il componente resta montato per l'anim di uscita.
  const translateY = anim.interpolate({ inputRange: [0, 1], outputRange: [-40, 0] });
  const opacity = anim;

  return (
    <Animated.View
      pointerEvents="none"
      style={[
        styles.wrap,
        { top: topInset, transform: [{ translateY }], opacity },
      ]}
      accessibilityLiveRegion="polite"
      testID="network-banner"
    >
      <View style={[styles.pill, mode === "recovered" ? styles.pillOk : styles.pillWarn]}>
        <Text style={styles.dot}>{mode === "recovered" ? "●" : "●"}</Text>
        <Text style={styles.txt} numberOfLines={1}>
          {mode === "recovered"
            ? "Connessione ripristinata"
            : "Connessione lenta o assente"}
        </Text>
      </View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    position: "absolute",
    left: 0,
    right: 0,
    alignItems: "center",
    zIndex: 999,
    paddingHorizontal: spacing.md,
  },
  pill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingVertical: 8,
    paddingHorizontal: 14,
    borderRadius: 999,
    marginTop: spacing.sm,
    // Nota: shadow* è deprecato su web ma non è un blocker in mobile
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.25,
    shadowRadius: 8,
    elevation: 6,
  },
  pillWarn: { backgroundColor: "#3a1e1e", borderWidth: 1, borderColor: "#a04040" },
  pillOk: { backgroundColor: "#1e3a24", borderWidth: 1, borderColor: "#3aa04a" },
  dot: { color: colors.brandSecondary, fontSize: 10 },
  txt: { color: colors.onSurface, fontSize: font.sm, fontWeight: "600" },
});
