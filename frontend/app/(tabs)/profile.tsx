import { View, Text, StyleSheet, ScrollView, Pressable } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useAuth } from "@/src/auth/AuthContext";
import { colors, spacing, font } from "@/src/theme";

export default function Profile() {
  const { user, logout } = useAuth();
  if (!user) return null;

  const badge = user.badge;
  const badgeUnlocked = badge?.unlocked;
  const badgeType = badge?.type;

  return (
    <SafeAreaView style={styles.safe} edges={["top"]} testID="profile-screen">
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.header}>
          <Text style={styles.brand}>PROFILO</Text>
          <Text style={styles.nickname} testID="profile-nickname">@{user.nickname}</Text>
          <Text style={styles.provider}>
            {user.auth_provider === "email" ? "Email" : user.auth_provider === "google" ? "Google" : "Anonimo"}
            {user.email ? ` · ${user.email}` : ""}
          </Text>
        </View>

        <View style={styles.badgeBlock} testID="profile-badge">
          <View style={[
            styles.badgeIcon,
            badgeUnlocked && badgeType === "bastian_contrario" && { backgroundColor: colors.brandPrimary },
            badgeUnlocked && badgeType === "buon_senso" && { backgroundColor: colors.brandSecondary },
          ]}>
            <Ionicons
              name={badgeUnlocked ? (badgeType === "bastian_contrario" ? "flash" : "shield-checkmark") : "lock-closed"}
              size={64}
              color={badgeUnlocked && badgeType === "buon_senso" ? colors.onBrandSecondary : colors.onBrandPrimary}
            />
          </View>
          <Text style={styles.badgeTitle}>
            {badgeUnlocked
              ? badgeType === "bastian_contrario" ? "BASTIAN CONTRARIO" : "BUON SENSO"
              : "SPILLA BLOCCATA"}
          </Text>
          <Text style={styles.badgeSubtitle}>
            {badgeUnlocked
              ? `Maggioranza ${badge?.majority ?? 0} · Minoranza ${badge?.minority ?? 0}`
              : `Progresso ${badge?.progress ?? 0}/${badge?.target ?? 5} voti`}
          </Text>
        </View>

        <View style={styles.statsRow}>
          <View style={styles.statBox}>
            <Text style={styles.statValue}>{user.total_votes}</Text>
            <Text style={styles.statLabel}>VOTI</Text>
          </View>
          <View style={[styles.statBox, { borderLeftWidth: 0 }]}>
            <Text style={[styles.statValue, { color: colors.brandPrimary }]}>{user.majority_votes}</Text>
            <Text style={styles.statLabel}>MAGGIORANZA</Text>
          </View>
          <View style={[styles.statBox, { borderLeftWidth: 0 }]}>
            <Text style={[styles.statValue, { color: colors.brandSecondary, backgroundColor: colors.surfaceInverse, paddingHorizontal: 6 }]}>{user.minority_votes}</Text>
            <Text style={styles.statLabel}>MINORANZA</Text>
          </View>
        </View>

        <Pressable style={styles.logout} onPress={logout} testID="profile-logout">
          <Text style={styles.logoutText}>ESCI</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  content: { paddingBottom: spacing.xxxl },
  header: { padding: spacing.lg, backgroundColor: colors.surfaceInverse, borderBottomWidth: 2, borderColor: colors.border },
  brand: { color: colors.onSurfaceInverse, fontSize: font.sizes.xxxl, letterSpacing: 2, fontWeight: "500" },
  nickname: { color: colors.brandSecondary, fontSize: font.sizes.xl, marginTop: spacing.sm },
  provider: { color: colors.onSurfaceInverse, fontSize: font.sizes.sm, opacity: 0.7, marginTop: spacing.xs },
  badgeBlock: { alignItems: "center", padding: spacing.xl, borderBottomWidth: 2, borderColor: colors.border },
  badgeIcon: { width: 140, height: 140, borderWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  badgeTitle: { fontSize: font.sizes.xxl, letterSpacing: 2, fontWeight: "500", color: colors.onSurface, marginTop: spacing.md },
  badgeSubtitle: { fontSize: font.sizes.base, color: colors.muted, marginTop: spacing.xs },
  statsRow: { flexDirection: "row", borderBottomWidth: 2, borderColor: colors.border },
  statBox: { flex: 1, padding: spacing.md, alignItems: "center", borderLeftWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceSecondary },
  statValue: { fontSize: font.sizes.xxxl, fontWeight: "500", color: colors.onSurface },
  statLabel: { fontSize: font.sizes.xs, color: colors.muted, letterSpacing: 1, marginTop: 2 },
  logout: { margin: spacing.lg, borderWidth: 2, borderColor: colors.border, padding: spacing.md, alignItems: "center", backgroundColor: colors.brandPrimary },
  logoutText: { color: colors.onBrandPrimary, fontSize: font.sizes.lg, letterSpacing: 2, fontWeight: "500" },
});
