import { useState } from "react";
import {
  View, Text, StyleSheet, TextInput, Pressable, ScrollView,
  KeyboardAvoidingView, Platform, ActivityIndicator,
} from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { useAuth } from "@/src/auth/AuthContext";
import { colors, spacing, font } from "@/src/theme";

type Mode = "email" | "google" | "anon";

export default function AuthScreen() {
  const router = useRouter();
  const { login, signup, anonymous, loginWithGoogle } = useAuth();
  const [mode, setMode] = useState<Mode>("email");
  const [isSignup, setIsSignup] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [nickname, setNickname] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handle = async () => {
    setError(null); setLoading(true);
    try {
      if (mode === "email") {
        if (isSignup) await signup(email.trim(), password, nickname.trim());
        else await login(email.trim(), password);
      } else if (mode === "anon") {
        await anonymous(nickname.trim());
      } else if (mode === "google") {
        await loginWithGoogle();
      }
      router.replace("/(tabs)");
    } catch (e: any) {
      setError(e?.message || "Errore");
    } finally { setLoading(false); }
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
          <View style={styles.header} testID="auth-header">
            <Text style={styles.brand}>APP DI FAIDE</Text>
            <Text style={styles.tagline}>Scegli il tuo schieramento.</Text>
          </View>

          <View style={styles.tabsRow}>
            {(["email", "google", "anon"] as Mode[]).map((m) => (
              <Pressable
                key={m}
                testID={`auth-tab-${m}`}
                style={[styles.tab, mode === m && styles.tabActive]}
                onPress={() => { setMode(m); setError(null); }}
              >
                <Text style={[styles.tabText, mode === m && styles.tabTextActive]}>
                  {m === "email" ? "EMAIL" : m === "google" ? "GOOGLE" : "ANONIMO"}
                </Text>
              </Pressable>
            ))}
          </View>

          {mode === "email" && (
            <View style={styles.form}>
              <View style={styles.switchRow}>
                <Pressable testID="auth-mode-login" onPress={() => setIsSignup(false)} style={[styles.switchBtn, !isSignup && styles.switchActive]}>
                  <Text style={[styles.switchTxt, !isSignup && styles.switchTxtActive]}>Accedi</Text>
                </Pressable>
                <Pressable testID="auth-mode-signup" onPress={() => setIsSignup(true)} style={[styles.switchBtn, isSignup && styles.switchActive]}>
                  <Text style={[styles.switchTxt, isSignup && styles.switchTxtActive]}>Registrati</Text>
                </Pressable>
              </View>

              {isSignup && (
                <TextInput
                  testID="auth-nickname-input"
                  style={styles.input}
                  placeholder="Nickname"
                  placeholderTextColor={colors.muted}
                  value={nickname}
                  onChangeText={setNickname}
                  autoCapitalize="none"
                />
              )}
              <TextInput
                testID="auth-email-input"
                style={styles.input}
                placeholder="Email"
                placeholderTextColor={colors.muted}
                value={email}
                onChangeText={setEmail}
                autoCapitalize="none"
                keyboardType="email-address"
              />
              <TextInput
                testID="auth-password-input"
                style={styles.input}
                placeholder="Password (min 6)"
                placeholderTextColor={colors.muted}
                value={password}
                onChangeText={setPassword}
                secureTextEntry
              />
            </View>
          )}

          {mode === "anon" && (
            <View style={styles.form}>
              <Text style={styles.help}>Nessuna email, nessuna password. Solo un nickname.</Text>
              <TextInput
                testID="auth-anon-nickname-input"
                style={styles.input}
                placeholder="Nickname"
                placeholderTextColor={colors.muted}
                value={nickname}
                onChangeText={setNickname}
                autoCapitalize="none"
              />
            </View>
          )}

          {mode === "google" && (
            <View style={styles.form}>
              <Text style={styles.help}>Accedi rapidamente con il tuo account Google.</Text>
            </View>
          )}

          {error && <Text style={styles.error} testID="auth-error">{error}</Text>}

          <Pressable testID="auth-submit-button" style={styles.cta} onPress={handle} disabled={loading}>
            {loading ? (
              <ActivityIndicator color={colors.onBrandPrimary} />
            ) : (
              <Text style={styles.ctaText}>
                {mode === "google"
                  ? "CONTINUA CON GOOGLE"
                  : mode === "anon"
                  ? "ENTRA COME ANONIMO"
                  : isSignup
                  ? "CREA ACCOUNT"
                  : "ACCEDI"}
              </Text>
            )}
          </Pressable>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  scroll: { padding: spacing.lg, paddingBottom: spacing.xxl },
  header: { paddingVertical: spacing.xl, borderBottomWidth: 2, borderColor: colors.border, marginBottom: spacing.lg },
  brand: { fontSize: font.sizes.giant, color: colors.onSurface, letterSpacing: 1, fontWeight: "500" },
  tagline: { fontSize: font.sizes.lg, color: colors.onSurface, marginTop: spacing.xs },
  tabsRow: { flexDirection: "row", gap: spacing.sm, marginBottom: spacing.lg },
  tab: { flex: 1, paddingVertical: spacing.md, borderWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceSecondary, alignItems: "center" },
  tabActive: { backgroundColor: colors.surfaceInverse },
  tabText: { fontSize: font.sizes.base, color: colors.onSurface, letterSpacing: 1 },
  tabTextActive: { color: colors.onSurfaceInverse },
  form: { gap: spacing.md, marginBottom: spacing.lg },
  switchRow: { flexDirection: "row", gap: spacing.sm },
  switchBtn: { flex: 1, paddingVertical: spacing.sm, borderWidth: 2, borderColor: colors.border, alignItems: "center", backgroundColor: colors.surfaceSecondary },
  switchActive: { backgroundColor: colors.brandSecondary },
  switchTxt: { color: colors.onSurface, fontSize: font.sizes.base },
  switchTxtActive: { color: colors.onBrandSecondary, fontWeight: "500" },
  input: { borderWidth: 2, borderColor: colors.border, padding: spacing.md, fontSize: font.sizes.lg, color: colors.onSurface, backgroundColor: colors.surfaceSecondary },
  help: { fontSize: font.sizes.base, color: colors.muted },
  error: { color: colors.error, fontSize: font.sizes.base, marginBottom: spacing.md, borderWidth: 2, borderColor: colors.error, padding: spacing.sm },
  cta: { backgroundColor: colors.brandPrimary, borderWidth: 2, borderColor: colors.border, paddingVertical: spacing.lg, alignItems: "center" },
  ctaText: { color: colors.onBrandPrimary, fontSize: font.sizes.xl, letterSpacing: 1, fontWeight: "500" },
});
