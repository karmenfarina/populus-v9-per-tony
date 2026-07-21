import React, { useEffect, useState } from "react";
import {
  View,
  Text,
  ScrollView,
  Pressable,
  ActivityIndicator,
  StyleSheet,
  Platform,
} from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { api } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { colors, font, spacing } from "@/src/theme";

/**
 * Mandatory Terms & Privacy Policy screen.
 *
 * Renders on the first login (and any time the stored acceptance
 * version diverges from the server one). The user MUST scroll to the
 * bottom and tap the primary action to proceed — no back button, no
 * dismissal, no logout-shortcut here beyond a discreet link.
 *
 * The document is fetched from the backend so it can be updated
 * without shipping a new client build.
 */
export default function TermsScreen() {
  const router = useRouter();
  const { refreshMe, logout } = useAuth();

  const [text, setText] = useState<string>("");
  const [version, setVersion] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [scrolledBottom, setScrolledBottom] = useState(false);
  const [accepting, setAccepting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r: any = await api.getLegalTerms();
        if (cancelled) return;
        setText(r.text || "");
        setVersion(r.version || "v1");
      } catch {
        if (cancelled) return;
        setError("Impossibile caricare i termini. Verifica la connessione e riprova.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  /**
   * Detect the "user has read to the bottom" moment so we can unlock
   * the primary button. Kept tolerant (~40px pad) so a slight overscroll
   * or rubber-band on iOS still counts.
   */
  const onScroll = (e: any) => {
    const { layoutMeasurement, contentOffset, contentSize } = e.nativeEvent;
    const nearBottom =
      layoutMeasurement.height + contentOffset.y >= contentSize.height - 40;
    if (nearBottom && !scrolledBottom) setScrolledBottom(true);
  };

  const onAccept = async () => {
    if (!version || accepting) return;
    setAccepting(true);
    setError(null);
    try {
      await api.acceptLegalTerms(version);
      await refreshMe(); // pulls the fresh `terms_accepted: true` into context
      router.replace("/");
    } catch (e: any) {
      setError(e?.detail || "Impossibile registrare l'accettazione. Riprova.");
    } finally {
      setAccepting(false);
    }
  };

  const onDecline = async () => {
    // Refusing = the user cannot use the service. We log them out and
    // send them back to the auth screen. This is required by GDPR
    // because we cannot process personal data without consent.
    try { await logout(); } catch { /* still navigate away */ }
    router.replace("/auth");
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
        <View style={styles.center}>
          <ActivityIndicator size="large" color={colors.brandPrimary} />
          <Text style={styles.loadingTxt}>Caricamento…</Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <View style={styles.header}>
        <Ionicons name="shield-checkmark" size={26} color={colors.brandPrimary} />
        <Text style={styles.title}>Prima di iniziare</Text>
        <Text style={styles.subtitle}>Leggi e accetta i Termini per usare Populus</Text>
      </View>

      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.scrollContent}
        onScroll={onScroll}
        scrollEventThrottle={64}
        testID="terms-scroll"
      >
        {/* We render the markdown as plain text for maximum portability.
            The document is authored with visible section headers so the
            plain-text rendering still reads well without a markdown
            parser dependency. */}
        <Text style={styles.doc}>{text}</Text>
      </ScrollView>

      {error ? (
        <View style={styles.errorBox} testID="terms-error">
          <Ionicons name="alert-circle" size={16} color={colors.error} />
          <Text style={styles.errorTxt}>{error}</Text>
        </View>
      ) : null}

      <View style={styles.footer}>
        {!scrolledBottom ? (
          <Text style={styles.footerHint} testID="terms-scroll-hint">
            Scorri fino in fondo per attivare il pulsante di accettazione.
          </Text>
        ) : null}
        <Pressable
          onPress={onAccept}
          disabled={!scrolledBottom || accepting}
          style={[
            styles.acceptBtn,
            (!scrolledBottom || accepting) ? styles.acceptBtnDisabled : null,
          ]}
          testID="terms-accept-btn"
        >
          {accepting ? (
            <ActivityIndicator size="small" color={colors.onBrandPrimary} />
          ) : (
            <Text style={styles.acceptBtnTxt}>ACCETTO E CONTINUA</Text>
          )}
        </Pressable>
        <Pressable onPress={onDecline} disabled={accepting} style={styles.declineBtn} testID="terms-decline-btn" hitSlop={8}>
          <Text style={styles.declineTxt}>Non accetto ed esco</Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, alignItems: "center", justifyContent: "center", gap: spacing.md },
  loadingTxt: { color: colors.muted, fontSize: font.sizes.sm },
  header: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.md,
    paddingBottom: spacing.md,
    borderBottomWidth: 2,
    borderBottomColor: colors.border,
    gap: 4,
    alignItems: "center",
  },
  title: { color: colors.onSurface, fontSize: font.sizes.xl, fontWeight: "700", letterSpacing: 1 },
  subtitle: { color: colors.muted, fontSize: font.sizes.sm, textAlign: "center" },
  scroll: { flex: 1 },
  scrollContent: { paddingHorizontal: spacing.lg, paddingVertical: spacing.lg },
  doc: {
    color: colors.onSurface,
    fontSize: font.sizes.sm,
    lineHeight: 22,
    // Monospace-y default on web keeps the markdown structure legible.
    ...Platform.select({ web: { fontFamily: "-apple-system, sans-serif" as any } }),
  },
  errorBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    padding: spacing.sm,
    marginHorizontal: spacing.lg,
    backgroundColor: `${colors.error}22`,
    borderWidth: 1,
    borderColor: colors.error,
  },
  errorTxt: { color: colors.error, fontSize: font.sizes.sm, flex: 1 },
  footer: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderTopWidth: 2,
    borderTopColor: colors.border,
    gap: spacing.sm,
  },
  footerHint: { color: colors.muted, fontSize: font.sizes.xs, textAlign: "center" },
  acceptBtn: {
    backgroundColor: colors.brandPrimary,
    paddingVertical: spacing.md,
    alignItems: "center",
  },
  acceptBtnDisabled: { opacity: 0.4 },
  acceptBtnTxt: {
    color: colors.onBrandPrimary,
    fontSize: font.sizes.base,
    fontWeight: "700",
    letterSpacing: 2,
  },
  declineBtn: { paddingVertical: spacing.sm, alignItems: "center" },
  declineTxt: {
    color: colors.muted,
    fontSize: font.sizes.sm,
    textDecorationLine: "underline",
  },
});
