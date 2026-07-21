import { useState } from "react";
import {
  View, Text, StyleSheet, TextInput, Pressable, ScrollView,
  KeyboardAvoidingView, Platform, ActivityIndicator, Image,
} from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api";
import { colors, spacing, font } from "@/src/theme";
import { sanitizeNicknameInput, validateNickname, NICKNAME_MAX } from "@/src/utils/nickname";

type Mode = "email" | "google" | "anon";

export default function AuthScreen() {
  const router = useRouter();
  const { login, signup, anonymous, loginWithGoogle } = useAuth();
  // NOTE: The EMAIL tab is currently HIDDEN from the UI (verification email
  // delivery isn't operational yet). All the email login/signup code below
  // is intentionally preserved and functional — flip AUTH_EMAIL_ENABLED to
  // `true` (or remove the guards on the tabs render/default mode) to bring
  // the tab back without any other changes.
  const AUTH_EMAIL_ENABLED = false;
  const VISIBLE_MODES: Mode[] = AUTH_EMAIL_ENABLED
    ? (["email", "google", "anon"] as Mode[])
    : (["google", "anon"] as Mode[]);
  const [mode, setMode] = useState<Mode>(AUTH_EMAIL_ENABLED ? "email" : "google");
  const [isSignup, setIsSignup] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [nickname, setNickname] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingVerify, setPendingVerify] = useState<string | null>(null); // email awaiting verification
  const [resending, setResending] = useState(false);

  const handle = async () => {
    setError(null);
    // Client-side validation for immediate, clear feedback.
    if (mode === "email") {
      const e = email.trim();
      if (!e) { setError("Inserisci la tua email."); return; }
      if (!/^\S+@\S+\.\S+$/.test(e)) { setError("Inserisci un indirizzo email valido."); return; }
      if (!password) { setError("Inserisci la password."); return; }
      if (password.length < 6) { setError("La password deve avere almeno 6 caratteri."); return; }
      if (isSignup) {
        const nickErr = validateNickname(nickname);
        if (nickErr) { setError(nickErr); return; }
        if (password !== confirmPassword) { setError("Le password non coincidono."); return; }
      }
    } else if (mode === "anon") {
      const nickErr = validateNickname(nickname);
      if (nickErr) { setError(nickErr); return; }
    }
    setLoading(true);
    try {
      if (mode === "email") {
        if (isSignup) await signup(email.trim(), password, nickname.trim());
        else await login(email.trim(), password);
      } else if (mode === "anon") {
        await anonymous(nickname.trim());
      } else if (mode === "google") {
        await loginWithGoogle();
      }
      // Route based on onboarding: index.tsx will handle deep redirect on next mount,
      // but here we push explicitly for immediacy.
      router.replace("/");
    } catch (e: any) {
      // Signup flow now stops on `requires_verification`: show a "check your
      // inbox" panel with a resend CTA, no session token was issued.
      if (e?.requires_verification) {
        setPendingVerify(e.email || email.trim());
        setError(null);
        return;
      }
      // Backend blocks login on unverified email accounts with 403 +
      // structured detail `{email_not_verified: true, email}`. Surface the
      // same inbox panel so the user can resend.
      const d = e?.detail;
      if (d && typeof d === 'object' && d.email_not_verified) {
        setPendingVerify(d.email || email.trim());
        setError(null);
        return;
      }
      // The API layer already returns human-friendly Italian messages via
      // ApiError.detail. Any other thrown value falls back to a generic label.
      setError(typeof d === 'string' ? d : (e?.message || "Errore imprevisto. Riprova."));
    } finally { setLoading(false); }
  };

  const doResend = async () => {
    if (!pendingVerify) return;
    setResending(true);
    setError(null);
    try {
      const res: any = await api.resendVerification(pendingVerify);
      setError(res?.message || "Email di verifica inviata. Controlla la casella.");
    } catch (e: any) {
      setError(e?.detail || e?.message || "Errore invio email.");
    } finally { setResending(false); }
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
          <View style={styles.header} testID="auth-header">
            <Image
              source={require("../assets/images/icon.png")}
              style={styles.logo}
              resizeMode="contain"
              testID="auth-logo"
            />
            <Text style={styles.brand}>POPULUS</Text>
            <Text style={styles.tagline}>Entra nel dibattito.</Text>
          </View>

          <View style={styles.tabsRow}>
            {VISIBLE_MODES.map((m) => (
              <Pressable
                key={m}
                testID={`auth-tab-${m}`}
                style={[styles.tab, mode === m && styles.tabActive]}
                onPress={async () => {
                  setError(null);
                  setPendingVerify(null);
                  // Google is a one-tap flow: fire the redirect immediately
                  // instead of switching the form layout (avoids a UI flash
                  // before the browser leaves the app).
                  if (m === "google") {
                    setLoading(true);
                    try { await loginWithGoogle(); }
                    catch (e: any) { setError(e?.message || "Errore"); }
                    finally { setLoading(false); }
                    return;
                  }
                  setMode(m);
                }}
              >
                <Text style={[styles.tabText, mode === m && styles.tabTextActive]}>
                  {m === "email" ? "EMAIL" : m === "google" ? "GOOGLE" : "ANONIMO"}
                </Text>
              </Pressable>
            ))}
          </View>

          {pendingVerify ? (
            <View style={styles.form} testID="verify-panel">
              <View style={styles.verifyBox}>
                <Ionicons name="mail-open-outline" size={54} color={colors.brandPrimary} style={{ alignSelf: "center", marginBottom: spacing.sm }} />
                <Text style={styles.verifyTitle}>CONTROLLA LA TUA EMAIL</Text>
                <Text style={styles.verifyBody}>
                  Ti abbiamo inviato un link di verifica a{"\n"}
                  <Text style={{ fontWeight: "700" }}>{pendingVerify}</Text>{"\n\n"}
                  Clicca sul link per attivare l&apos;account. Se non lo trovi, controlla la cartella spam.
                </Text>
                {error && <Text style={styles.error}>{error}</Text>}
                <Pressable
                  onPress={doResend}
                  disabled={resending}
                  style={[styles.cta, resending && { opacity: 0.6 }]}
                  testID="verify-resend"
                >
                  <Text style={styles.ctaTxt}>{resending ? "INVIO..." : "REINVIA EMAIL"}</Text>
                </Pressable>
                <Pressable
                  onPress={() => { setPendingVerify(null); setError(null); }}
                  style={styles.verifyBack}
                  testID="verify-back"
                >
                  <Text style={styles.verifyBackTxt}>Torna al login</Text>
                </Pressable>
              </View>
            </View>
          ) : mode === "email" && (
            <View style={styles.form}>
              <View style={styles.switchRow}>
                <Pressable testID="auth-mode-login" onPress={() => { setIsSignup(false); setConfirmPassword(""); setError(null); }} style={[styles.switchBtn, !isSignup && styles.switchActive]}>
                  <Text style={[styles.switchTxt, !isSignup && styles.switchTxtActive]}>Accedi</Text>
                </Pressable>
                <Pressable testID="auth-mode-signup" onPress={() => { setIsSignup(true); setError(null); }} style={[styles.switchBtn, isSignup && styles.switchActive]}>
                  <Text style={[styles.switchTxt, isSignup && styles.switchTxtActive]}>Registrati</Text>
                </Pressable>
              </View>

              {isSignup && (
                <TextInput
                  testID="auth-nickname-input"
                  style={styles.input}
                  placeholder="Nickname (solo lettere, numeri, . e _)"
                  placeholderTextColor={colors.muted}
                  value={nickname}
                  onChangeText={(t) => setNickname(sanitizeNicknameInput(t))}
                  autoCapitalize="none"
                  autoCorrect={false}
                  maxLength={NICKNAME_MAX}
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
              {isSignup && (
                <TextInput
                  testID="auth-password-confirm-input"
                  style={[
                    styles.input,
                    // Live mismatch cue: red border once the user starts typing
                    // the confirmation AND it differs from the primary field.
                    confirmPassword.length > 0 && confirmPassword !== password
                      ? { borderColor: colors.error }
                      : null,
                  ]}
                  placeholder="Conferma password"
                  placeholderTextColor={colors.muted}
                  value={confirmPassword}
                  onChangeText={setConfirmPassword}
                  secureTextEntry
                />
              )}
            </View>
          )}

          {mode === "anon" && (
            <View style={styles.form}>
              <Text style={styles.help}>Nessuna email, nessuna password. Solo un nickname.</Text>
              <TextInput
                testID="auth-anon-nickname-input"
                style={styles.input}
                placeholder="Nickname (solo lettere, numeri, . e _)"
                placeholderTextColor={colors.muted}
                value={nickname}
                onChangeText={(t) => setNickname(sanitizeNicknameInput(t))}
                autoCapitalize="none"
                autoCorrect={false}
                maxLength={NICKNAME_MAX}
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
  header: { paddingVertical: spacing.xl, borderBottomWidth: 2, borderColor: colors.border, marginBottom: spacing.lg, alignItems: "center" },
  logo: { width: 120, height: 120, marginBottom: spacing.md },
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
  verifyBox: {
    padding: spacing.lg,
    borderWidth: 2,
    borderColor: colors.brandPrimary,
    backgroundColor: colors.surfaceSecondary,
    gap: spacing.sm,
  },
  verifyTitle: {
    color: colors.onSurface,
    fontSize: font.sizes.lg,
    letterSpacing: 2,
    fontWeight: "500",
    textAlign: "center",
    marginBottom: spacing.xs,
  },
  verifyBody: {
    color: colors.onSurface,
    fontSize: font.sizes.base,
    textAlign: "center",
    lineHeight: 22,
  },
  ctaTxt: { color: colors.onBrandPrimary, letterSpacing: 2, fontWeight: "500", textAlign: "center" },
  verifyBack: { marginTop: spacing.sm, alignItems: "center" },
  verifyBackTxt: { color: colors.muted, fontSize: font.sizes.sm, textDecorationLine: "underline" },
});
