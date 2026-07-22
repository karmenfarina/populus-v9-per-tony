import React, { useCallback, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  FlatList,
  ActivityIndicator,
  Image,
  Switch,
  Alert,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useFocusEffect, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { api } from "@/src/api";
import { colors, spacing, font } from "@/src/theme";

/**
 * Story-privacy screen: dynamic roster of everyone who currently has
 * the caller in their circle, with a per-user toggle to hide the
 * caller's future stories from them.
 *
 * The list is fully live — every time the screen gains focus we re-run
 * the query, so newly added followers appear immediately and users
 * who removed the caller from their circle disappear.
 *
 * Toggles POST synchronously to /api/stories/hidden_viewers/{id} with
 * { hidden: bool }. On failure we roll back the optimistic update and
 * surface an alert.
 */

type Viewer = {
  user_id: string;
  nickname?: string | null;
  display_name?: string | null;
  avatar?: string | null;
  is_anonymous?: boolean;
  hidden: boolean;
};

export default function StoriesHiddenViewers() {
  const router = useRouter();
  const [rows, setRows] = useState<Viewer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r: any = await api.storiesHiddenViewers();
      setRows((r?.viewers || []) as Viewer[]);
    } catch (e: any) {
      setError(e?.message || "Impossibile caricare la lista");
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const toggle = async (viewer: Viewer) => {
    if (pending[viewer.user_id]) return;
    // Optimistic flip — feels instant. Rollback on error.
    setRows((prev) => prev.map((r) => r.user_id === viewer.user_id ? { ...r, hidden: !r.hidden } : r));
    setPending((p) => ({ ...p, [viewer.user_id]: true }));
    try {
      await api.toggleHiddenViewer(viewer.user_id, !viewer.hidden);
    } catch (e: any) {
      setRows((prev) => prev.map((r) => r.user_id === viewer.user_id ? { ...r, hidden: viewer.hidden } : r));
      Alert.alert("Errore", e?.message || "Impossibile aggiornare");
    } finally {
      setPending((p) => {
        const next = { ...p };
        delete next[viewer.user_id];
        return next;
      });
    }
  };

  const hiddenCount = rows.filter((r) => r.hidden).length;

  const renderItem = ({ item }: { item: Viewer }) => (
    <View style={styles.row} testID={`hidden-row-${item.user_id}`}>
      <View style={styles.avatarWrap}>
        {item.avatar ? (
          <Image source={{ uri: item.avatar }} style={styles.avatar} />
        ) : (
          <View style={[styles.avatar, styles.avatarFallback]}>
            <Ionicons name={item.is_anonymous ? "glasses-outline" : "person"} size={20} color={colors.muted} />
          </View>
        )}
      </View>
      <View style={{ flex: 1 }}>
        <Text style={styles.nick} numberOfLines={1}>
          {item.is_anonymous ? "Utente anonimo" : (item.nickname || item.display_name || "utente")}
        </Text>
        {!item.is_anonymous && item.display_name ? (
          <Text style={styles.sub} numberOfLines={1}>{item.display_name}</Text>
        ) : null}
      </View>
      <View style={styles.switchWrap}>
        <Text style={[styles.switchLabel, item.hidden && { color: colors.brandPrimary }]}>
          {item.hidden ? "NASCOSTA" : "VEDE"}
        </Text>
        <Switch
          value={!item.hidden}
          onValueChange={() => toggle(item)}
          testID={`hidden-toggle-${item.user_id}`}
          trackColor={{ true: colors.brandPrimary, false: colors.border }}
          thumbColor={colors.surface}
          disabled={!!pending[item.user_id]}
        />
      </View>
    </View>
  );

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]} testID="stories-hidden-viewers">
      <View style={styles.header}>
        <Pressable
          onPress={() => {
            // This screen is reachable ONLY from Profile → the back
            // button must always land the user back on their profile,
            // NOT on Home. `router.back()` used to fall through to the
            // tab stack's root (Home) when the intermediate history
            // had been cleaned up. `router.replace("/profile")` gives
            // us deterministic behaviour on every platform.
            router.replace("/profile" as any);
          }}
          style={styles.backBtn}
          testID="hidden-viewers-back"
        >
          <Ionicons name="chevron-back" size={22} color={colors.onSurface} />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>CHI VEDE LE MIE STORIE</Text>
          <Text style={styles.subtitle}>
            {loading ? "Caricamento…" : `${rows.length} persone · ${hiddenCount} nascoste`}
          </Text>
        </View>
      </View>

      <View style={styles.disclaimer}>
        <Ionicons name="information-circle-outline" size={16} color={colors.muted} />
        <Text style={styles.disclaimerTxt}>
          Vedono le tue storie tutti coloro che hanno te nella propria Cerchia. Puoi
          nascondere le tue storie a chiunque disattivando l&apos;interruttore.
        </Text>
      </View>

      {loading ? (
        <View style={styles.center}><ActivityIndicator color={colors.brandPrimary} /></View>
      ) : error ? (
        <View style={styles.center}>
          <Ionicons name="alert-circle" size={28} color={colors.brandPrimary} />
          <Text style={styles.errorTxt}>{error}</Text>
          <Pressable onPress={load} style={styles.retry}>
            <Text style={styles.retryTxt}>RIPROVA</Text>
          </Pressable>
        </View>
      ) : rows.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="people-outline" size={40} color={colors.muted} />
          <Text style={styles.emptyTitle}>Nessuno ti segue</Text>
          <Text style={styles.emptyHint}>
            Quando altri utenti ti aggiungeranno alla loro Cerchia, li vedrai qui e
            potrai decidere se mostrare o nascondere loro le tue storie.
          </Text>
        </View>
      ) : (
        <FlatList
          data={rows}
          keyExtractor={(x) => x.user_id}
          renderItem={renderItem}
          contentContainerStyle={{ paddingBottom: spacing.xl }}
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderBottomWidth: 2,
    borderBottomColor: colors.border,
  },
  backBtn: {
    width: 36,
    height: 36,
    alignItems: "center",
    justifyContent: "center",
  },
  title: {
    color: colors.onSurface,
    fontSize: font.sizes.lg,
    fontWeight: "700",
    letterSpacing: 1.5,
  },
  subtitle: {
    color: colors.muted,
    fontSize: font.sizes.xs,
    marginTop: 2,
  },
  disclaimer: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    backgroundColor: colors.surfaceSecondary,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  disclaimerTxt: {
    flex: 1,
    color: colors.muted,
    fontSize: font.sizes.xs,
    lineHeight: 18,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    gap: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  avatarWrap: {
    width: 44,
    height: 44,
  },
  avatar: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: colors.surfaceTertiary,
  },
  avatarFallback: {
    alignItems: "center",
    justifyContent: "center",
  },
  nick: {
    color: colors.onSurface,
    fontSize: font.sizes.sm,
    fontWeight: "600",
  },
  sub: {
    color: colors.muted,
    fontSize: font.sizes.xs,
    marginTop: 2,
  },
  switchWrap: {
    alignItems: "flex-end",
    gap: 4,
  },
  switchLabel: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "700",
    letterSpacing: 1,
  },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: spacing.xl,
    gap: spacing.sm,
  },
  errorTxt: {
    color: colors.onSurface,
    fontSize: font.sizes.sm,
    textAlign: "center",
  },
  retry: {
    marginTop: spacing.md,
    backgroundColor: colors.brandPrimary,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    borderRadius: 4,
  },
  retryTxt: {
    color: colors.onBrandPrimary,
    fontSize: font.sizes.xs,
    fontWeight: "700",
    letterSpacing: 1.5,
  },
  emptyTitle: {
    color: colors.onSurface,
    fontSize: font.sizes.base,
    fontWeight: "700",
  },
  emptyHint: {
    color: colors.muted,
    fontSize: font.sizes.xs,
    textAlign: "center",
    lineHeight: 18,
  },
});
