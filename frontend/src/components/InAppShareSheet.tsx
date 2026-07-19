import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View, Text, StyleSheet, Modal, Pressable, TextInput, ActivityIndicator,
  FlatList, KeyboardAvoidingView, Platform, Image, Alert,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, spacing, font } from "@/src/theme";
import { api } from "@/src/api";

/**
 * Instagram-style share-to-user sheet.
 *
 * - Search bar at the top (nickname substring search).
 * - Grid of suggested users when the query is empty — ranked by past
 *   messaging + comment interaction (server-computed).
 * - Multi-select up to `MAX_SELECTION` recipients.
 * - Optional caption input rendered when at least one user is selected.
 * - "Invia" sends one DM per selected user with the feud snapshot attached.
 */

const MAX_SELECTION = 15;

type Suggested = {
  user_id: string;
  nickname: string;
  primary_photo_id?: string | null;
  photo_data?: string | null;
};

type Props = {
  visible: boolean;
  feudId: string;
  feudTitle?: string;
  onClose: () => void;
  onOpenExternal?: () => void;
};

export default function InAppShareSheet({ visible, feudId, feudTitle, onClose, onOpenExternal }: Props) {
  const [suggestions, setSuggestions] = useState<Suggested[]>([]);
  const [loadingSug, setLoadingSug] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Suggested[]>([]);
  const [searching, setSearching] = useState(false);
  const [selected, setSelected] = useState<Record<string, Suggested>>({});
  const [caption, setCaption] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const searchDebounceRef = useRef<any>(null);

  useEffect(() => {
    if (!visible) {
      setQuery("");
      setResults([]);
      setSelected({});
      setCaption("");
      setError(null);
      return;
    }
    (async () => {
      setLoadingSug(true);
      try {
        const r = await api.shareSuggestions(21);
        setSuggestions(r.users || []);
      } catch (e: any) {
        setError(e?.message || "Impossibile caricare i suggeriti");
      } finally {
        setLoadingSug(false);
      }
    })();
  }, [visible]);

  // Debounced live search. Clearing the query brings back the suggestion
  // grid immediately.
  useEffect(() => {
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    const q = query.trim();
    if (q.length === 0) {
      setResults([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    searchDebounceRef.current = setTimeout(async () => {
      try {
        const r = await api.searchUsers(q, 30);
        setResults(r.users || []);
      } catch {
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 220);
    return () => { if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current); };
  }, [query]);

  const activeList = query.trim().length > 0 ? results : suggestions;
  const selectedCount = Object.keys(selected).length;

  const toggle = useCallback((u: Suggested) => {
    setSelected((prev) => {
      const next = { ...prev };
      if (next[u.user_id]) {
        delete next[u.user_id];
      } else {
        if (Object.keys(next).length >= MAX_SELECTION) {
          Alert.alert("Limite", `Puoi condividere con al massimo ${MAX_SELECTION} persone alla volta.`);
          return prev;
        }
        next[u.user_id] = u;
      }
      return next;
    });
  }, []);

  const send = async () => {
    if (selectedCount === 0 || sending) return;
    setSending(true);
    setError(null);
    try {
      const recipients = Object.keys(selected);
      const res = await api.shareFeudToUsers(feudId, recipients, caption.trim() || undefined);
      const failed: any[] = res.failed || [];
      if (failed.length && failed.length === recipients.length) {
        setError("Nessun invio riuscito");
      } else {
        onClose();
      }
    } catch (e: any) {
      setError(e?.message || "Errore durante l'invio");
    } finally {
      setSending(false);
    }
  };

  const renderUser = ({ item }: { item: Suggested }) => {
    const isSel = !!selected[item.user_id];
    return (
      <Pressable
        onPress={() => toggle(item)}
        testID={`share-user-${item.user_id}`}
        style={styles.userCell}
      >
        <View style={styles.avatarWrap}>
          {item.photo_data ? (
            <Image
              source={{ uri: `data:image/jpeg;base64,${item.photo_data}` }}
              style={styles.avatarImg}
            />
          ) : (
            <View style={[styles.avatarImg, styles.avatarPlaceholder]}>
              <Ionicons name="person" size={28} color={colors.muted} />
            </View>
          )}
          {isSel && (
            <View style={styles.checkOverlay}>
              <View style={styles.checkBubble}>
                <Ionicons name="checkmark" size={16} color={colors.onBrandPrimary} />
              </View>
            </View>
          )}
        </View>
        <Text style={styles.userNick} numberOfLines={1}>{item.nickname}</Text>
      </Pressable>
    );
  };

  return (
    <Modal animationType="slide" transparent visible={visible} onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable style={styles.sheet} onPress={() => { /* consume */ }}>
          <KeyboardAvoidingView
            behavior={Platform.OS === "ios" ? "padding" : undefined}
            style={{ flex: 1 }}
          >
            <View style={styles.handleWrap}><View style={styles.handle} /></View>

            {/* Search bar */}
            <View style={styles.searchRow}>
              <Ionicons name="search" size={20} color={colors.muted} style={{ marginRight: 8 }} />
              <TextInput
                testID="share-search-input"
                placeholder="Cerca un utente"
                placeholderTextColor={colors.muted}
                value={query}
                onChangeText={setQuery}
                style={styles.searchInput}
                autoCapitalize="none"
                autoCorrect={false}
              />
              {searching ? (
                <ActivityIndicator size="small" color={colors.brandPrimary} />
              ) : query.length > 0 ? (
                <Pressable onPress={() => setQuery("")} hitSlop={8}>
                  <Ionicons name="close-circle" size={20} color={colors.muted} />
                </Pressable>
              ) : (
                <Pressable onPress={onClose} hitSlop={8} testID="share-in-app-close">
                  <Ionicons name="close" size={22} color={colors.onSurface} />
                </Pressable>
              )}
            </View>

            {/* Section label */}
            {query.trim().length === 0 && suggestions.length > 0 && (
              <Text style={styles.sectionLabel}>SUGGERITI</Text>
            )}

            {/* Grid */}
            {loadingSug && suggestions.length === 0 ? (
              <View style={styles.emptyBox}>
                <ActivityIndicator color={colors.brandPrimary} />
              </View>
            ) : activeList.length === 0 ? (
              <View style={styles.emptyBox}>
                <Text style={styles.emptyTxt}>
                  {query.trim().length > 0
                    ? "Nessun utente trovato"
                    : "Nessun suggerimento ancora. Interagisci con altri utenti per popolare la lista."}
                </Text>
              </View>
            ) : (
              <FlatList
                data={activeList}
                keyExtractor={(u) => u.user_id}
                numColumns={3}
                renderItem={renderUser}
                columnWrapperStyle={styles.gridRow}
                contentContainerStyle={{ paddingBottom: spacing.md }}
                showsVerticalScrollIndicator={false}
                keyboardShouldPersistTaps="handled"
              />
            )}

            {/* Bottom action panel */}
            {selectedCount > 0 && (
              <View style={styles.actionBar} testID="share-action-bar">
                <TextInput
                  testID="share-caption-input"
                  placeholder={`Scrivi un messaggio${feudTitle ? ` su "${feudTitle}"` : ""}...`}
                  placeholderTextColor={colors.muted}
                  value={caption}
                  onChangeText={setCaption}
                  style={styles.captionInput}
                  multiline
                  maxLength={500}
                />
                <Pressable
                  onPress={send}
                  disabled={sending}
                  testID="share-send-btn"
                  style={[styles.sendBtn, sending && styles.sendBtnBusy]}
                >
                  {sending ? (
                    <ActivityIndicator color={colors.onBrandPrimary} />
                  ) : (
                    <>
                      <Ionicons name="paper-plane" size={18} color={colors.onBrandPrimary} />
                      <Text style={styles.sendTxt}>INVIA ({selectedCount})</Text>
                    </>
                  )}
                </Pressable>
              </View>
            )}

            {error && (
              <View style={styles.errorBox}>
                <Text style={styles.errorTxt}>{error}</Text>
              </View>
            )}

            {onOpenExternal && (
              <Pressable
                onPress={() => { onClose(); setTimeout(() => onOpenExternal(), 220); }}
                style={styles.externalBtn}
                testID="share-open-external"
              >
                <Ionicons name="share-outline" size={18} color={colors.onSurface} />
                <Text style={styles.externalBtnTxt}>ALTRE APP · COPIA LINK</Text>
              </Pressable>
            )}
          </KeyboardAvoidingView>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.55)", justifyContent: "flex-end" },
  sheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: spacing.md,
    paddingTop: spacing.xs,
    paddingBottom: spacing.md,
    maxHeight: "88%",
    minHeight: "70%",
  },
  handleWrap: { alignItems: "center", paddingVertical: spacing.xs },
  handle: { width: 44, height: 4, borderRadius: 2, backgroundColor: colors.border },
  searchRow: {
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 2,
    borderColor: colors.border,
    backgroundColor: colors.surfaceSecondary,
    paddingHorizontal: spacing.md,
    paddingVertical: 8,
    marginBottom: spacing.sm,
  },
  searchInput: {
    flex: 1,
    fontSize: font.sizes.base,
    color: colors.onSurface,
    paddingVertical: 4,
  },
  sectionLabel: {
    fontSize: font.sizes.xs,
    letterSpacing: 2,
    color: colors.muted,
    marginTop: spacing.xs,
    marginBottom: spacing.sm,
    fontWeight: "500",
  },
  gridRow: {
    justifyContent: "space-between",
    marginBottom: spacing.md,
  },
  userCell: {
    width: "31%",
    alignItems: "center",
    paddingVertical: spacing.xs,
  },
  avatarWrap: {
    width: 84,
    height: 84,
    borderRadius: 42,
    marginBottom: 6,
    position: "relative",
  },
  avatarImg: { width: 84, height: 84, borderRadius: 42 },
  avatarPlaceholder: {
    backgroundColor: colors.surfaceSecondary,
    justifyContent: "center",
    alignItems: "center",
    borderWidth: 2,
    borderColor: colors.border,
  },
  checkOverlay: {
    position: "absolute", top: 0, left: 0, right: 0, bottom: 0,
    borderRadius: 42, borderWidth: 3, borderColor: colors.brandPrimary,
    justifyContent: "flex-end", alignItems: "flex-end",
    padding: 2,
  },
  checkBubble: {
    width: 24, height: 24, borderRadius: 12,
    backgroundColor: colors.brandPrimary,
    justifyContent: "center", alignItems: "center",
  },
  userNick: {
    fontSize: font.sizes.sm,
    color: colors.onSurface,
    textAlign: "center",
    maxWidth: 100,
  },
  emptyBox: { padding: spacing.xl, alignItems: "center", justifyContent: "center" },
  emptyTxt: { color: colors.muted, textAlign: "center", fontSize: font.sizes.base },
  actionBar: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: spacing.sm,
    borderTopWidth: 2,
    borderColor: colors.border,
    paddingTop: spacing.sm,
    paddingBottom: spacing.md,
    backgroundColor: colors.surface,
  },
  captionInput: {
    flex: 1,
    borderWidth: 2,
    borderColor: colors.border,
    backgroundColor: colors.surfaceSecondary,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    fontSize: font.sizes.base,
    color: colors.onSurface,
    maxHeight: 120,
    minHeight: 44,
  },
  sendBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.brandPrimary,
    paddingVertical: 12,
    paddingHorizontal: spacing.md,
    borderWidth: 2,
    borderColor: colors.border,
    minHeight: 44,
  },
  sendBtnBusy: { opacity: 0.7 },
  sendTxt: {
    color: colors.onBrandPrimary,
    fontSize: font.sizes.sm,
    fontWeight: "500",
    letterSpacing: 1,
  },
  errorBox: {
    marginTop: spacing.sm,
    padding: spacing.sm,
    borderWidth: 2,
    borderColor: colors.error,
    backgroundColor: colors.surface,
  },
  errorTxt: { color: colors.error, fontSize: font.sizes.sm },
  externalBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center",
    gap: 8,
    borderWidth: 2, borderColor: colors.border,
    backgroundColor: colors.surfaceSecondary,
    paddingVertical: spacing.sm,
    marginTop: spacing.xs,
  },
  externalBtnTxt: {
    color: colors.onSurface, fontSize: font.sizes.sm,
    letterSpacing: 1, fontWeight: "500",
  },
});
