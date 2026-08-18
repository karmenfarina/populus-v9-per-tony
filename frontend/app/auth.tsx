import { useState } from "react";
import {
  View, Text, StyleSheet, TextInput, Pressable, ScrollView,
  KeyboardAvoidingView, Platform, ActivityIndicator, Image,
} from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons, MaterialCommunityIcons } from "@expo/vector-icons";
import { useAuth } from "@/src/auth/AuthContext";
// import { api } from "@/src/api"; // no longer used — Firebase handles email flow
import { colors, spacing, font, radius } from "@/src/theme";
import { sanitizeNicknameInput, validateNickname, NICKNAME_MAX } from "@/src/utils/nickname";

type Mode = "email" | "google" | "anon";

export default function AuthScreen() {
  const router = useRouter();
  // `login` and `signup` (legacy backend endpoints) are intentionally
  // not destructured — the email tab is now backed by Firebase.
  const { anonymous, loginWithGoogle, firebaseSignup, firebaseLogin, firebaseResendVerification } = useAuth();
  // Email/password is now backed by Firebase Auth (verification email
  // sent by Firebase from noreply@populus-1f567.firebaseapp.com — no
  // custom SMTP or domain required). Legacy backend signup/login are
  // kept in the code as fallback but the tab now routes to Firebase.
  const AUTH_EMAIL_ENABLED = true;
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
        if (isSignup) {
          // Firebase creates the account + auto-sends the verification
          // email. We surface the "check your inbox" panel and stop —
          // the user must click the link before we mint a session.
          await firebaseSignup(email.trim(), password);
          setPendingVerify(email.trim());
          setError(null);
          return;
        }
        await firebaseLogin(email.trim(), password);
      } else if (mode === "anon") {
        await anonymous(nickname.trim());
      } else if (mode === "google") {
        await loginWithGoogle();
        // On web, loginWithGoogle sets `window.location.href` and returns
        // synchronously — the browser navigates away moments later. If we
        // fall through to `router.replace("/")` in that tiny gap, Expo
        // Router re-mounts `auth.tsx`, which resets `mode` to its default
        // ("email") and flashes the wrong tab for one frame before the
        // actual Google redirect happens. On native the WebBrowser flow
        // is fully awaited so router.replace below is safe and needed.
        if (Platform.OS === 'web') return;
      }
      // Route based on onboarding: index.tsx will handle deep redirect on next mount,
      // but here we push explicitly for immediacy.
      router.replace("/");
    } catch (e: any) {
      // Firebase login throws with code 'auth/email-not-verified' when
      // the user tries to log in before clicking the link. Route to
      // the "check your inbox" panel with a resend CTA.
      if (e?.code === 'auth/email-not-verified') {
        setPendingVerify(email.trim());
        setError(null);
        return;
      }
      // Firebase error code → friendly Italian message.
      const fbCode = e?.code as string | undefined;
      if (fbCode) {
        const fbMsg: Record<string, string> = {
          'auth/email-already-in-use': "Email già registrata. Prova ad accedere invece.",
          'auth/invalid-email': "Indirizzo email non valido.",
          'auth/weak-password': "Password troppo debole (minimo 6 caratteri).",
          'auth/user-not-found': "Nessun account con questa email.",
          'auth/wrong-password': "Password sbagliata.",
          'auth/invalid-credential': "Email o password sbagliata.",
          'auth/too-many-requests': "Troppi tentativi. Riprova più tardi.",
          'auth/network-request-failed': "Nessuna connessione. Riprova.",
        };
        if (fbMsg[fbCode]) { setError(fbMsg[fbCode]); return; }
      }
      // Legacy backend signup flow used `requires_verification`; kept
      // for the (unused) legacy signup/login paths.
      if (e?.requires_verification) {
        setPendingVerify(e.email || email.trim());
        setError(null);
        return;
      }
      const d = e?.detail;
      if (d && typeof d === 'object' && d.email_not_verified) {
        setPendingVerify(d.email || email.trim());
        setError(null);
        return;
      }
      setError(typeof d === 'string' ? d : (e?.message || "Errore imprevisto. Riprova."));
    } finally { setLoading(false); }
  };

  const doResend = async () => {
    if (!pendingVerify) return;
    setResending(true);
    setError(null);
    try {
      // Firebase resend works on the currently-authenticated user.
      // If we're here right after signup Firebase already holds a
      // reference, otherwise we sign the user in again silently to
      // get a fresh handle. The email/password must still be typed
      // in the form fields.
      try {
        await firebaseResendVerification();
      } catch {
        // Fallback: relog silently to reattach a Firebase user, then resend.
        if (password) {
          await firebaseLogin(email.trim(), password).catch(() => {});
          await firebaseResendVerification();
        } else {
          throw new Error("Reinserisci la password per ricevere di nuovo l'email.");
        }
      }
      setError("Email di verifica inviata. Controlla la casella (e la cartella spam).");
    } catch (e: any) {
      const fbCode = e?.code as string | undefined;
      const fbMsg: Record<string, string> = {
        'auth/too-many-requests': "Troppi tentativi. Aspetta qualche minuto e riprova.",
        'auth/network-request-failed': "Nessuna connessione. Riprova.",
        'auth/user-not-found': "Nessun account con questa email.",
      };
      setError((fbCode && fbMsg[fbCode]) || e?.message || "Errore invio email.");
    } finally { setResending(false); }
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
          <View style={styles.header} testID="auth-header">
            <Image
              source={require("../assets/images/icon-dark.png")}
              style={styles.logo}
              resizeMode="contain"
              testID="auth-logo"
            />
            <Text style={styles.brand}>POPULUS</Text>
            <Text style={styles.tagline}>Entra nel dibattito.</Text>
          </View>

          <View style={styles.tabsRow}>
            {VISIBLE_MODES.map((m) => {
              const active = mode === m;
              const iconColor = active ? colors.brandSecondary : colors.onSurface;
              const label = m === "email" ? "EMAIL" : m === "google" ? "GOOGLE" : "ANONIMO";
              return (
                <Pressable
                  key={m}
                  testID={`auth-tab-${m}`}
                  style={[styles.tab, active && styles.tabActive]}
                  onPress={() => {
                    setError(null);
                    setPendingVerify(null);
                    setMode(m);
                  }}
                >
                  {m === "email" ? (
                    <Ionicons name="mail-outline" size={18} color={iconColor} />
                  ) : m === "google" ? (
                    <Ionicons name="logo-google" size={18} color={iconColor} />
                  ) : (
                    <MaterialCommunityIcons name="incognito" size={18} color={iconColor} />
                  )}
                  <Text style={[styles.tabText, active && styles.tabTextActive]}>
                    {label}
                  </Text>
                </Pressable>
              );
            })}
          </View>

          {pendingVerify ? (
            <View style={styles.form} testID="verify-panel">
              <View style={styles.verifyBox}>
                <Ionicons name="mail-open-outline" size={54} color={colors.brandPrimary} style={{ alignSelf: "center", marginBottom: spacing.sm }} />
                <Text style={styles.verifyTitle}>CONTROLLA LA TUA EMAIL</Text>
                <Text style={styles.verifyBody}>
                  Ti abbiamo inviato un link di verifica a{"\n"}
                  <Text style={{ fontWeight: "700" }}>{pendingVerify}</Text>{"\n\n"}
                  Clicca sul link per attivare l&apos;account.
                </Text>
                <View style={styles.spamWarning} testID="verify-spam-warning">
                  <Ionicons name="warning" size={20} color={colors.brandPrimary} />
                  <View style={{ flex: 1 }}>
                    <Text style={styles.spamTitle}>NON TROVI L&apos;EMAIL?</Text>
                    <Text style={styles.spamBody}>
                      Controlla la cartella <Text style={{ fontWeight: "700" }}>Spam</Text> o{" "}
                      <Text style={{ fontWeight: "700" }}>Promozioni</Text>. Il mittente è{" "}
                      <Text style={{ fontWeight: "700" }}>noreply@populus-1f567.firebaseapp.com</Text>.
                      Segnala come &quot;Non spam&quot; per ricevere le prossime email.
                    </Text>
                  </View>
                </View>
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
              {isSignup && (
                <View style={styles.signupHint} testID="signup-spam-hint">
                  <Ionicons name="information-circle" size={16} color={colors.brandPrimary} />
                  <Text style={styles.signupHintTxt}>
                    Dopo la registrazione riceverai un&apos;email di verifica.{" "}
                    <Text style={{ fontWeight: "700" }}>Controlla anche lo spam</Text>{" "}
                    o le promozioni se non la trovi in posta.
                  </Text>
                </View>
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
                onChangeText={(t) => {
                  // Sanitize on every keystroke so the input only ever
                  // holds a valid nickname AND clear any stale error the
                  // previous submit may have surfaced (otherwise the
                  // user sees a red banner even while typing).
                  setNickname(sanitizeNicknameInput(t));
                  if (error) setError(null);
                }}
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
              <View style={styles.ctaInner}>
                {mode === "google" ? (
                  <Ionicons name="logo-google" size={20} color={colors.onBrandPrimary} />
                ) : null}
                <Text style={styles.ctaText}>
                  {mode === "google"
                    ? "CONTINUA CON GOOGLE"
                    : mode === "anon"
                    ? "ENTRA COME ANONIMO"
                    : isSignup
                    ? "CREA ACCOUNT"
                    : "ACCEDI"}
                </Text>
              </View>
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
  header: { paddingVertical: spacing.xl, marginBottom: spacing.lg, alignItems: "center" },
  logo: { width: 132, height: 132, marginBottom: spacing.md, borderRadius: radius.lg },
  brand: { fontSize: font.sizes.giant, color: colors.onSurface, letterSpacing: 1, fontWeight: "800" },
  tagline: { fontSize: font.sizes.lg, color: colors.muted, marginTop: spacing.xs },
  tabsRow: { flexDirection: "row", gap: spacing.sm, marginBottom: spacing.lg, paddingTop: spacing.md, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border },
  tab: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    paddingVertical: spacing.md,
    borderWidth: 1.5,
    borderColor: colors.borderStrong,
    borderRadius: radius.md,
    backgroundColor: "transparent",
  },
  tabActive: { borderColor: colors.brandSecondary, backgroundColor: "rgba(255,199,0,0.08)" },
  tabText: { fontSize: font.sizes.base, color: colors.onSurface, letterSpacing: 1, fontWeight: "700" },
  tabTextActive: { color: colors.brandSecondary, fontWeight: "800" },
  form: { gap: spacing.md, marginBottom: spacing.lg },
  switchRow: { flexDirection: "row", gap: spacing.sm },
  switchBtn: { flex: 1, paddingVertical: spacing.sm, borderWidth: 1.5, borderColor: colors.borderStrong, borderRadius: radius.md, alignItems: "center", backgroundColor: "transparent" },
  switchActive: { backgroundColor: colors.brandSecondary, borderColor: colors.brandSecondary },
  switchTxt: { color: colors.onSurface, fontSize: font.sizes.base, fontWeight: "600" },
  switchTxtActive: { color: colors.onBrandSecondary, fontWeight: "800" },
  input: {
    borderWidth: 1.5,
    borderColor: colors.borderStrong,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    fontSize: font.sizes.lg,
    color: colors.onSurface,
    backgroundColor: colors.surfaceSecondary,
  },
  help: { fontSize: font.sizes.base, color: colors.muted, textAlign: "center" },
  error: { color: colors.error, fontSize: font.sizes.base, marginBottom: spacing.md, borderWidth: 1.5, borderColor: colors.error, borderRadius: radius.sm, padding: spacing.sm },
  cta: {
    backgroundColor: colors.brandPrimary,
    borderRadius: radius.md,
    paddingVertical: spacing.lg,
    alignItems: "center",
    justifyContent: "center",
  },
  ctaInner: { flexDirection: "row", alignItems: "center", gap: 10 },
  ctaText: { color: colors.onBrandPrimary, fontSize: font.sizes.lg, letterSpacing: 1, fontWeight: "800" },
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
  spamWarning: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing.sm,
    borderWidth: 2,
    borderColor: colors.brandPrimary,
    backgroundColor: colors.surface,
    padding: spacing.md,
    marginTop: spacing.md,
    marginBottom: spacing.sm,
  },
  spamTitle: {
    color: colors.brandPrimary,
    fontSize: font.sizes.sm,
    letterSpacing: 1,
    fontWeight: "700",
    marginBottom: 4,
  },
  spamBody: {
    color: colors.onSurface,
    fontSize: font.sizes.xs,
    lineHeight: 18,
  },
  signupHint: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing.xs,
    padding: spacing.sm,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceSecondary,
    marginTop: spacing.xs,
  },
  signupHintTxt: {
    flex: 1,
    color: colors.onSurface,
    fontSize: font.sizes.xs,
    lineHeight: 16,
  },
});
