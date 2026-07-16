import React, { useCallback, useEffect, useState } from "react";
import { View, Text, StyleSheet, Pressable, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { api, ApiError, setToken } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { colors, spacing, font } from "@/src/theme";

export default function VerifyEmail() {
  const { token } = useLocalSearchParams<{ token?: string }>();
  const router = useRouter();
  const { refreshMe } = useAuth();
  const [status, setStatus] = useState<"idle" | "loading" | "ok" | "err">("idle");
  const [msg, setMsg] = useState<string>("");

  const doVerify = useCallback(async () => {
    if (!token) {
      setStatus("err");
      setMsg("Token non fornito.");
      return;
    }
    setStatus("loading");
    try {
      const res: any = await api.verifyEmail(String(token));
      if (res?.token) {
        await setToken(res.token);
        await refreshMe();
      }
      setStatus("ok");
      setMsg("Email verificata con successo. Sei loggato.");
    } catch (e: any) {
      setStatus("err");
      setMsg(e instanceof ApiError ? e.detail : (e?.message || "Verifica fallita."));
    }
  }, [token, refreshMe]);

  // Auto-verify on mount so the flow feels seamless; the token is single-use
  // and gated behind an authenticated button on the email side.
  useEffect(() => { doVerify(); }, [doVerify]);

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <View style={styles.box}>
        <Text style={styles.title}>VERIFICA EMAIL</Text>
        {status === "loading" && <ActivityIndicator size="large" color={colors.brandPrimary} />}
        {status === "ok" && (
          <>
            <View style={styles.iconOk}><Ionicons name="checkmark-circle" size={72} color={colors.brandPrimary} /></View>
            <Text style={styles.msgOk}>{msg}</Text>
            <Pressable style={styles.btn} onPress={() => router.replace("/")}>
              <Text style={styles.btnTxt}>VAI ALLA HOME  ›</Text>
            </Pressable>
          </>
        )}
        {status === "err" && (
          <>
            <View style={styles.iconErr}><Ionicons name="close-circle" size={72} color={colors.brandSecondary} /></View>
            <Text style={styles.msgErr}>{msg}</Text>
            <Pressable style={styles.btnAlt} onPress={() => router.replace("/auth")}>
              <Text style={styles.btnAltTxt}>TORNA AL LOGIN</Text>
            </Pressable>
          </>
        )}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  box: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl, gap: spacing.md },
  title: { color: colors.onSurface, fontSize: font.sizes.xxl, letterSpacing: 2, fontWeight: "500", marginBottom: spacing.md },
  iconOk: { marginBottom: spacing.sm },
  iconErr: { marginBottom: spacing.sm },
  msgOk: { color: colors.onSurface, fontSize: font.sizes.base, textAlign: "center", lineHeight: 22 },
  msgErr: { color: colors.brandSecondary, fontSize: font.sizes.base, textAlign: "center", lineHeight: 22 },
  btn: { marginTop: spacing.md, borderWidth: 2, borderColor: colors.brandPrimary, backgroundColor: colors.brandPrimary, paddingHorizontal: spacing.lg, paddingVertical: spacing.md },
  btnTxt: { color: colors.onBrandPrimary, letterSpacing: 2, fontWeight: "500" },
  btnAlt: { marginTop: spacing.md, borderWidth: 2, borderColor: colors.onSurface, paddingHorizontal: spacing.lg, paddingVertical: spacing.md },
  btnAltTxt: { color: colors.onSurface, letterSpacing: 2, fontWeight: "500" },
});
