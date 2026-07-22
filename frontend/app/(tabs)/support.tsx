import { useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, TextInput, ActivityIndicator,
  KeyboardAvoidingView, Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { api, ApiError } from "@/src/api";
import { colors, spacing, font } from "@/src/theme";
import { useAuth } from "@/src/auth/AuthContext";
import { useSmartBack } from "@/src/utils/useSmartBack";

const CATEGORIES = [
  { id: "Bug", label: "Bug o malfunzionamento" },
  { id: "Login o verifica email", label: "Login o verifica email" },
  { id: "Contenuto inappropriato", label: "Contenuto inappropriato o segnalazione" },
  { id: "Cerchia del Gossip", label: "Problema con la Cerchia" },
  { id: "Stories o Spille", label: "Problema con Stories o Spille" },
  { id: "Pubblicità", label: "Problema con la pubblicità" },
  { id: "Suggerimento", label: "Suggerimento o richiesta" },
  { id: "Account", label: "Problema con l'account" },
  { id: "Privacy", label: "Privacy o dati personali" },
  { id: "Altro", label: "Altro" },
];
const FREQUENCIES = ["Prima volta", "Occasionale", "Frequente", "Blocca l'app"];
const SECTIONS = [
  "Home / Faide del giorno",
  "Dettaglio faida (chat, voto, HYPE)",
  "Stories",
  "Cerchia del Gossip",
  "Spille (Badges)",
  "Profilo",
  "Ricerca",
  "Notifiche",
  "Archivio",
  "Login / Registrazione",
  "Altro",
];
const DEVICES = ["iOS", "Android", "Web / Browser"];

export default function SupportScreen() {
  const router = useRouter();
  // The support screen is opened from the Profile tab. `router.back()`
  // in a Tabs layout is unreliable on web — it collapses back to "/".
  // `useSmartBack` walks the tracked nav stack and falls back to the
  // owning tab ("/profile") when there's nothing else to pop.
  const goBack = useSmartBack("/profile");
  const { user, logout } = useAuth();
  const isAnonymous = !!user && user.auth_provider === "anonymous";
  const [category, setCategory] = useState<string | null>(null);
  const [frequency, setFrequency] = useState<string | null>(null);
  const [section, setSection] = useState<string | null>(null);
  // Device chip row helps triage bugs quickly — iOS/Android/Web behave
  // very differently for AdMob, Firebase Auth redirects and Stories.
  const [device, setDevice] = useState<string | null>(null);
  const [description, setDescription] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const validate = (): string | null => {
    if (!category) return "Seleziona una categoria del problema.";
    if (!frequency) return "Indica la frequenza del problema.";
    if (!section) return "Indica la sezione dell'app in cui si verifica.";
    if (!device) return "Indica su quale dispositivo hai riscontrato il problema.";
    if (description.trim().length < 10) return "Descrivi il problema con almeno 10 caratteri.";
    if (contactEmail.trim() && !/^\S+@\S+\.\S+$/.test(contactEmail.trim())) {
      return "L'email di contatto non è valida.";
    }
    return null;
  };

  const submit = async () => {
    const v = validate();
    if (v) { setErr(v); return; }
    setErr(null); setSending(true);
    try {
      // Prepend device tag to the description so the support admin sees
      // it in the email/ticket even though the backend schema doesn't
      // have a dedicated device field.
      const descWithDevice = `[Dispositivo: ${device}]\n\n${description.trim()}`;
      await api.submitSupport({
        category: category!,
        description: descWithDevice,
        frequency: frequency!,
        section: section!,
        contact_email: contactEmail.trim() || undefined,
      });
      setSent(true);
    } catch (e: any) {
      setErr(e instanceof ApiError ? e.detail : (e?.message || "Errore nell'invio."));
    } finally { setSending(false); }
  };

  if (isAnonymous) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]} testID="support-anon-lock">
        <View style={styles.headerBar}>
          <Pressable onPress={goBack} style={styles.backBtn} testID="support-back">
            <Ionicons name="chevron-back" size={22} color={colors.brandSecondary} />
          </Pressable>
          <Text style={styles.title}>ASSISTENZA</Text>
        </View>
        <View style={styles.centerBox}>
          <View style={styles.anonLockCircle}>
            <Ionicons name="lock-closed-outline" size={64} color={colors.brandSecondary} />
          </View>
          <Text style={styles.thanksBig}>SOLO PER ACCOUNT REGISTRATI</Text>
          <Text style={styles.thanksSmall}>
            Come utente anonimo non puoi inviare richieste di assistenza. Registrati
            con un account per poterci scrivere e ricevere una risposta.
          </Text>
          <Pressable
            style={styles.homeBtn}
            onPress={async () => { await logout(); router.replace("/auth"); }}
            testID="support-register-cta"
          >
            <Text style={styles.homeBtnTxt}>REGISTRATI ORA  ›</Text>
          </Pressable>
          <Pressable onPress={() => router.replace("/")} testID="support-back-home" hitSlop={8}>
            <Text style={styles.anonSecondaryTxt}>Torna alle faide</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    );
  }

  if (sent) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]} testID="support-sent">
        <View style={styles.headerBar}>
          <Pressable onPress={goBack} style={styles.backBtn} testID="support-back">
            <Ionicons name="chevron-back" size={22} color={colors.brandSecondary} />
          </Pressable>
          <Text style={styles.title}>ASSISTENZA</Text>
        </View>
        <View style={styles.centerBox}>
          <Ionicons name="checkmark-circle" size={72} color={colors.brandPrimary} />
          <Text style={styles.thanksBig}>Richiesta inviata!</Text>
          <Text style={styles.thanksSmall}>Grazie per averci scritto. Ti risponderemo il prima possibile.</Text>
          <Pressable style={styles.homeBtn} onPress={() => router.replace("/")}>
            <Text style={styles.homeBtnTxt}>TORNA ALLE FAIDE</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]} testID="support-screen">
      <View style={styles.headerBar}>
        <Pressable onPress={goBack} style={styles.backBtn} testID="support-back">
          <Ionicons name="chevron-back" size={22} color={colors.brandSecondary} />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>ASSISTENZA</Text>
          <Text style={styles.subtitle}>Raccontaci cosa non va</Text>
        </View>
      </View>

      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <Text style={styles.label}>Qual è il problema?</Text>
          <View style={styles.chipsWrap}>
            {CATEGORIES.map((c) => (
              <Pressable
                key={c.id}
                testID={`support-cat-${c.id}`}
                onPress={() => setCategory(c.id)}
                style={[styles.chip, category === c.id && styles.chipOn]}
              >
                <Text style={[styles.chipTxt, category === c.id && styles.chipTxtOn]}>{c.label}</Text>
              </Pressable>
            ))}
          </View>

          <Text style={styles.label}>Quanto spesso capita?</Text>
          <View style={styles.chipsWrap}>
            {FREQUENCIES.map((f) => (
              <Pressable
                key={f}
                testID={`support-freq-${f}`}
                onPress={() => setFrequency(f)}
                style={[styles.chip, frequency === f && styles.chipOn]}
              >
                <Text style={[styles.chipTxt, frequency === f && styles.chipTxtOn]}>{f}</Text>
              </Pressable>
            ))}
          </View>

          <Text style={styles.label}>Dove è successo?</Text>
          <View style={styles.chipsWrap}>
            {SECTIONS.map((s) => (
              <Pressable
                key={s}
                testID={`support-sec-${s}`}
                onPress={() => setSection(s)}
                style={[styles.chip, section === s && styles.chipOn]}
              >
                <Text style={[styles.chipTxt, section === s && styles.chipTxtOn]}>{s}</Text>
              </Pressable>
            ))}
          </View>

          <Text style={styles.label}>Su quale dispositivo?</Text>
          <View style={styles.chipsWrap}>
            {DEVICES.map((d) => (
              <Pressable
                key={d}
                testID={`support-dev-${d}`}
                onPress={() => setDevice(d)}
                style={[styles.chip, device === d && styles.chipOn]}
              >
                <Text style={[styles.chipTxt, device === d && styles.chipTxtOn]}>{d}</Text>
              </Pressable>
            ))}
          </View>

          {category === "Login o verifica email" && (
            <View style={styles.tipBox} testID="support-tip-verify">
              <Ionicons name="information-circle" size={16} color={colors.brandPrimary} />
              <Text style={styles.tipTxt}>
                Se non ricevi l&apos;email di verifica, controlla la cartella
                spam/promozioni. Le email arrivano da &quot;noreply@populus-1f567.firebaseapp.com&quot;.
              </Text>
            </View>
          )}

          <Text style={styles.label}>Descrivi il problema</Text>
          <TextInput
            value={description}
            onChangeText={setDescription}
            multiline
            numberOfLines={5}
            maxLength={2000}
            placeholder="Cosa è successo? Passi per riprodurlo, cosa ti aspettavi, cosa hai visto..."
            placeholderTextColor={colors.muted}
            testID="support-desc"
            style={styles.textarea}
          />

          <Text style={styles.label}>Email di contatto (facoltativa)</Text>
          <TextInput
            value={contactEmail}
            onChangeText={setContactEmail}
            placeholder="Lasciala se vuoi una risposta"
            placeholderTextColor={colors.muted}
            keyboardType="email-address"
            autoCapitalize="none"
            testID="support-email"
            style={styles.input}
          />

          {err && <Text style={styles.err} testID="support-error">{err}</Text>}

          <Pressable
            testID="support-submit"
            onPress={submit}
            disabled={sending}
            style={[styles.cta, sending && { opacity: 0.6 }]}
          >
            {sending ? (
              <ActivityIndicator color={colors.onBrandPrimary} />
            ) : (
              <Text style={styles.ctaTxt}>INVIA RICHIESTA</Text>
            )}
          </Pressable>

          <Text style={styles.privacy}>
            Le tue info (nickname, ID account, provider) vengono incluse per aiutarci a trovarti.
          </Text>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  headerBar: { flexDirection: "row", alignItems: "center", gap: spacing.sm, paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderBottomWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceInverse },
  backBtn: { width: 40, height: 40, borderWidth: 2, borderColor: colors.brandSecondary, alignItems: "center", justifyContent: "center" },
  title: { color: colors.brandSecondary, fontSize: font.sizes.xxxl, letterSpacing: 2, fontWeight: "500" },
  subtitle: { color: colors.onSurfaceInverse, fontSize: font.sizes.sm, letterSpacing: 1, marginTop: 2 },
  content: { padding: spacing.lg, gap: spacing.md, paddingBottom: spacing.xxxl },
  label: { color: colors.onSurface, fontSize: font.sizes.sm, letterSpacing: 1, fontWeight: "500", marginTop: spacing.sm },
  chipsWrap: { flexDirection: "row", flexWrap: "wrap", gap: spacing.xs },
  chip: { borderWidth: 2, borderColor: colors.border, paddingHorizontal: spacing.sm, paddingVertical: 6, backgroundColor: colors.surface },
  chipOn: { borderColor: colors.brandPrimary, backgroundColor: colors.brandPrimary },
  chipTxt: { fontSize: font.sizes.xs, color: colors.onSurface, letterSpacing: 0.5 },
  chipTxtOn: { color: colors.onBrandPrimary, fontWeight: "500" },
  input: { borderWidth: 2, borderColor: colors.border, padding: spacing.sm, backgroundColor: colors.surface, color: colors.onSurface, fontSize: font.sizes.base },
  textarea: { borderWidth: 2, borderColor: colors.border, padding: spacing.sm, backgroundColor: colors.surface, color: colors.onSurface, fontSize: font.sizes.base, minHeight: 120, textAlignVertical: "top" },
  cta: { marginTop: spacing.md, backgroundColor: colors.brandPrimary, padding: spacing.md, alignItems: "center" },
  ctaTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.base, letterSpacing: 2, fontWeight: "500" },
  err: { color: colors.brandPrimary, fontSize: font.sizes.sm },
  privacy: { color: colors.muted, fontSize: font.sizes.xs, marginTop: spacing.sm, textAlign: "center" },
  centerBox: { flex: 1, alignItems: "center", justifyContent: "center", gap: spacing.md, padding: spacing.xl },
  thanksBig: { color: colors.onSurface, fontSize: font.sizes.xxl, letterSpacing: 1, fontWeight: "500" },
  thanksSmall: { color: colors.muted, fontSize: font.sizes.base, textAlign: "center" },
  homeBtn: { marginTop: spacing.md, borderWidth: 2, borderColor: colors.brandPrimary, paddingHorizontal: spacing.lg, paddingVertical: spacing.md, backgroundColor: colors.brandPrimary },
  homeBtnTxt: { color: colors.onBrandPrimary, letterSpacing: 2, fontWeight: "500" },
  anonLockCircle: { width: 120, height: 120, borderRadius: 60, borderWidth: 2, borderColor: colors.brandSecondary, alignItems: "center", justifyContent: "center", backgroundColor: colors.surfaceInverse, marginBottom: spacing.sm },
  anonSecondaryTxt: { color: colors.muted, fontSize: font.sizes.xs, letterSpacing: 1, marginTop: spacing.sm, textDecorationLine: "underline" },
  tipBox: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing.xs,
    borderWidth: 2,
    borderColor: colors.brandPrimary,
    backgroundColor: colors.surfaceSecondary,
    padding: spacing.sm,
    marginTop: spacing.xs,
  },
  tipTxt: {
    flex: 1,
    color: colors.onSurface,
    fontSize: font.sizes.xs,
    lineHeight: 18,
  },
});
