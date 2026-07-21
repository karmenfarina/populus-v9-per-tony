import { View, ActivityIndicator, StyleSheet } from "react-native";
import { Redirect } from "expo-router";
import { useAuth } from "@/src/auth/AuthContext";
import { colors } from "@/src/theme";

export default function Index() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <View style={styles.container} testID="root-loading">
        <ActivityIndicator size="large" color={colors.brandPrimary} />
      </View>
    );
  }

  if (!user) return <Redirect href="/auth" />;
  // First-run mandatory Terms & Privacy acceptance. Must precede onboarding
  // so the user can't fill in personal data before consenting to the
  // trattamento dei dati. `terms_accepted` is a server-computed boolean
  // that also flips back to `false` whenever the terms version bumps.
  if (user.terms_accepted !== true) return <Redirect href="/terms" />;
  if (!user.onboarding_completed) return <Redirect href="/onboarding" />;
  return <Redirect href="/(tabs)" />;
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.surface,
  },
});
