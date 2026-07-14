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

const CATEGORIES = [
  { id: "Bug", label: "Bug o malfunzionamento" },
  { id: "Contenuto inappropriato", label: "Contenuto inappropriato" },
  { id: "Suggerimento", label: "Suggerimento" },
  { id: "Account", label: "Problema con l'account" },
  { id: "Altro", label: "Altro" },
];
const FREQUENCIES = ["Prima volta", "Occasionale", "Frequente"];
const SECTIONS = ["Home", "Faida", "Profilo", "Notifiche", "Archivio", "Altro"];

export default function SupportScreen() {
  const router = useRouter();
  const [category, setCategory] = useState<string | null>(null);
  const [frequency, setFrequency] = useState<string | null>(null);
  const [section, setSection] = useState<string | null>(null);
  const [description, setDescription] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const validate = (): string | null => {
    if (!category) return "Seleziona una categoria del problema.";
    if (!frequency) return "Indica la frequenza del problema.";
    if (!section) return "Indica la sezione dell'app in cui si verifica.";
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
      await api.submitSupport({
        category: category!,
        description: description.trim(),
        frequency: frequency!,
        section: section!,
        contact_email: contactEmail.trim() || undefined,
      });
      setSent(true);
    } catch (e: any) {
      setErr(e instanceof ApiError ? e.detail : (e?.message || "Errore nell'invio."));
    } finally { setSending(false); }
  };

  if (sent) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]} testID="support-sent">
        <View style={styles.headerBar}>
          <Pressable onPress={() => router.back()} style={styles.backBtn} testID="support-back">
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
        <Pressable onPress={() => router.back()} style={styles.backBtn} testID="support-back">
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
});
