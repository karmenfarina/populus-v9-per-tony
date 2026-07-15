import React, { useCallback, useEffect, useState } from "react";
import {
  View, Text, StyleSheet, FlatList, RefreshControl, Pressable, ActivityIndicator,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { api, Feud } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { colors, spacing, font } from "@/src/theme";
import FeudCard from "@/src/components/FeudCard";

export default function TopScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const [feuds, setFeuds] = useState<Feud[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!user) return;
    try {
      setError(null);
      const data: any = await api.favorites();
      setFeuds(Array.isArray(data?.feuds) ? data.feuds : []);
    } catch (e: any) {
      setError(e?.detail || e?.message || "Errore nel caricamento");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [user]);

  useEffect(() => { load(); }, [load]);

  // Refresh on tab focus so newly favorited feuds appear immediately
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    load();
  }, [load]);

  // Non-registered users see a lock screen
  if (user && user.auth_provider === "anonymous") {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]} testID="top-anon-lock">
        <View style={styles.headerBar}>
          <Text style={styles.title}>TOP</Text>
          <Text style={styles.subtitle}>Le tue faide preferite</Text>
        </View>
        <View style={styles.centerBox}>
          <View style={styles.lockCircle}>
            <Ionicons name="bookmark-outline" size={64} color={colors.brandSecondary} />
          </View>
          <Text style={styles.emptyBig}>SALVA LE TUE FAIDE PREFERITE</Text>
          <Text style={styles.emptySmall}>
            Registrati con un account per salvare le faide che ti interessano di più
            e ritrovarle qui in ordine cronologico.
          </Text>
          <Pressable
            testID="top-register-cta"
            style={styles.cta}
            onPress={() => router.replace("/auth")}
          >
            <Text style={styles.ctaTxt}>REGISTRATI ORA  ›</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]} testID="top-screen">
      <View style={styles.headerBar}>
        <Text style={styles.title}>TOP</Text>
        <Text style={styles.subtitle}>Le tue faide preferite</Text>
      </View>

      {loading && !refreshing ? (
        <View style={styles.centerBox}>
          <ActivityIndicator size="large" color={colors.brandPrimary} />
        </View>
      ) : error ? (
        <View style={styles.centerBox}>
          <Ionicons name="alert-circle-outline" size={48} color={colors.muted} />
          <Text style={styles.emptySmall}>{error}</Text>
          <Pressable style={styles.cta} onPress={load} testID="top-retry">
            <Text style={styles.ctaTxt}>RIPROVA</Text>
          </Pressable>
        </View>
      ) : feuds.length === 0 ? (
        <View style={styles.centerBox}>
          <View style={styles.lockCircle}>
            <Ionicons name="bookmark-outline" size={64} color={colors.brandSecondary} />
          </View>
          <Text style={styles.emptyBig}>NESSUNA FAIDA SALVATA</Text>
          <Text style={styles.emptySmall}>
            {"Apri una faida e tocca l'icona "}
            <Ionicons name="bookmark-outline" size={14} color={colors.onSurface} />
            {" per aggiungerla ai tuoi preferiti."}
          </Text>
          <Pressable
            testID="top-browse-cta"
            style={styles.cta}
            onPress={() => router.replace("/")}
          >
            <Text style={styles.ctaTxt}>SCOPRI LE FAIDE  ›</Text>
          </Pressable>
        </View>
      ) : (
        <FlatList
          data={feuds}
          keyExtractor={(f) => f.feud_id}
          contentContainerStyle={styles.list}
          ItemSeparatorComponent={() => <View style={{ height: spacing.md }} />}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              tintColor={colors.brandPrimary}
              colors={[colors.brandPrimary]}
            />
          }
          renderItem={({ item }) => (
            <FeudCard feud={item} onPress={() => router.push(`/feud/${item.feud_id}`)} />
          )}
          testID="top-list"
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  headerBar: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: 2,
    borderColor: colors.border,
    backgroundColor: colors.surfaceInverse,
  },
  title: { color: colors.brandSecondary, fontSize: font.sizes.xxxl, letterSpacing: 2, fontWeight: "500" },
  subtitle: { color: colors.onSurfaceInverse, fontSize: font.sizes.sm, letterSpacing: 1, marginTop: 2 },
  centerBox: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl, gap: spacing.md },
  lockCircle: {
    width: 120, height: 120, borderRadius: 60,
    borderWidth: 2, borderColor: colors.brandSecondary,
    alignItems: "center", justifyContent: "center",
    backgroundColor: colors.surfaceInverse,
    marginBottom: spacing.sm,
  },
  emptyBig: { color: colors.onSurface, fontSize: font.sizes.xxl, letterSpacing: 1, fontWeight: "500", textAlign: "center" },
  emptySmall: { color: colors.muted, fontSize: font.sizes.base, textAlign: "center", lineHeight: 22 },
  cta: {
    marginTop: spacing.md,
    borderWidth: 2, borderColor: colors.brandPrimary,
    backgroundColor: colors.brandPrimary,
    paddingHorizontal: spacing.lg, paddingVertical: spacing.md,
  },
  ctaTxt: { color: colors.onBrandPrimary, letterSpacing: 2, fontWeight: "500" },
  list: { padding: spacing.lg, paddingBottom: spacing.xxxl },
});
