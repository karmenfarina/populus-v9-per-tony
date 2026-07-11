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
  const { user, refreshMe } = useAuth();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [cats, setCats] = useState<{ id: string; label: string }[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [age, setAge] = useState<string>("");
  const [sex, setSex] = useState<Sex | null>(null);
  const [region, setRegion] = useState<string>("");
  const [regionOpen, setRegionOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    (async () => {
      const r = await api.categories();
      setCats(r.categories);
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

  const goNext = () => {
    setError(null);
    if (step === 1) {
      if (selected.size === 0) { setError("Scegli almeno una categoria preferita"); return; }
      setStep(2);
    } else if (step === 2) {
      const ageNum = parseInt(age, 10);
      if (!ageNum || ageNum < 13 || ageNum > 120) { setError("Inserisci un'età valida (13-120)"); return; }
      if (!sex) { setError("Seleziona il sesso"); return; }
      setStep(3);
    }
  };

  const goBack = () => {
    setError(null);
    if (step === 2) setStep(1);
    else if (step === 3) setStep(2);
  };

  const submit = async () => {
    setError(null);
    if (!region) { setError("Seleziona la regione"); return; }
    setSubmitting(true);
    try {
      const ageNum = parseInt(age, 10);
      await api.updateProfile({
        age: ageNum,
        sex: sex as Sex,
        region,
        favorite_categories: Array.from(selected),
      });
      await refreshMe();
      router.replace("/(tabs)");
    } catch (e: any) {
      setError(e?.message || "Errore durante il salvataggio");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]} testID="onboarding-screen">
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <View style={styles.header}>
          <View style={styles.progressRow}>
            {[1, 2, 3].map((n) => (
              <View
                key={n}
                testID={`progress-${n}`}
                style={[styles.progressDot, step >= (n as 1 | 2 | 3) && styles.progressDotOn]}
              />
            ))}
          </View>
          <Text style={styles.brand} testID="onboarding-step-brand">
            {step === 1 && (
              <>BENVENUTO{user?.nickname ? `, @${user.nickname}` : ""}</>
            )}
            {step === 2 && "CHI SEI"}
            {step === 3 && "DA DOVE VIENI"}
          </Text>
          <Text style={styles.tagline}>Step {step} di 3</Text>
        </View>

        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          {step === 1 && (
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

          {step === 2 && (
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

          {step === 3 && (
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

          {error && <Text style={styles.error} testID="onboarding-error">{error}</Text>}
        </ScrollView>

        <View style={styles.footer}>
          {step > 1 && (
            <Pressable onPress={goBack} testID="onboarding-back" style={styles.backBtn}>
              <Ionicons name="chevron-back" size={20} color={colors.onSurface} />
              <Text style={styles.backTxt}>INDIETRO</Text>
            </Pressable>
          )}
          {step < 3 ? (
            <Pressable onPress={goNext} testID="onboarding-next" style={[styles.cta, step === 1 && styles.ctaFull]}>
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
