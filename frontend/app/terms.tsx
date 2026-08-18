import React, { useEffect, useState } from "react";
import {
  View,
  Text,
  ScrollView,
  Pressable,
  ActivityIndicator,
  StyleSheet,
  Platform,
  Modal,
} from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { api } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { colors, font, spacing, radius } from "@/src/theme";

/**
 * Mandatory onboarding screen — Terms of Service AND NDA.
 *
 * Design decisions
 * ─────────────────────────────────────────────────────────────────
 * The screen shows two independent cards (one per document). Each
 * card:
 *   • displays the title + a short summary
 *   • has its OWN acceptance checkbox on the right
 *   • can be tapped to open the full text in a modal (optional)
 * The primary CTA is enabled ONLY when both checkboxes are ticked.
 * We never force the user to scroll through the whole document —
 * reading is opt-in; acceptance is explicit.
 */
type LegalDoc = { version: string; text: string };

export default function TermsScreen() {
  const router = useRouter();
  const { refreshMe, logout } = useAuth();

  const [terms, setTerms] = useState<LegalDoc | null>(null);
  const [nda, setNda] = useState<LegalDoc | null>(null);
  const [loading, setLoading] = useState(true);
  const [accepting, setAccepting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [acceptedTerms, setAcceptedTerms] = useState(false);
  const [acceptedNda, setAcceptedNda] = useState(false);

  // Optional full-text viewer — driven by which card the user tapped.
  const [viewer, setViewer] = useState<"terms" | "nda" | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [t, n]: [any, any] = await Promise.all([
          api.getLegalTerms(),
          api.getLegalNda(),
        ]);
        if (cancelled) return;
        setTerms({ version: t.version || "v1", text: t.text || "" });
        setNda({ version: n.version || "v1", text: n.text || "" });
      } catch {
        if (cancelled) return;
        setError("Impossibile caricare i documenti. Verifica la connessione e riprova.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const allAccepted = acceptedTerms && acceptedNda;

  const onAccept = async () => {
    if (!allAccepted || accepting || !terms || !nda) return;
    setAccepting(true);
    setError(null);
    try {
      await api.acceptLegalBoth(terms.version, nda.version);
      await refreshMe(); // pulls `terms_accepted: true` into context
      router.replace("/");
    } catch (e: any) {
      setError(e?.detail || "Impossibile registrare l'accettazione. Riprova.");
    } finally {
      setAccepting(false);
    }
  };

  const onDecline = async () => {
    // Refusing = we cannot process personal data → log out + kick out.
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
      <ScrollView contentContainerStyle={styles.scroll}>
        <View style={styles.header}>
          <Ionicons name="shield-checkmark" size={32} color={colors.brandPrimary} />
          <Text style={styles.title}>Prima di iniziare</Text>
          <Text style={styles.subtitle}>
            Per usare Populus devi accettare due documenti. Puoi accettarli senza leggerli, oppure toccare
            ogni card per aprirla e leggere il testo integrale.
          </Text>
        </View>

        {/* CARD 1 — Terms & Privacy Policy */}
        <DocCard
          icon="document-text-outline"
          title="Termini di Servizio e Privacy"
          summary="Regole d'uso della piattaforma, informativa privacy, gestione dei tuoi dati."
          version={terms?.version || "v1"}
          checked={acceptedTerms}
          onToggle={() => setAcceptedTerms((v) => !v)}
          onOpenText={() => setViewer("terms")}
          testIDPrefix="terms"
        />

        {/* CARD 2 — NDA */}
        <DocCard
          icon="lock-closed-outline"
          title="Accordo di Riservatezza (NDA)"
          summary="Impegno a non divulgare contenuti privati di altri utenti al di fuori della piattaforma."
          version={nda?.version || "v1"}
          checked={acceptedNda}
          onToggle={() => setAcceptedNda((v) => !v)}
          onOpenText={() => setViewer("nda")}
          testIDPrefix="nda"
        />

        {error ? (
          <View style={styles.errorBox} testID="terms-error">
            <Ionicons name="alert-circle" size={16} color={colors.error} />
            <Text style={styles.errorTxt}>{error}</Text>
          </View>
        ) : null}
      </ScrollView>

      <View style={styles.footer}>
        <Pressable
          onPress={onAccept}
          disabled={!allAccepted || accepting}
          style={[styles.acceptBtn, (!allAccepted || accepting) && styles.acceptBtnDisabled]}
          testID="terms-accept-btn"
        >
          {accepting ? (
            <ActivityIndicator size="small" color={colors.onBrandPrimary} />
          ) : (
            <Text style={styles.acceptBtnTxt}>
              {allAccepted ? "ACCETTO E CONTINUO" : "SPUNTA ENTRAMBI I DOCUMENTI"}
            </Text>
          )}
        </Pressable>
        <Pressable onPress={onDecline} disabled={accepting} style={styles.declineBtn} testID="terms-decline-btn" hitSlop={8}>
          <Text style={styles.declineTxt}>Non accetto ed esco</Text>
        </Pressable>
      </View>

      {/* Full-text viewer modal (opt-in reading) */}
      <Modal
        visible={viewer !== null}
        animationType="slide"
        onRequestClose={() => setViewer(null)}
      >
        <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
          <View style={styles.modalHeader}>
            <Pressable
              onPress={() => setViewer(null)}
              style={styles.closeBtn}
              hitSlop={12}
              testID="doc-viewer-close"
            >
              <Ionicons name="close" size={24} color={colors.onSurface} />
            </Pressable>
            <Text style={styles.modalTitle} numberOfLines={1}>
              {viewer === "terms" ? "Termini & Privacy" : "NDA"}
            </Text>
            <View style={{ width: 24 }} />
          </View>
          <ScrollView contentContainerStyle={styles.modalBody}>
            <Text style={styles.doc}>
              {viewer === "terms" ? terms?.text : nda?.text}
            </Text>
          </ScrollView>
          <View style={styles.modalFooter}>
            <Pressable
              onPress={() => {
                if (viewer === "terms") setAcceptedTerms(true);
                if (viewer === "nda") setAcceptedNda(true);
                setViewer(null);
              }}
              style={styles.modalAcceptBtn}
              testID="doc-viewer-accept"
            >
              <Ionicons name="checkmark-circle" size={18} color={colors.onBrandPrimary} />
              <Text style={styles.modalAcceptTxt}>HO LETTO — ACCETTO</Text>
            </Pressable>
          </View>
        </SafeAreaView>
      </Modal>
    </SafeAreaView>
  );
}

// ── Sub-component: single document acceptance card ────────────────
function DocCard(props: {
  icon: keyof typeof Ionicons.glyphMap;
  title: string;
  summary: string;
  version: string;
  checked: boolean;
  onToggle: () => void;
  onOpenText: () => void;
  testIDPrefix: string;
}) {
  const { icon, title, summary, version, checked, onToggle, onOpenText, testIDPrefix } = props;
  return (
    <View style={[styles.card, checked && styles.cardChecked]} testID={`${testIDPrefix}-card`}>
      <Pressable
        onPress={onOpenText}
        style={styles.cardBody}
        testID={`${testIDPrefix}-open`}
      >
        <View style={styles.cardIcon}>
          <Ionicons name={icon} size={22} color={colors.brandPrimary} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={styles.cardTitle}>{title}</Text>
          <Text style={styles.cardSummary}>{summary}</Text>
          <View style={styles.readRow}>
            <Ionicons name="reader-outline" size={13} color={colors.brandSecondary} />
            <Text style={styles.readLink}>Tocca per leggere il testo integrale</Text>
          </View>
          <Text style={styles.cardMeta}>Versione {version}</Text>
        </View>
      </Pressable>
      <Pressable
        onPress={onToggle}
        style={[styles.checkbox, checked && styles.checkboxChecked]}
        hitSlop={10}
        testID={`${testIDPrefix}-checkbox`}
        accessibilityRole="checkbox"
        accessibilityState={{ checked }}
        accessibilityLabel={`Accetto ${title}`}
      >
        {checked ? (
          <Ionicons name="checkmark" size={22} color={colors.onBrandPrimary} />
        ) : null}
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, alignItems: "center", justifyContent: "center", gap: spacing.md },
  loadingTxt: { color: colors.muted, fontSize: font.sizes.sm },
  scroll: {
    padding: spacing.lg,
    paddingBottom: spacing.xl,
    gap: spacing.md,
  },
  header: {
    alignItems: "center",
    gap: 6,
    marginBottom: spacing.md,
  },
  title: {
    color: colors.onSurface,
    fontSize: font.sizes.xxl,
    fontWeight: "800",
    letterSpacing: 0.5,
    marginTop: 6,
  },
  subtitle: {
    color: colors.muted,
    fontSize: font.sizes.sm,
    textAlign: "center",
    lineHeight: 20,
    paddingHorizontal: spacing.md,
  },
  // ── Doc card ─────────────────────────────────────────────────
  card: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
  },
  cardChecked: {
    borderColor: colors.brandPrimary,
    backgroundColor: `${colors.brandPrimary}12`,
  },
  cardBody: {
    flex: 1,
    flexDirection: "row",
    gap: spacing.sm,
    alignItems: "flex-start",
  },
  cardIcon: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: colors.surfaceTertiary,
    alignItems: "center",
    justifyContent: "center",
  },
  cardTitle: {
    color: colors.onSurface,
    fontSize: font.sizes.base,
    fontWeight: "700",
    letterSpacing: 0.3,
  },
  cardSummary: {
    color: colors.muted,
    fontSize: font.sizes.sm,
    lineHeight: 18,
    marginTop: 3,
  },
  readRow: {
    marginTop: 6,
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  readLink: {
    color: colors.brandSecondary,
    fontSize: font.sizes.xs,
    fontWeight: "600",
    textDecorationLine: "underline",
  },
  cardMeta: {
    color: colors.muted,
    fontSize: font.sizes.xs,
    marginTop: 4,
    letterSpacing: 0.5,
  },
  // ── Checkbox ─────────────────────────────────────────────────
  checkbox: {
    width: 32,
    height: 32,
    borderRadius: 8,
    borderWidth: 2,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.surface,
  },
  checkboxChecked: {
    backgroundColor: colors.brandPrimary,
    borderColor: colors.brandPrimary,
  },
  // ── Footer / CTA ─────────────────────────────────────────────
  footer: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    gap: spacing.sm,
    backgroundColor: colors.surface,
  },
  acceptBtn: {
    backgroundColor: colors.brandPrimary,
    borderRadius: radius.pill,
    paddingVertical: spacing.md,
    alignItems: "center",
  },
  acceptBtnDisabled: { opacity: 0.4 },
  acceptBtnTxt: {
    color: colors.onBrandPrimary,
    fontSize: font.sizes.base,
    fontWeight: "800",
    letterSpacing: 1.5,
  },
  declineBtn: { paddingVertical: spacing.sm, alignItems: "center" },
  declineTxt: {
    color: colors.muted,
    fontSize: font.sizes.sm,
    textDecorationLine: "underline",
  },
  // ── Error banner ─────────────────────────────────────────────
  errorBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    padding: spacing.sm,
    backgroundColor: `${colors.error}22`,
    borderWidth: 1,
    borderColor: colors.error,
    borderRadius: radius.md,
  },
  errorTxt: { color: colors.error, fontSize: font.sizes.sm, flex: 1 },
  // ── Full-text viewer modal ───────────────────────────────────
  modalHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  closeBtn: {
    width: 32,
    height: 32,
    alignItems: "center",
    justifyContent: "center",
  },
  modalTitle: {
    color: colors.onSurface,
    fontSize: font.sizes.base,
    fontWeight: "700",
    letterSpacing: 1,
  },
  modalBody: {
    padding: spacing.lg,
    paddingBottom: spacing.xl,
  },
  doc: {
    color: colors.onSurface,
    fontSize: font.sizes.sm,
    lineHeight: 22,
    ...Platform.select({ web: { fontFamily: "-apple-system, sans-serif" as any } }),
  },
  modalFooter: {
    padding: spacing.md,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  modalAcceptBtn: {
    backgroundColor: colors.brandPrimary,
    borderRadius: radius.pill,
    paddingVertical: spacing.md,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
  },
  modalAcceptTxt: {
    color: colors.onBrandPrimary,
    fontSize: font.sizes.base,
    fontWeight: "800",
    letterSpacing: 1.5,
  },
});
