import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View, Text, StyleSheet, RefreshControl, Pressable,
} from "react-native";
import { FlashList, type FlashListRef } from "@shopify/flash-list";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { api, Feud } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { colors, spacing, font, radius } from "@/src/theme";
import FeudCard from "@/src/components/FeudCard";
import { ScrollToTopButton } from "@/src/components/ScrollToTopButton";
import { FeudListSkeleton } from "@/src/components/Skeleton";

export default function TopScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const [feuds, setFeuds] = useState<Feud[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Floating "back to top" pill on the TOP list.
  const topListRef = useRef<FlashListRef<Feud>>(null);
  const [showTopBtn, setShowTopBtn] = useState(false);

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

  const keyExtractor = useCallback((f: Feud) => f.feud_id, []);
  const renderItem = useCallback(
    ({ item }: { item: Feud }) => (
      <FeudCard
        feud={item}
        onPress={() => router.push({ pathname: `/feud/${item.feud_id}`, params: { from: 'top' } })}
      />
    ),
    [router]
  );
  const handleScroll = useCallback((e: any) => {
    setShowTopBtn(e.nativeEvent.contentOffset.y > 600);
  }, []);
  const Separator = useMemo(() => {
    const S = () => <View style={{ height: spacing.md }} />;
    S.displayName = "Sep";
    return S;
  }, []);

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
        <FeudListSkeleton count={3} />
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
        <FlashList
          ref={topListRef}
          data={feuds}
          keyExtractor={keyExtractor}
          contentContainerStyle={styles.list}
          ItemSeparatorComponent={Separator}
          onScroll={handleScroll}
          scrollEventThrottle={120}
          removeClippedSubviews
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              tintColor={colors.brandPrimary}
              colors={[colors.brandPrimary]}
            />
          }
          renderItem={renderItem}
          testID="top-list"
        />
      )}
      <ScrollToTopButton
        visible={showTopBtn}
        onPress={() => topListRef.current?.scrollToOffset({ offset: 0, animated: true })}
        testID="top-scroll-top"
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  headerBar: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.lg,
    backgroundColor: colors.surfaceInverse,
  },
  title: { color: colors.onSurface, fontSize: font.sizes.xxxl, letterSpacing: 1.5, fontWeight: "800" },
  subtitle: { color: colors.muted, fontSize: font.sizes.sm, letterSpacing: 0.5, marginTop: 4, fontWeight: "600" },
  centerBox: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl, gap: spacing.md },
  lockCircle: {
    width: 120, height: 120, borderRadius: 60,
    borderWidth: 1.5, borderColor: colors.brandSecondary,
    alignItems: "center", justifyContent: "center",
    backgroundColor: colors.surfaceInverse,
    marginBottom: spacing.sm,
  },
  emptyBig: { color: colors.onSurface, fontSize: font.sizes.xxl, letterSpacing: 1, fontWeight: "800", textAlign: "center" },
  emptySmall: { color: colors.muted, fontSize: font.sizes.base, textAlign: "center", lineHeight: 22 },
  cta: {
    marginTop: spacing.md,
    backgroundColor: colors.brandPrimary,
    borderRadius: radius.pill,
    paddingHorizontal: spacing.xl, paddingVertical: spacing.sm + 4,
  },
  ctaTxt: { color: colors.onBrandPrimary, letterSpacing: 1.5, fontWeight: "800" },
  list: { padding: spacing.lg, paddingBottom: spacing.xxxl, gap: spacing.md },
});
