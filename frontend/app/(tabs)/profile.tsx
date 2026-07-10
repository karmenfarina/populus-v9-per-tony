import { useCallback, useEffect, useState } from "react";
import { View, Text, StyleSheet, ScrollView, Pressable, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { useAuth } from "@/src/auth/AuthContext";
import { api, HistoryItem } from "@/src/api";
import { colors, spacing, font, sideColor } from "@/src/theme";

type Filter = "all" | "majority" | "minority";

export default function Profile() {
  const { user, logout, refreshMe } = useAuth();
  const router = useRouter();
  const [filter, setFilter] = useState<Filter>("all");
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [loadingH, setLoadingH] = useState(false);

  const loadHistory = useCallback(async (f: Filter) => {
    setLoadingH(true);
    try {
      const r = await api.history(f);
      setHistory(r.history);
    } finally { setLoadingH(false); }
  }, []);

  useEffect(() => {
    refreshMe();
    loadHistory(filter);
  }, [filter, loadHistory, refreshMe]);

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
          <View style={[styles.statBox, { borderLeftWidth: 2 }]}>
            <Text style={[styles.statValue, { color: colors.brandPrimary }]}>{user.majority_votes}</Text>
            <Text style={styles.statLabel}>MAGGIORANZA</Text>
          </View>
          <View style={[styles.statBox, { borderLeftWidth: 2, backgroundColor: colors.surfaceInverse }]}>
            <Text style={[styles.statValue, { color: colors.brandSecondary }]}>{user.minority_votes}</Text>
            <Text style={[styles.statLabel, { color: colors.brandSecondary }]}>MINORANZA</Text>
          </View>
        </View>

        <View style={styles.historyHeader}>
          <Text style={styles.historyTitle}>STORICO VOTI</Text>
        </View>
        <View style={styles.filterRow}>
          {(["all", "majority", "minority"] as Filter[]).map((f) => (
            <Pressable
              key={f}
              onPress={() => setFilter(f)}
              testID={`filter-${f}`}
              style={[styles.filterChip, filter === f && (
                f === "majority" ? { backgroundColor: colors.brandPrimary } :
                f === "minority" ? { backgroundColor: colors.brandSecondary } :
                { backgroundColor: colors.surfaceInverse }
              )]}
            >
              <Text style={[styles.filterTxt,
                filter === f && (
                  f === "minority" ? { color: colors.onBrandSecondary } : { color: "#FFFFFF" }
                )
              ]}>
                {f === "all" ? "TUTTI" : f === "majority" ? "MAGGIORANZA" : "MINORANZA"}
              </Text>
            </Pressable>
          ))}
        </View>

        {loadingH ? (
          <View style={styles.center}><ActivityIndicator color={colors.brandPrimary} /></View>
        ) : history.length === 0 ? (
          <Text style={styles.emptyH}>Nessun voto in questa categoria.</Text>
        ) : (
          <View style={styles.historyList}>
            {history.map((h) => {
              const votedName = h.side_voted === "A" ? h.party_a : h.party_b;
              return (
                <Pressable
                  key={h.feud_id + h.voted_at}
                  style={styles.historyItem}
                  onPress={() => router.push(`/feud/${h.feud_id}`)}
                  testID={`history-${h.feud_id}`}
                >
                  <View style={[styles.sideBar, { backgroundColor: sideColor(h.side_voted) }]} />
                  <View style={{ flex: 1, padding: spacing.sm }}>
                    <Text style={styles.hCat}>{h.category_label.toUpperCase()}</Text>
                    <Text style={styles.hTitle} numberOfLines={2}>{h.title}</Text>
                    <View style={styles.hMetaRow}>
                      <Text style={[styles.hVoted, { color: sideColor(h.side_voted) }]}>Hai votato: {votedName}</Text>
                      <Text style={[styles.hBadge, h.aligned ? styles.hBadgeMaj : styles.hBadgeMin]}>
                        {h.aligned ? "MAGGIORANZA" : "MINORANZA"}
                      </Text>
                    </View>
                  </View>
                </Pressable>
              );
            })}
          </View>
        )}

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
  statBox: { flex: 1, padding: spacing.md, alignItems: "center", borderColor: colors.border, backgroundColor: colors.surfaceSecondary },
  statValue: { fontSize: font.sizes.xxxl, fontWeight: "500", color: colors.onSurface },
  statLabel: { fontSize: font.sizes.xs, color: colors.muted, letterSpacing: 1, marginTop: 2 },
  historyHeader: { paddingHorizontal: spacing.lg, paddingTop: spacing.lg, paddingBottom: spacing.sm },
  historyTitle: { fontSize: font.sizes.xxl, letterSpacing: 2, fontWeight: "500", color: colors.onSurface },
  filterRow: { flexDirection: "row", gap: spacing.sm, paddingHorizontal: spacing.lg, paddingBottom: spacing.md },
  filterChip: { flex: 1, borderWidth: 2, borderColor: colors.border, paddingVertical: spacing.sm, alignItems: "center", backgroundColor: colors.surfaceSecondary },
  filterTxt: { fontSize: font.sizes.xs, letterSpacing: 1, color: colors.onSurface, fontWeight: "500" },
  center: { padding: spacing.xl, alignItems: "center" },
  emptyH: { paddingHorizontal: spacing.lg, paddingVertical: spacing.xl, color: colors.muted, fontSize: font.sizes.base },
  historyList: { paddingHorizontal: spacing.lg, gap: spacing.sm },
  historyItem: { flexDirection: "row", borderWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceSecondary, overflow: "hidden" },
  sideBar: { width: 8 },
  hCat: { fontSize: font.sizes.xs, letterSpacing: 2, color: colors.muted },
  hTitle: { fontSize: font.sizes.base, color: colors.onSurface, marginTop: 2, lineHeight: 18 },
  hMetaRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: spacing.xs, flexWrap: "wrap", gap: spacing.xs },
  hVoted: { fontSize: font.sizes.xs, fontWeight: "500" },
  hBadge: { fontSize: font.sizes.xs, letterSpacing: 1, paddingHorizontal: 6, paddingVertical: 2, borderWidth: 1, borderColor: colors.border },
  hBadgeMaj: { backgroundColor: colors.brandPrimary, color: colors.onBrandPrimary },
  hBadgeMin: { backgroundColor: colors.brandSecondary, color: colors.onBrandSecondary },
  logout: { margin: spacing.lg, borderWidth: 2, borderColor: colors.border, padding: spacing.md, alignItems: "center", backgroundColor: colors.brandPrimary },
  logoutText: { color: colors.onBrandPrimary, fontSize: font.sizes.lg, letterSpacing: 2, fontWeight: "500" },
});
