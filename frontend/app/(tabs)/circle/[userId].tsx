import React, { useCallback, useState } from "react";
import {
  View,
  Text,
  Pressable,
  FlatList,
  Image,
  StyleSheet,
  TextInput,
  ActivityIndicator,
  Switch,
  Alert,
  Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { api } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { useSmartBack } from "@/src/utils/useSmartBack";
import { colors, spacing, font } from "@/src/theme";

type Member = {
  user_id: string;
  nickname: string;
  primary_photo_id?: string | null;
  photo_data?: string | null;
  display_name?: string | null;
};

/**
 * Cerchia del Gossip — friend-circle browser.
 *
 * Usable both by the owner (with edit controls) and by other users
 * (read-only unless the owner has set the circle to private). Search
 * filters the list live; members are ordered by most-recent
 * interaction with the owner (kept in sync server-side).
 */
export default function CircleScreen() {
  const { userId } = useLocalSearchParams<{ userId: string }>();
  const router = useRouter();
  const { user } = useAuth();

  const [members, setMembers] = useState<Member[]>([]);
  const [count, setCount] = useState(0);
  const [max, setMax] = useState(45);
  const [isOwner, setIsOwner] = useState(false);
  const [privateCircle, setPrivateCircle] = useState(false);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");

  const goBack = useSmartBack(userId === user?.user_id ? "/profile" : `/user/${userId}`);

  const load = useCallback(async () => {
    if (!userId) return;
    setLoading(true);
    try {
      const r = await api.circleGet(userId, q.trim() || undefined);
      setCount(r.count || 0);
      setMax(r.max || 45);
      setIsOwner(!!r.is_owner);
      setPrivateCircle(!!r.private);
      setMembers(r.members || []);
    } catch { /* silent */ }
    finally { setLoading(false); }
  }, [userId, q]);

  // Load whenever the screen gains focus. This also handles the initial
  // mount and, crucially, returning from a profile where the user tapped
  // Add/Remove from Circle so the list, count and privacy flag stay in
  // sync in real time.
  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const togglePrivacy = async (v: boolean) => {
    // Optimistic switch — flip local then persist.
    setPrivateCircle(v);
    try {
      await api.circleSetPrivacy(v);
    } catch {
      setPrivateCircle(!v); // rollback
      Alert.alert("Errore", "Impossibile aggiornare la privacy.");
    }
  };

  const removeMember = (m: Member) => {
    const run = async () => {
      try {
        await api.circleRemove(m.user_id);
        setMembers((prev) => prev.filter((x) => x.user_id !== m.user_id));
        setCount((c) => Math.max(0, c - 1));
      } catch (e: any) {
        Alert.alert("Errore", e?.detail || "Impossibile rimuovere");
      }
    };
    if (Platform.OS === "web") {
      if (typeof window !== "undefined" && window.confirm(`Rimuovere @${m.nickname} dalla tua cerchia?`)) run();
      return;
    }
    Alert.alert(
      "Rimuovi dalla cerchia",
      `Rimuovere @${m.nickname}?`,
      [{ text: "Annulla", style: "cancel" }, { text: "Rimuovi", style: "destructive", onPress: run }],
    );
  };

  const renderMember = ({ item }: { item: Member }) => (
    <View style={styles.row} testID={`circle-row-${item.user_id}`}>
      <Pressable
        onPress={() => router.push({ pathname: "/messages/[userId]", params: { userId: item.user_id, from: `/circle/${userId}` } })}
        style={styles.rowLeft}
        testID={`circle-open-chat-${item.user_id}`}
      >
        {item.photo_data ? (
          <Image source={{ uri: item.photo_data }} style={styles.avatar} />
        ) : (
          <View style={[styles.avatar, styles.avatarFallback]}>
            <Ionicons name="person" size={22} color={colors.muted} />
          </View>
        )}
        <View style={{ flex: 1 }}>
          <Text style={styles.nick}>@{item.nickname}</Text>
          {item.display_name ? <Text style={styles.dispname}>{item.display_name}</Text> : null}
        </View>
      </Pressable>
      <Pressable
        onPress={() =>
          router.push({
            pathname: "/user/[id]",
            params: { id: item.user_id, from: `/circle/${userId}` },
          })
        }
        style={styles.iconBtn}
        testID={`circle-open-profile-${item.user_id}`}
        hitSlop={6}
      >
        <Ionicons name="person-outline" size={18} color={colors.onSurface} />
      </Pressable>
      {isOwner ? (
        <Pressable
          onPress={() => removeMember(item)}
          style={styles.iconBtn}
          testID={`circle-remove-${item.user_id}`}
          hitSlop={6}
        >
          <Ionicons name="close" size={20} color={colors.error} />
        </Pressable>
      ) : null}
    </View>
  );

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Pressable onPress={goBack} style={styles.backBtn} testID="circle-back">
          <Ionicons name="chevron-back" size={22} color={colors.onSurfaceInverse} />
        </Pressable>
        <Text style={styles.title}>CERCHIA</Text>
        <View style={styles.backBtn} />
      </View>

      <View style={styles.subhead}>
        <Text style={styles.subheadTxt}>
          {count} / {max}
        </Text>
        {isOwner ? (
          <View style={styles.privRow}>
            <Text style={styles.privLabel}>Cerchia privata</Text>
            <Switch
              value={privateCircle}
              onValueChange={togglePrivacy}
              testID="circle-privacy-switch"
            />
          </View>
        ) : null}
      </View>

      <View style={styles.searchWrap}>
        <Ionicons name="search" size={16} color={colors.muted} />
        <TextInput
          value={q}
          onChangeText={setQ}
          placeholder="Cerca amico..."
          placeholderTextColor={colors.muted}
          style={styles.searchInput}
          autoCapitalize="none"
          testID="circle-search-input"
        />
      </View>

      {privateCircle && !isOwner ? (
        <View style={styles.emptyBox} testID="circle-private-notice">
          <Ionicons name="lock-closed" size={38} color={colors.muted} />
          <Text style={styles.emptyTitle}>Cerchia privata</Text>
          <Text style={styles.emptySub}>
            Questo utente ha nascosto la propria cerchia agli altri.
          </Text>
        </View>
      ) : loading ? (
        <View style={styles.emptyBox}><ActivityIndicator color={colors.brandPrimary} /></View>
      ) : members.length === 0 ? (
        <View style={styles.emptyBox} testID="circle-empty">
          <Ionicons name="people-outline" size={38} color={colors.muted} />
          <Text style={styles.emptyTitle}>
            {q ? "Nessun risultato" : (isOwner ? "La tua cerchia è vuota" : "Nessuno nella cerchia")}
          </Text>
          {isOwner && !q ? (
            <Text style={styles.emptySub}>
              Aggiungi amici dai loro profili per farli comparire qui.
            </Text>
          ) : null}
        </View>
      ) : (
        <FlatList
          data={members}
          keyExtractor={(m) => m.user_id}
          renderItem={renderMember}
          contentContainerStyle={{ paddingBottom: spacing.xl }}
          testID="circle-list"
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    backgroundColor: colors.brandPrimary,
    paddingHorizontal: spacing.md, paddingVertical: spacing.md,
    borderBottomWidth: 2, borderColor: colors.border,
  },
  backBtn: { width: 32, height: 32, alignItems: "center", justifyContent: "center" },
  title: { color: colors.onBrandPrimary, fontSize: font.sizes.xl, letterSpacing: 3, fontWeight: "500" },
  subhead: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: spacing.lg, paddingVertical: spacing.md,
    borderBottomWidth: 1, borderColor: colors.border,
  },
  subheadTxt: { color: colors.onSurface, fontSize: font.sizes.base, fontWeight: "600" },
  privRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  privLabel: { color: colors.onSurface, fontSize: font.sizes.sm },
  searchWrap: {
    flexDirection: "row", alignItems: "center", gap: spacing.sm,
    borderWidth: 1, borderColor: colors.border,
    marginHorizontal: spacing.md, marginTop: spacing.md,
    paddingHorizontal: spacing.md, paddingVertical: 8,
    backgroundColor: colors.surfaceSecondary,
  },
  searchInput: { flex: 1, color: colors.onSurface, fontSize: font.sizes.base, padding: 0 },
  row: {
    flexDirection: "row", alignItems: "center", gap: spacing.sm,
    paddingHorizontal: spacing.md, paddingVertical: spacing.sm,
    borderBottomWidth: 1, borderColor: colors.border,
  },
  rowLeft: { flexDirection: "row", alignItems: "center", flex: 1, gap: spacing.sm },
  avatar: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.surfaceSecondary },
  avatarFallback: { alignItems: "center", justifyContent: "center" },
  nick: { color: colors.onSurface, fontSize: font.sizes.base, fontWeight: "600" },
  dispname: { color: colors.muted, fontSize: font.sizes.sm, marginTop: 2 },
  iconBtn: { padding: 8 },
  emptyBox: { alignItems: "center", justifyContent: "center", padding: spacing.xl, gap: spacing.sm },
  emptyTitle: { color: colors.onSurface, fontSize: font.sizes.lg, fontWeight: "600", marginTop: spacing.sm },
  emptySub: { color: colors.muted, fontSize: font.sizes.sm, textAlign: "center" },
});
