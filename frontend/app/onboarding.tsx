import { useEffect, useMemo, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, TextInput, ActivityIndicator,
  KeyboardAvoidingView, Platform, Modal, FlatList,
} from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { api } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { colors, spacing, font } from "@/src/theme";

const REGIONS = [
  "Abruzzo", "Basilicata", "Calabria", "Campania", "Emilia-Romagna",
  "Friuli-Venezia Giulia", "Lazio", "Liguria", "Lombardia", "Marche",
  "Molise", "Piemonte", "Puglia", "Sardegna", "Sicilia", "Toscana",
  "Trentino-Alto Adige", "Umbria", "Valle d'Aosta", "Veneto", "Altro",
];

type Sex = "F" | "M" | "other" | "na";

export default function Onboarding() {
  const router = useRouter();
  const { user, refreshMe, logout } = useAuth();

  // Only external-provider accounts (Google today, others tomorrow) need to
  // pick a nickname during onboarding — email signup and anonymous flows both
  // already collect one earlier. We expose a boolean so the step ordering
  // (and total step count) can adapt at render time.
  const needsNickname = user?.auth_provider === "google";
  const totalSteps = needsNickname ? 5 : 4;
  type Step = 1 | 2 | 3 | 4 | 5;
  const [step, setStep] = useState<Step>(needsNickname ? 1 : 2);
  const [nickname, setNickname] = useState<string>(needsNickname ? "" : (user?.nickname || ""));
  const [cats, setCats] = useState<{ id: string; label: string }[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [age, setAge] = useState<string>("");
  const [sex, setSex] = useState<Sex | null>(null);
  const [region, setRegion] = useState<string>("");
  const [regionOpen, setRegionOpen] = useState(false);
  const [professions, setProfessions] = useState<string[]>([]);
  const [profession, setProfession] = useState<string>("");
  const [professionOpen, setProfessionOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    (async () => {
      const [r, p] = await Promise.all([
        api.categories(),
        api.professions().catch(() => ({ professions: [] as string[] })),
      ]);
      setCats(r.categories);
      setProfessions((p as any).professions || []);
    })();
  }, []);

  const allSelected = useMemo(
    () => cats.length > 0 && selected.size === cats.length,
    [cats, selected]
  );

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (allSelected) setSelected(new Set());
    else setSelected(new Set(cats.map((c) => c.id)));
  };

  // Renders a friendly "Step X di Y" label using the ADAPTIVE step numbering
  // (step 1 might not exist for email users) but always in a 1..totalSteps
  // range for the display.
  const displayStep = useMemo(() => {
    if (!needsNickname) return step - 1; // steps go 2..5, display as 1..4
    return step; // steps go 1..5, display as 1..5
  }, [step, needsNickname]);

  const goNext = () => {
    setError(null);
    if (step === 1) {
      const clean = nickname.trim().replace(/^@+/, "");
      if (clean.length < 2 || clean.length > 24) {
        setError("Il nickname deve avere 2-24 caratteri");
        return;
      }
      setNickname(clean);
      setStep(2);
    } else if (step === 2) {
      if (selected.size === 0) { setError("Scegli almeno una categoria preferita"); return; }
      setStep(3);
    } else if (step === 3) {
      const ageNum = parseInt(age, 10);
      if (!ageNum || ageNum < 13 || ageNum > 120) { setError("Inserisci un'età valida (13-120)"); return; }
      if (!sex) { setError("Seleziona il sesso"); return; }
      setStep(4);
    } else if (step === 4) {
      if (!region) { setError("Seleziona la regione"); return; }
      setStep(5);
    }
  };

  const goBack = () => {
    setError(null);
    if (step === 2 && needsNickname) setStep(1);
    else if (step === 3) setStep(2);
    else if (step === 4) setStep(3);
    else if (step === 5) setStep(4);
  };

  const submit = async () => {
    setError(null);
    if (!region) { setError("Seleziona la regione"); return; }
    if (!profession) { setError("Seleziona la professione"); return; }
    setSubmitting(true);
    try {
      const ageNum = parseInt(age, 10);
      await api.updateProfile({
        age: ageNum,
        sex: sex as Sex,
        region,
        favorite_categories: Array.from(selected),
        profession,
        ...(needsNickname && nickname ? { nickname } : {}),
      });
      await refreshMe();
      router.replace("/(tabs)");
    } catch (e: any) {
      setError(e?.message || "Errore durante il salvataggio");
    } finally {
      setSubmitting(false);
    }
  };

  // First "back" button behaviour: when the user is on the very first step
  // (nickname for Google users, categories otherwise) we treat it as
  // cancelling the onboarding entirely and logging out.
  const isFirstStep = (needsNickname && step === 1) || (!needsNickname && step === 2);

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]} testID="onboarding-screen">
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <View style={styles.header}>
          <View style={styles.progressRow}>
            {Array.from({ length: totalSteps }, (_, i) => i + 1).map((n) => (
              <View
                key={n}
                testID={`progress-${n}`}
                style={[styles.progressDot, n <= displayStep && styles.progressDotOn]}
              />
            ))}
          </View>
          <Text style={styles.brand} testID="onboarding-step-brand">
            {step === 1 && "SCEGLI IL NICKNAME"}
            {step === 2 && (
              <>BENVENUTO{nickname ? `, @${nickname}` : (user?.nickname ? `, @${user.nickname}` : "")}</>
            )}
            {step === 3 && "CHI SEI"}
            {step === 4 && "DA DOVE VIENI"}
            {step === 5 && "COSA FAI"}
          </Text>
          <Text style={styles.tagline}>Step {displayStep} di {totalSteps}</Text>
        </View>

        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          {step === 1 && (
            <View style={styles.section} testID="onboarding-step-nickname">
              <Text style={styles.sectionTitle}>NICKNAME</Text>
              <Text style={styles.sectionHint}>
                Scegli come vuoi essere chiamato/a nella community. Verrà mostrato
                al posto del tuo nome vero accanto alla foto profilo.
              </Text>
              <View style={styles.nickInputWrap}>
                <Text style={styles.nickAt}>@</Text>
                <TextInput
                  testID="nickname-input"
                  style={styles.nickInput}
                  placeholder="es. gossip_queen"
                  placeholderTextColor={colors.muted}
                  value={nickname}
                  onChangeText={(t) => setNickname(t.replace(/^@+/, "").slice(0, 24))}
                  autoCapitalize="none"
                  autoCorrect={false}
                  maxLength={24}
                />
              </View>
              <Text style={styles.hintTiny}>2-24 caratteri. Puoi cambiarlo in seguito.</Text>
            </View>
          )}

          {step === 2 && (
            <View style={styles.section} testID="onboarding-step-1">
              <Text style={styles.sectionTitle}>LE TUE CATEGORIE PREFERITE</Text>
              <Text style={styles.sectionHint}>Sceglile per personalizzare la tua home.</Text>

              <Pressable onPress={toggleAll} testID="select-all-toggle" style={styles.selectAllRow}>
                <Ionicons
                  name={allSelected ? "checkbox" : "square-outline"}
                  size={16}
                  color={colors.onSurface}
                />
                <Text style={styles.selectAllTxt}>
                  {allSelected ? "TOGLI TUTTE" : "SELEZIONA TUTTE"}
                </Text>
              </Pressable>

              <View style={styles.catsGrid}>
                {cats.map((c) => {
                  const on = selected.has(c.id);
                  return (
                    <Pressable
                      key={c.id}
                      onPress={() => toggle(c.id)}
                      testID={`cat-${c.id}`}
                      style={[styles.catChip, on && styles.catChipOn]}
                    >
                      <Ionicons
                        name={on ? "checkbox" : "square-outline"}
                        size={20}
                        color={on ? colors.onBrandPrimary : colors.onSurface}
                      />
                      <Text style={[styles.catTxt, on && styles.catTxtOn]}>{c.label}</Text>
                    </Pressable>
                  );
                })}
              </View>
            </View>
          )}

          {step === 3 && (
            <View style={styles.section} testID="onboarding-step-2">
              <Text style={styles.fieldLabel}>ETÀ</Text>
              <TextInput
                testID="age-input"
                style={styles.input}
                placeholder="es. 27"
                placeholderTextColor={colors.muted}
                value={age}
                onChangeText={setAge}
                keyboardType="number-pad"
                maxLength={3}
              />

              <Text style={[styles.fieldLabel, { marginTop: spacing.lg }]}>SESSO</Text>
              <View style={styles.sexRow}>
                {([
                  { k: "F", label: "Femmina" },
                  { k: "M", label: "Maschio" },
                  { k: "other", label: "Altro" },
                  { k: "na", label: "Preferisco non dirlo" },
                ] as { k: Sex; label: string }[]).map((s) => (
                  <Pressable
                    key={s.k}
                    onPress={() => setSex(s.k)}
                    testID={`sex-${s.k}`}
                    style={[styles.sexBtn, sex === s.k && styles.sexBtnOn]}
                  >
                    <Text style={[styles.sexTxt, sex === s.k && styles.sexTxtOn]}>{s.label}</Text>
                  </Pressable>
                ))}
              </View>
            </View>
          )}

          {step === 4 && (
            <View style={styles.section} testID="onboarding-step-3">
              <Text style={styles.sectionHint}>Scegli la tua regione di provenienza.</Text>
              <Pressable onPress={() => setRegionOpen(true)} testID="region-open" style={styles.regionBtn}>
                <Text style={[styles.regionBtnTxt, !region && { color: colors.muted }]}>
                  {region || "Seleziona regione"}
                </Text>
                <Ionicons name="chevron-down" size={20} color={colors.onSurface} />
              </Pressable>
            </View>
          )}

          {step === 5 && (
            <View style={styles.section} testID="onboarding-step-4">
              <Text style={styles.sectionHint}>Che lavoro fai? Ci aiuta a capire meglio la community.</Text>
              <Pressable onPress={() => setProfessionOpen(true)} testID="profession-open" style={styles.regionBtn}>
                <Text style={[styles.regionBtnTxt, !profession && { color: colors.muted }]}>
                  {profession || "Seleziona professione"}
                </Text>
                <Ionicons name="chevron-down" size={20} color={colors.onSurface} />
              </Pressable>
            </View>
          )}

          {error && <Text style={styles.error} testID="onboarding-error">{error}</Text>}
        </ScrollView>

        <View style={styles.footer}>
          {!isFirstStep ? (
            <Pressable onPress={goBack} testID="onboarding-back" style={styles.backBtn}>
              <Ionicons name="chevron-back" size={20} color={colors.onSurface} />
              <Text style={styles.backTxt}>INDIETRO</Text>
            </Pressable>
          ) : (
            <Pressable
              onPress={async () => {
                // On the very first step, "INDIETRO" cancels the onboarding
                // entirely and returns the user to the login screen. We log
                // out silently so the token from the fresh signup doesn't
                // auto-login them the moment they hit /auth.
                try { await logout(); } catch { /* silent */ }
                router.replace("/auth");
              }}
              testID="onboarding-cancel"
              style={styles.backBtn}
            >
              <Ionicons name="chevron-back" size={20} color={colors.onSurface} />
              <Text style={styles.backTxt}>INDIETRO</Text>
            </Pressable>
          )}
          {step < 5 ? (
            <Pressable onPress={goNext} testID="onboarding-next" style={styles.cta}>
              <Text style={styles.ctaTxt}>AVANTI ›</Text>
            </Pressable>
          ) : (
            <Pressable onPress={submit} disabled={submitting} testID="onboarding-submit" style={styles.cta}>
              {submitting ? (
                <ActivityIndicator color={colors.onBrandPrimary} />
              ) : (
                <Text style={styles.ctaTxt}>SALVA E CONTINUA</Text>
              )}
            </Pressable>
          )}
        </View>
      </KeyboardAvoidingView>

      <Modal visible={regionOpen} animationType="slide" transparent onRequestClose={() => setRegionOpen(false)}>
        <View style={styles.modalBackdrop}>
          <View style={styles.modalSheet}>
            <View style={styles.modalHead}>
              <Text style={styles.modalTitle}>REGIONE</Text>
              <Pressable onPress={() => setRegionOpen(false)} testID="region-close">
                <Ionicons name="close" size={26} color={colors.onSurfaceInverse} />
              </Pressable>
            </View>
            <FlatList
              data={REGIONS}
              keyExtractor={(r) => r}
              renderItem={({ item }) => (
                <Pressable
                  onPress={() => { setRegion(item); setRegionOpen(false); }}
                  style={[styles.regionItem, region === item && styles.regionItemOn]}
                  testID={`region-${item.replace(/[^a-zA-Z0-9]/g, "-")}`}
                >
                  <Text style={[styles.regionItemTxt, region === item && styles.regionItemTxtOn]}>
                    {item}
                  </Text>
                  {region === item && <Ionicons name="checkmark" size={20} color={colors.onBrandPrimary} />}
                </Pressable>
              )}
            />
          </View>
        </View>
      </Modal>

      <Modal visible={professionOpen} animationType="slide" transparent onRequestClose={() => setProfessionOpen(false)}>
        <View style={styles.modalBackdrop}>
          <View style={styles.modalSheet}>
            <View style={styles.modalHead}>
              <Text style={styles.modalTitle}>PROFESSIONE</Text>
              <Pressable onPress={() => setProfessionOpen(false)} testID="profession-close">
                <Ionicons name="close" size={26} color={colors.onSurfaceInverse} />
              </Pressable>
            </View>
            <FlatList
              data={professions}
              keyExtractor={(r) => r}
              renderItem={({ item }) => (
                <Pressable
                  onPress={() => { setProfession(item); setProfessionOpen(false); }}
                  style={[styles.regionItem, profession === item && styles.regionItemOn]}
                  testID={`profession-${item.replace(/[^a-zA-Z0-9]/g, "-")}`}
                >
                  <Text style={[styles.regionItemTxt, profession === item && styles.regionItemTxtOn]}>
                    {item}
                  </Text>
                  {profession === item && <Ionicons name="checkmark" size={20} color={colors.onBrandPrimary} />}
                </Pressable>
              )}
            />
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: { padding: spacing.lg, borderBottomWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceInverse },
  progressRow: { flexDirection: "row", gap: spacing.xs, marginBottom: spacing.md },
  progressDot: { flex: 1, height: 6, backgroundColor: colors.surfaceTertiary, borderWidth: 2, borderColor: colors.border },
  progressDotOn: { backgroundColor: colors.brandSecondary },
  brand: { fontSize: font.sizes.xxxl, fontWeight: "500", letterSpacing: 1, color: colors.onSurfaceInverse },
  tagline: { fontSize: font.sizes.sm, letterSpacing: 2, color: colors.brandSecondary, marginTop: spacing.xs },
  content: { padding: spacing.lg, paddingBottom: spacing.xl, gap: spacing.lg },
  section: { gap: spacing.md },
  sectionTitle: { fontSize: font.sizes.xl, letterSpacing: 2, fontWeight: "500", color: colors.onSurface },
  sectionHint: { fontSize: font.sizes.base, color: colors.muted },
  hintTiny: { fontSize: font.sizes.xs, color: colors.muted, letterSpacing: 1 },
  nickInputWrap: {
    flexDirection: "row", alignItems: "center", borderWidth: 2, borderColor: colors.border,
    backgroundColor: colors.surfaceSecondary, paddingHorizontal: spacing.md,
  },
  nickAt: { fontSize: font.sizes.xxl, color: colors.brandPrimary, marginRight: 4, fontWeight: "500" },
  nickInput: { flex: 1, paddingVertical: spacing.md, fontSize: font.sizes.lg, color: colors.onSurface },
  selectAllRow: { alignSelf: "flex-start", flexDirection: "row", alignItems: "center", gap: 6, borderWidth: 2, borderColor: colors.border, paddingHorizontal: spacing.sm, paddingVertical: 6, backgroundColor: colors.surfaceSecondary },
  selectAllTxt: { fontSize: font.sizes.xs, letterSpacing: 1, fontWeight: "500", color: colors.onSurface },
  catsGrid: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm },
  catChip: { flexDirection: "row", alignItems: "center", gap: 8, borderWidth: 2, borderColor: colors.border, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, backgroundColor: colors.surfaceSecondary },
  catChipOn: { backgroundColor: colors.brandPrimary, borderColor: colors.brandPrimary },
  catTxt: { fontSize: font.sizes.base, color: colors.onSurface, fontWeight: "500" },
  catTxtOn: { color: colors.onBrandPrimary },
  fieldLabel: { fontSize: font.sizes.xs, letterSpacing: 2, color: colors.muted },
  input: { borderWidth: 2, borderColor: colors.border, padding: spacing.md, fontSize: font.sizes.lg, color: colors.onSurface, backgroundColor: colors.surfaceSecondary, marginTop: 4 },
  sexRow: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm, marginTop: 4 },
  sexBtn: { borderWidth: 2, borderColor: colors.border, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, backgroundColor: colors.surfaceSecondary },
  sexBtnOn: { backgroundColor: colors.brandSecondary, borderColor: colors.brandSecondary },
  sexTxt: { fontSize: font.sizes.base, color: colors.onSurface },
  sexTxtOn: { color: colors.onBrandSecondary, fontWeight: "500" },
  regionBtn: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", borderWidth: 2, borderColor: colors.border, padding: spacing.md, backgroundColor: colors.surfaceSecondary },
  regionBtnTxt: { fontSize: font.sizes.lg, color: colors.onSurface },
  error: { color: colors.error, borderWidth: 2, borderColor: colors.error, padding: spacing.sm, fontSize: font.sizes.base },
  footer: { flexDirection: "row", gap: spacing.sm, padding: spacing.lg, borderTopWidth: 2, borderColor: colors.border, backgroundColor: colors.surface },
  backBtn: { flexDirection: "row", alignItems: "center", gap: 4, borderWidth: 2, borderColor: colors.border, paddingVertical: spacing.md, paddingHorizontal: spacing.lg, backgroundColor: colors.surfaceSecondary },
  backTxt: { fontSize: font.sizes.base, letterSpacing: 2, fontWeight: "500", color: colors.onSurface },
  cta: { flex: 1, backgroundColor: colors.brandPrimary, borderWidth: 2, borderColor: colors.border, paddingVertical: spacing.md, alignItems: "center", justifyContent: "center" },
  ctaFull: { flex: 1 },
  ctaTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.lg, letterSpacing: 2, fontWeight: "500" },
  modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "flex-end" },
  modalSheet: { backgroundColor: colors.surface, borderTopWidth: 2, borderColor: colors.border, maxHeight: "75%" },
  modalHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", padding: spacing.lg, borderBottomWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceInverse },
  modalTitle: { color: colors.onSurfaceInverse, fontSize: font.sizes.xl, letterSpacing: 2, fontWeight: "500" },
  regionItem: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", padding: spacing.md, borderBottomWidth: 1, borderColor: colors.border },
  regionItemOn: { backgroundColor: colors.brandPrimary },
  regionItemTxt: { fontSize: font.sizes.lg, color: colors.onSurface },
  regionItemTxtOn: { color: colors.onBrandPrimary, fontWeight: "500" },
});
