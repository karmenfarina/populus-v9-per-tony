import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  Pressable,
  FlatList,
  Image,
  StyleSheet,
  TextInput,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Modal,
  Alert,
  BackHandler,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import * as ImagePicker from "expo-image-picker";
import * as FileSystem from "expo-file-system/legacy";
import { api, ChatMessage, MiniUser } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { useMessaging } from "@/src/messaging/MessagingContext";
import { colors, spacing, font } from "@/src/theme";

const REACTIONS = ["❤️", "😂", "😮", "😢", "😡", "👍", "👎", "🔥"];

/**
 * Cache of message_id → local file URI for chat images.
 *
 * The full-screen image viewer used to render `data:image/jpeg;base64,<huge>`
 * URIs directly, but RN Native's image loader caches decoded bitmaps by URI
 * digest — with multi-MB data URIs the cache would sometimes return a
 * previously-decoded bitmap for a *different* message, showing the wrong
 * image. Writing the payload to a temp file with the message_id as the file
 * name gives every image a globally unique `file://` URI that the loader can
 * cache safely.
 *
 * We keep the resolved paths in-module so they persist across route mounts
 * within a session. Files are stored under expo cacheDirectory (auto-cleaned
 * by the OS).
 */
const imageFileCache: Map<string, string> = new Map();

async function resolveImageFile(messageId: string, base64: string): Promise<string> {
  const cached = imageFileCache.get(messageId);
  if (cached) return cached;
  if (Platform.OS === "web") {
    // On web we cannot use FileSystem, but the bug doesn't reproduce on web
    // in the same way — fall back to a data URI which the browser handles as
    // a distinct resource per content string.
    const uri = `data:image/jpeg;base64,${base64}`;
    imageFileCache.set(messageId, uri);
    return uri;
  }
  const dir = (FileSystem as any).cacheDirectory || (FileSystem as any).documentDirectory;
  const safe = messageId.replace(/[^a-zA-Z0-9_]/g, "_");
  const uri = `${dir}chat_${safe}.jpg`;
  try {
    // Only write if the file does not already exist (this session).
    const info = await FileSystem.getInfoAsync(uri);
    if (!info.exists) {
      await FileSystem.writeAsStringAsync(uri, base64, {
        encoding: FileSystem.EncodingType.Base64,
      });
    }
    imageFileCache.set(messageId, uri);
    return uri;
  } catch (e) {
    // Fall back to data URI if the FS write fails — never crash the viewer.
    // eslint-disable-next-line no-console
    console.warn("[chat] resolveImageFile failed, falling back to data URI", e);
    const uri = `data:image/jpeg;base64,${base64}`;
    imageFileCache.set(messageId, uri);
    return uri;
  }
}

function formatTime(ts: string): string {
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

function isSameDay(a: string, b: string): boolean {
  try {
    const da = new Date(a);
    const db = new Date(b);
    return (
      da.getFullYear() === db.getFullYear() &&
      da.getMonth() === db.getMonth() &&
      da.getDate() === db.getDate()
    );
  } catch {
    return false;
  }
}

function formatDay(ts: string): string {
  try {
    const d = new Date(ts);
    const today = new Date();
    const yest = new Date(Date.now() - 86_400_000);
    if (isSameDay(ts, today.toISOString())) return "OGGI";
    if (isSameDay(ts, yest.toISOString())) return "IERI";
    return d.toLocaleDateString("it-IT", { day: "numeric", month: "short", year: "numeric" }).toUpperCase();
  } catch {
    return "";
  }
}

export default function ChatScreen() {
  const { userId, from } = useLocalSearchParams<{ userId: string; from?: string }>();
  const router = useRouter();
  const { user } = useAuth();
  const { subscribe, refresh: refreshBadge } = useMessaging();

  // Route back to wherever the user came from. Callers pass `?from=/circle/…`
  // or `?from=/messages` when they push the chat so the back button honours
  // the origin. Fallback: the messages list (safe default when we can't
  // determine the origin, e.g. deep-link or push notification).
  const goBackToMessagesList = useCallback(() => {
    const target = (typeof from === "string" && from.startsWith("/")) ? from : "/messages";
    router.replace(target as any);
  }, [router, from]);

  // Android hardware back button — override so it also goes to the
  // messages list instead of popping to Home.
  useFocusEffect(
    useCallback(() => {
      if (Platform.OS !== "android") return;
      const sub = BackHandler.addEventListener("hardwareBackPress", () => {
        goBackToMessagesList();
        return true; // consume the event
      });
      return () => sub.remove();
    }, [goBackToMessagesList]),
  );

  const [otherUser, setOtherUser] = useState<MiniUser | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [text, setText] = useState("");
  const [iBlocked, setIBlocked] = useState(false);
  const [theyBlocked, setTheyBlocked] = useState(false);
  const [reactTarget, setReactTarget] = useState<ChatMessage | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [reportText, setReportText] = useState("");
  const [pendingImage, setPendingImage] = useState<string | null>(null);
  const [emojiPickerOpen, setEmojiPickerOpen] = useState(false);
  // Viewer state. We store the resolved local URI (file:// on native, data:
  // on web) plus the source message id so the render never has to hold multi-
  // MB base64 strings in state.
  const [viewerUri, setViewerUri] = useState<string | null>(null);
  const [viewerKey, setViewerKey] = useState<string>("empty");
  // Custom confirm modal (replaces Alert.alert for reliable behaviour on both
  // native and web).
  const [confirmState, setConfirmState] = useState<{
    title: string;
    body: string;
    onConfirm: () => void;
  } | null>(null);
  const listRef = useRef<FlatList>(null);

  const openViewerForMessage = useCallback(async (msg: ChatMessage) => {
    if (!msg.image_data) return;
    // Resolve immediately so the modal never displays a stale image.
    const uri = await resolveImageFile(msg.message_id, msg.image_data);
    setViewerUri(uri);
    setViewerKey(`msg-${msg.message_id}`);
  }, []);
  const openViewerForPending = useCallback((base64: string) => {
    // Pending images have no message id; use a rolling key to force remount.
    setViewerUri(`data:image/jpeg;base64,${base64}`);
    setViewerKey(`pending-${Date.now()}`);
  }, []);
  const closeViewer = useCallback(() => {
    setViewerUri(null);
    setViewerKey("empty");
  }, []);

  const confirmDialog = useCallback(
    (title: string, body: string, onConfirm: () => void) => {
      setConfirmState({ title, body, onConfirm });
    },
    [],
  );

  const loadInitial = useCallback(async () => {
    if (!userId) return;
    try {
      const r = await api.messagesWith(userId);
      setOtherUser(r.other_user);
      setMessages(r.messages || []);
      setIBlocked(!!r.i_blocked);
      setTheyBlocked(!!r.they_blocked);
      // Mark all incoming as read immediately.
      try {
        await api.markConversationRead(userId);
        refreshBadge();
      } catch {
        /* silent */
      }
    } catch (e: any) {
      Alert.alert("Errore", e?.detail || "Impossibile aprire la chat");
      goBackToMessagesList();
    }
  }, [userId, refreshBadge, goBackToMessagesList]);

  useEffect(() => {
    if (!user || user.is_anonymous || !userId) return;
    let mounted = true;
    (async () => {
      setLoading(true);
      await loadInitial();
      if (mounted) setLoading(false);
    })();
    return () => {
      mounted = false;
    };
  }, [user, userId, loadInitial]);

  // Real-time updates for THIS conversation only.
  useEffect(() => {
    if (!user || !userId) return;
    const unsub = subscribe((ev) => {
      if (ev.type === "message.new" || ev.type === "message.sent") {
        const m = ev.message;
        const otherId = user.user_id === m.sender_id ? m.recipient_id : m.sender_id;
        if (otherId !== userId) return;
        setMessages((prev) => {
          if (prev.some((x) => x.message_id === m.message_id)) return prev;
          return [...prev, m];
        });
        // If it's incoming, mark read + refresh badge.
        if (m.recipient_id === user.user_id) {
          api.markConversationRead(userId).catch(() => {});
          refreshBadge();
        }
      } else if (ev.type === "message.read") {
        setMessages((prev) =>
          prev.map((x) =>
            ev.message_ids.includes(x.message_id) ? { ...x, read_at: ev.read_at } : x,
          ),
        );
      } else if (ev.type === "message.reaction" || ev.type === "message.deleted") {
        const m = ev.message;
        setMessages((prev) =>
          prev.map((x) => (x.message_id === m.message_id ? { ...x, ...m } : x)),
        );
      }
    });
    return () => unsub();
  }, [subscribe, user, userId, refreshBadge]);

  // Auto-scroll to end on new messages.
  useEffect(() => {
    if (messages.length === 0) return;
    setTimeout(() => {
      try {
        listRef.current?.scrollToEnd({ animated: true });
      } catch {
        /* ignore */
      }
    }, 60);
  }, [messages.length]);

  const send = useCallback(async () => {
    if (sending) return;
    const t = text.trim();
    const img = pendingImage;
    if (!t && !img) return;
    if (iBlocked || theyBlocked) return;
    setSending(true);
    // Optimistic append
    const tmp: ChatMessage = {
      message_id: `tmp_${Date.now()}`,
      conversation_id: "",
      sender_id: user?.user_id || "",
      recipient_id: userId || "",
      text: t || null,
      image_data: img,
      kind: img && !t ? "image" : t && !img ? "text" : "mixed",
      reactions: {},
      created_at: new Date().toISOString(),
      read_at: null,
    };
    setMessages((prev) => [...prev, tmp]);
    setText("");
    setPendingImage(null);
    try {
      const r = await api.sendMessage(userId!, t || undefined, img || undefined);
      const real: ChatMessage = r.message;
      // The WS `message.sent` event may already have inserted the real message
      // by the time the HTTP response resolves. Merge without duplicating.
      setMessages((prev) => {
        const withoutTmp = prev.filter((x) => x.message_id !== tmp.message_id);
        if (withoutTmp.some((x) => x.message_id === real.message_id)) {
          return withoutTmp;
        }
        return [...withoutTmp, real];
      });
    } catch (e: any) {
      setMessages((prev) => prev.filter((x) => x.message_id !== tmp.message_id));
      Alert.alert("Errore", e?.detail || "Impossibile inviare il messaggio");
    } finally {
      setSending(false);
    }
  }, [sending, text, pendingImage, iBlocked, theyBlocked, user, userId]);

  const attachImage = useCallback(async () => {
    if (iBlocked || theyBlocked) return;
    try {
      const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
      if (perm.status !== "granted") {
        Alert.alert(
          "Permesso richiesto",
          "Consenti l'accesso alle foto per inviare immagini in chat.",
        );
        return;
      }
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ["images"],
        allowsEditing: true,
        quality: 0.6,
        base64: true,
      });
      if (result.canceled) return;
      const asset = result.assets?.[0];
      if (!asset?.base64) return;
      setPendingImage(asset.base64);
    } catch {
      Alert.alert("Errore", "Impossibile selezionare l'immagine");
    }
  }, [iBlocked, theyBlocked]);

  const react = useCallback(async (messageId: string, emoji: string) => {
    setReactTarget(null);
    try {
      const r = await api.reactMessage(messageId, emoji);
      setMessages((prev) =>
        prev.map((x) => (x.message_id === messageId ? { ...x, ...r.message } : x)),
      );
    } catch (e: any) {
      Alert.alert("Errore", e?.detail || "Impossibile aggiungere reazione");
    }
  }, []);

  const deleteMessage = useCallback((m: ChatMessage) => {
    setReactTarget(null);
    // Alert.alert with a destructive button is unreliable on RN-Web (some
    // versions collapse it into window.confirm which loses the button
    // labels). Use our own themed modal for a deterministic flow.
    confirmDialog(
      "Elimina messaggio",
      "Vuoi eliminare questo messaggio per tutti?",
      async () => {
        try {
          await api.deleteMessage(m.message_id);
          setMessages((prev) =>
            prev.map((x) =>
              x.message_id === m.message_id
                ? { ...x, deleted: true, text: null, image_data: null, reactions: {} }
                : x,
            ),
          );
          // If the currently open viewer was showing this image, close it.
          setViewerUri((cur) => (cur && viewerKey === `msg-${m.message_id}` ? null : cur));
          // Invalidate any cached file so it can't be reopened stale.
          imageFileCache.delete(m.message_id);
        } catch (e: any) {
          Alert.alert("Errore", e?.detail || "Impossibile eliminare");
        }
      },
    );
  }, [confirmDialog, viewerKey]);

  const toggleBlock = useCallback(async () => {
    setMenuOpen(false);
    if (!otherUser) return;
    if (iBlocked) {
      try {
        await api.unblockUser(otherUser.user_id);
        setIBlocked(false);
      } catch (e: any) {
        Alert.alert("Errore", e?.detail || "Impossibile sbloccare");
      }
      return;
    }
    confirmDialog(
      "Blocca utente",
      `Vuoi bloccare @${otherUser.nickname}? Non riceverai più messaggi da questo utente.`,
      async () => {
        try {
          await api.blockUser(otherUser.user_id);
          setIBlocked(true);
        } catch (e: any) {
          Alert.alert("Errore", e?.detail || "Impossibile bloccare");
        }
      },
    );
  }, [iBlocked, otherUser, confirmDialog]);

  const submitReport = useCallback(async () => {
    if (!otherUser) return;
    const reason = reportText.trim();
    if (reason.length < 2) {
      Alert.alert("Segnalazione", "Descrivi brevemente il motivo (min 2 caratteri).");
      return;
    }
    try {
      await api.reportUser(otherUser.user_id, reason);
      setReportOpen(false);
      setReportText("");
      Alert.alert("Grazie", "La segnalazione è stata inviata al team di moderazione.");
    } catch (e: any) {
      Alert.alert("Errore", e?.detail || "Impossibile inviare la segnalazione");
    }
  }, [otherUser, reportText]);

  const renderMessage = useCallback(
    ({ item, index }: { item: ChatMessage; index: number }) => {
      const mine = user && item.sender_id === user.user_id;
      const prev = index > 0 ? messages[index - 1] : null;
      const showDay = !prev || !isSameDay(prev.created_at, item.created_at);
      const reactions = Object.values(item.reactions || {});
      // Snapshot the item locally so tap handlers can never reference a
      // different message due to FlatList row recycling.
      const bubbleItem = item;
      const bubbleImage = item.image_data;
      const bubbleId = item.message_id;
      const bubbleDeleted = !!item.deleted;
      const handleTap = () => {
        if (bubbleDeleted) return;
        if (bubbleImage) openViewerForMessage(bubbleItem);
      };
      const handleLongPress = () => {
        if (bubbleDeleted) return;
        setReactTarget(item);
      };
      return (
        <View>
          {showDay && (
            <View style={styles.dayDivider}>
              <Text style={styles.dayTxt}>{formatDay(item.created_at)}</Text>
            </View>
          )}
          <Pressable
            onPress={handleTap}
            onLongPress={handleLongPress}
            style={[styles.bubbleRow, mine ? styles.rowMine : styles.rowTheirs]}
            testID={`msg-bubble-${bubbleId}`}
          >
            <View
              style={[
                styles.bubble,
                mine ? styles.bubbleMine : styles.bubbleTheirs,
                item.deleted && styles.bubbleDeleted,
              ]}
            >
              {item.deleted ? (
                <Text style={[styles.txt, mine ? styles.txtMine : styles.txtTheirs, styles.txtItalic]}>
                  Messaggio eliminato
                </Text>
              ) : (
                <>
                  {item.shared_feud ? (
                    <Pressable
                      onPress={() => router.push({
                        pathname: "/feud/[id]",
                        params: {
                          id: item.shared_feud!.feud_id,
                          from: "messages",
                          messagesUserId: String(userId || ""),
                        },
                      })}
                      style={styles.sharedFeudCard}
                      testID={`msg-shared-feud-${bubbleId}`}
                    >
                      {item.shared_feud.image_url ? (
                        <Image
                          source={{ uri: item.shared_feud.image_url }}
                          style={styles.sharedFeudImg}
                        />
                      ) : (
                        <View style={[styles.sharedFeudImg, { backgroundColor: colors.surfaceSecondary, alignItems: "center", justifyContent: "center" }]}>
                          <Ionicons name="newspaper-outline" size={40} color={colors.muted} />
                        </View>
                      )}
                      <View style={styles.sharedFeudBody}>
                        {item.shared_feud.category_label ? (
                          <Text style={styles.sharedFeudCat} numberOfLines={1}>
                            {item.shared_feud.category_label.toUpperCase()}
                          </Text>
                        ) : null}
                        <Text style={styles.sharedFeudTitle} numberOfLines={3}>
                          {item.shared_feud.title || "Apri il post"}
                        </Text>
                        <View style={styles.sharedFeudCta}>
                          <Ionicons name="open-outline" size={14} color={colors.brandPrimary} />
                          <Text style={styles.sharedFeudCtaTxt}>APRI IL POST</Text>
                        </View>
                      </View>
                    </Pressable>
                  ) : null}
                  {bubbleImage ? (
                    <Image
                      source={{ uri: `data:image/jpeg;base64,${bubbleImage}` }}
                      style={styles.msgImg}
                      testID={`msg-image-${bubbleId}`}
                    />
                  ) : null}
                  {item.text ? (
                    <Text style={[styles.txt, mine ? styles.txtMine : styles.txtTheirs]}>{item.text}</Text>
                  ) : null}
                </>
              )}
              <View style={styles.metaRow}>
                <Text style={[styles.metaTxt, mine ? styles.metaMine : styles.metaTheirs]}>
                  {formatTime(item.created_at)}
                </Text>
                {mine && !item.deleted && (
                  <Ionicons
                    name={item.read_at ? "checkmark-done" : "checkmark"}
                    size={14}
                    color={item.read_at ? "#4FC3F7" : colors.muted}
                    style={{ marginLeft: 4 }}
                  />
                )}
              </View>
            </View>
          </Pressable>
          {reactions.length > 0 && !item.deleted && (
            <View style={[styles.reactionsBar, mine ? styles.reactMine : styles.reactTheirs]}>
              {reactions.map((emoji, i) => (
                <Text key={`${bubbleId}-r-${i}`} style={styles.reactEmoji}>
                  {emoji}
                </Text>
              ))}
            </View>
          )}
        </View>
      );
    },
    [user, messages, openViewerForMessage],
  );

  if (!user || user.is_anonymous) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]}>
        <View style={styles.center}>
          <Text style={{ color: colors.muted }}>Accesso non consentito</Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]} testID="chat-screen">
      <View style={styles.header}>
        <Pressable
          onPress={goBackToMessagesList}
          style={styles.backBtn}
          testID="chat-back"
        >
          <Ionicons name="chevron-back" size={22} color={colors.onSurfaceInverse} />
        </Pressable>
        <Pressable
          onPress={() => otherUser && router.push({ pathname: "/user/[id]", params: { id: otherUser.user_id } })}
          style={styles.headerCenter}
        >
          {otherUser?.photo_data ? (
            <Image
              source={{ uri: `data:image/jpeg;base64,${otherUser.photo_data}` }}
              style={styles.headerAvatar}
            />
          ) : (
            <View style={[styles.headerAvatar, styles.headerAvatarPh]}>
              <Ionicons name="person" size={16} color={colors.muted} />
            </View>
          )}
          <Text style={styles.headerNick} numberOfLines={1}>
            @{otherUser?.nickname || "…"}
          </Text>
        </Pressable>
        <Pressable onPress={() => setMenuOpen(true)} style={styles.menuBtn} testID="chat-menu">
          <Ionicons name="ellipsis-vertical" size={22} color={colors.onSurfaceInverse} />
        </Pressable>
      </View>

      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        keyboardVerticalOffset={Platform.OS === "ios" ? 90 : 0}
      >
        {loading ? (
          <View style={styles.center}>
            <ActivityIndicator color={colors.brandPrimary} />
          </View>
        ) : (
          <FlatList
            ref={listRef}
            data={messages}
            keyExtractor={(m) => m.message_id}
            renderItem={renderMessage}
            contentContainerStyle={styles.list}
            onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: false })}
            ListEmptyComponent={
              <View style={styles.emptyChat}>
                <Ionicons name="chatbubble-outline" size={48} color={colors.muted} />
                <Text style={styles.emptyChatTxt}>
                  Inizia la conversazione con @{otherUser?.nickname}
                </Text>
              </View>
            }
          />
        )}

        {(iBlocked || theyBlocked) && (
          <View style={styles.blockedBar}>
            <Ionicons name="ban" size={16} color={colors.onBrandPrimary} />
            <Text style={styles.blockedTxt}>
              {iBlocked
                ? `Hai bloccato @${otherUser?.nickname}. Sbloccalo dal menu per continuare a scrivere.`
                : "Non puoi contattare questo utente."}
            </Text>
          </View>
        )}

        {pendingImage && (
          <View style={styles.pendingRow}>
            <Pressable onPress={() => openViewerForPending(pendingImage)}>
              <Image source={{ uri: `data:image/jpeg;base64,${pendingImage}` }} style={styles.pendingImg} />
            </Pressable>
            <Pressable onPress={() => setPendingImage(null)} style={styles.pendingClose}>
              <Ionicons name="close" size={18} color={colors.onSurfaceInverse} />
            </Pressable>
          </View>
        )}

        <View style={styles.composer}>
          <Pressable
            onPress={attachImage}
            style={styles.composeBtn}
            disabled={iBlocked || theyBlocked || sending}
            testID="chat-attach"
          >
            <Ionicons name="image-outline" size={22} color={colors.onSurface} />
          </Pressable>
          <Pressable
            onPress={() => setEmojiPickerOpen(true)}
            style={styles.composeBtn}
            disabled={iBlocked || theyBlocked || sending}
            testID="chat-emoji"
          >
            <Ionicons name="happy-outline" size={22} color={colors.onSurface} />
          </Pressable>
          <TextInput
            style={styles.input}
            value={text}
            onChangeText={setText}
            placeholder={iBlocked || theyBlocked ? "Chat disabilitata" : "Scrivi un messaggio…"}
            placeholderTextColor={colors.muted}
            multiline
            editable={!iBlocked && !theyBlocked}
            testID="chat-input"
          />
          <Pressable
            onPress={send}
            style={[
              styles.sendBtn,
              (iBlocked || theyBlocked || (!text.trim() && !pendingImage)) && { opacity: 0.4 },
            ]}
            disabled={sending || iBlocked || theyBlocked || (!text.trim() && !pendingImage)}
            testID="chat-send"
          >
            <Ionicons name="send" size={20} color={colors.onBrandPrimary} />
          </Pressable>
        </View>
      </KeyboardAvoidingView>

      {/* Reaction sheet */}
      <Modal visible={!!reactTarget} transparent animationType="fade" onRequestClose={() => setReactTarget(null)}>
        <Pressable style={styles.modalBg} onPress={() => setReactTarget(null)}>
          <View style={styles.reactSheet}>
            <View style={styles.reactRow}>
              {REACTIONS.map((e) => (
                <Pressable
                  key={e}
                  onPress={() => reactTarget && react(reactTarget.message_id, e)}
                  style={styles.reactBtn}
                >
                  <Text style={{ fontSize: 26 }}>{e}</Text>
                </Pressable>
              ))}
            </View>
            {reactTarget?.sender_id === user.user_id && (
              <Pressable onPress={() => reactTarget && deleteMessage(reactTarget)} style={styles.reactAction}>
                <Ionicons name="trash-outline" size={18} color={colors.error} />
                <Text style={{ color: colors.error, letterSpacing: 1 }}>ELIMINA MESSAGGIO</Text>
              </Pressable>
            )}
          </View>
        </Pressable>
      </Modal>

      {/* Emoji quick insert */}
      <Modal visible={emojiPickerOpen} transparent animationType="fade" onRequestClose={() => setEmojiPickerOpen(false)}>
        <Pressable style={styles.modalBg} onPress={() => setEmojiPickerOpen(false)}>
          <View style={styles.reactSheet}>
            <Text style={styles.sheetTitle}>INSERISCI EMOJI</Text>
            <View style={styles.reactRow}>
              {["😀","😂","😍","😎","🤔","😢","😡","👍","🙏","🎉","🔥","💯","❤️","😱","🥳","😴"].map((e) => (
                <Pressable
                  key={e}
                  onPress={() => {
                    setText((t) => t + e);
                    setEmojiPickerOpen(false);
                  }}
                  style={styles.reactBtn}
                >
                  <Text style={{ fontSize: 26 }}>{e}</Text>
                </Pressable>
              ))}
            </View>
          </View>
        </Pressable>
      </Modal>

      {/* Menu */}
      <Modal visible={menuOpen} transparent animationType="fade" onRequestClose={() => setMenuOpen(false)}>
        <Pressable style={styles.modalBg} onPress={() => setMenuOpen(false)}>
          <View style={styles.menuSheet}>
            <Pressable
              onPress={() => {
                setMenuOpen(false);
                otherUser && router.push({ pathname: "/user/[id]", params: { id: otherUser.user_id } });
              }}
              style={styles.menuItem}
            >
              <Ionicons name="person-outline" size={20} color={colors.onSurface} />
              <Text style={styles.menuTxt}>Vai al profilo</Text>
            </Pressable>
            <Pressable onPress={toggleBlock} style={styles.menuItem}>
              <Ionicons name={iBlocked ? "checkmark-circle-outline" : "ban-outline"} size={20} color={colors.error} />
              <Text style={[styles.menuTxt, { color: colors.error }]}>
                {iBlocked ? "Sblocca utente" : "Blocca utente"}
              </Text>
            </Pressable>
            <Pressable
              onPress={() => {
                setMenuOpen(false);
                setReportOpen(true);
              }}
              style={styles.menuItem}
            >
              <Ionicons name="flag-outline" size={20} color={colors.error} />
              <Text style={[styles.menuTxt, { color: colors.error }]}>Segnala utente</Text>
            </Pressable>
          </View>
        </Pressable>
      </Modal>

      {/* Report */}
      <Modal visible={reportOpen} transparent animationType="fade" onRequestClose={() => setReportOpen(false)}>
        <Pressable style={styles.modalBg} onPress={() => setReportOpen(false)}>
          <Pressable style={styles.reportSheet} onPress={() => {}}>
            <Text style={styles.sheetTitle}>SEGNALA @{otherUser?.nickname}</Text>
            <TextInput
              style={styles.reportInput}
              value={reportText}
              onChangeText={setReportText}
              placeholder="Motivo della segnalazione…"
              placeholderTextColor={colors.muted}
              multiline
              maxLength={500}
            />
            <View style={{ flexDirection: "row", gap: spacing.sm }}>
              <Pressable
                onPress={() => setReportOpen(false)}
                style={[styles.reportBtn, { backgroundColor: colors.surfaceTertiary }]}
              >
                <Text style={{ color: colors.onSurface, letterSpacing: 1 }}>ANNULLA</Text>
              </Pressable>
              <Pressable
                onPress={submitReport}
                style={[styles.reportBtn, { backgroundColor: colors.brandPrimary }]}
              >
                <Text style={{ color: colors.onBrandPrimary, letterSpacing: 1, fontWeight: "500" }}>INVIA</Text>
              </Pressable>
            </View>
          </Pressable>
        </Pressable>
      </Modal>

      {/* Fullscreen image viewer */}
      <Modal
        visible={!!viewerUri}
        transparent
        animationType="fade"
        onRequestClose={closeViewer}
      >
        <Pressable style={styles.viewerBg} onPress={closeViewer}>
          <Pressable onPress={closeViewer} style={styles.viewerCloseBtn} testID="viewer-close">
            <Ionicons name="close" size={28} color="#fff" />
          </Pressable>
          {viewerUri && (
            <Image
              key={viewerKey}
              source={{ uri: viewerUri }}
              style={styles.viewerImg}
              resizeMode="contain"
            />
          )}
        </Pressable>
      </Modal>

      {/* Custom confirm modal (replaces Alert.alert for destructive actions). */}
      <Modal
        visible={!!confirmState}
        transparent
        animationType="fade"
        onRequestClose={() => setConfirmState(null)}
      >
        <Pressable style={styles.modalBg} onPress={() => setConfirmState(null)}>
          <Pressable style={styles.reportSheet} onPress={() => {}}>
            <Text style={styles.sheetTitle}>{confirmState?.title?.toUpperCase()}</Text>
            <Text style={{ color: colors.onSurface, fontSize: font.sizes.base, textAlign: "center" }}>
              {confirmState?.body}
            </Text>
            <View style={{ flexDirection: "row", gap: spacing.sm }}>
              <Pressable
                onPress={() => setConfirmState(null)}
                style={[styles.reportBtn, { backgroundColor: colors.surfaceTertiary }]}
                testID="confirm-cancel"
              >
                <Text style={{ color: colors.onSurface, letterSpacing: 1 }}>ANNULLA</Text>
              </Pressable>
              <Pressable
                onPress={() => {
                  const cb = confirmState?.onConfirm;
                  setConfirmState(null);
                  if (cb) cb();
                }}
                style={[styles.reportBtn, { backgroundColor: colors.error }]}
                testID="confirm-ok"
              >
                <Text style={{ color: "#fff", letterSpacing: 1, fontWeight: "500" }}>CONFERMA</Text>
              </Pressable>
            </View>
          </Pressable>
        </Pressable>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surfaceInverse,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.sm,
    borderBottomWidth: 2,
    borderColor: colors.border,
    gap: spacing.sm,
  },
  backBtn: { padding: spacing.sm },
  menuBtn: { padding: spacing.sm },
  headerCenter: { flex: 1, flexDirection: "row", alignItems: "center", gap: spacing.sm },
  headerAvatar: { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.surfaceTertiary },
  headerAvatarPh: { alignItems: "center", justifyContent: "center" },
  headerNick: { color: colors.brandSecondary, fontSize: font.sizes.base, letterSpacing: 1, flexShrink: 1 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  list: { padding: spacing.sm, gap: 2 },
  dayDivider: { alignItems: "center", marginVertical: spacing.md },
  dayTxt: { fontSize: font.sizes.xs, color: colors.muted, letterSpacing: 2, backgroundColor: colors.surfaceTertiary, paddingHorizontal: spacing.md, paddingVertical: 4, borderRadius: 8 },
  bubbleRow: { paddingHorizontal: 4, marginVertical: 2 },
  rowMine: { alignItems: "flex-end" },
  rowTheirs: { alignItems: "flex-start" },
  bubble: {
    maxWidth: "80%",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: 16,
  },
  bubbleMine: { backgroundColor: colors.brandPrimary, borderBottomRightRadius: 4 },
  bubbleTheirs: { backgroundColor: colors.surfaceSecondary, borderBottomLeftRadius: 4, borderWidth: 1, borderColor: colors.surfaceTertiary },
  bubbleDeleted: { opacity: 0.6 },
  txt: { fontSize: font.sizes.base, lineHeight: 20 },
  txtMine: { color: colors.onBrandPrimary },
  txtTheirs: { color: colors.onSurface },
  txtItalic: { fontStyle: "italic" },
  msgImg: { width: 220, height: 220, borderRadius: 8, marginBottom: 6, backgroundColor: colors.surfaceTertiary },
  sharedFeudCard: {
    width: 240,
    borderWidth: 2,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    marginBottom: 6,
    overflow: "hidden",
  },
  sharedFeudImg: { width: 240, height: 140, backgroundColor: colors.surfaceTertiary },
  sharedFeudBody: { padding: 10, gap: 6 },
  sharedFeudCat: {
    fontSize: 10,
    letterSpacing: 2,
    color: colors.brandPrimary,
    fontWeight: "500",
  },
  sharedFeudTitle: {
    fontSize: font.sizes.base,
    color: colors.onSurface,
    fontWeight: "500",
    lineHeight: 18,
  },
  sharedFeudCta: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    marginTop: 4,
  },
  sharedFeudCtaTxt: {
    color: colors.brandPrimary,
    fontSize: 11,
    letterSpacing: 1,
    fontWeight: "500",
  },
  metaRow: { flexDirection: "row", alignItems: "center", justifyContent: "flex-end", marginTop: 2 },
  metaTxt: { fontSize: 10 },
  metaMine: { color: "rgba(255,255,255,0.75)" },
  metaTheirs: { color: colors.muted },
  reactionsBar: {
    flexDirection: "row",
    gap: 4,
    marginTop: -8,
    marginBottom: 6,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.surfaceTertiary,
    borderRadius: 12,
    paddingHorizontal: 6,
    paddingVertical: 2,
    alignSelf: "flex-start",
  },
  reactMine: { alignSelf: "flex-end", marginRight: 8 },
  reactTheirs: { alignSelf: "flex-start", marginLeft: 8 },
  reactEmoji: { fontSize: 14 },
  emptyChat: { alignItems: "center", justifyContent: "center", padding: spacing.xxl, gap: spacing.sm },
  emptyChatTxt: { color: colors.muted, textAlign: "center" },
  composer: {
    flexDirection: "row",
    alignItems: "flex-end",
    padding: spacing.sm,
    gap: spacing.xs,
    borderTopWidth: 1,
    borderColor: colors.surfaceTertiary,
    backgroundColor: colors.surface,
  },
  composeBtn: {
    width: 40,
    height: 40,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 20,
    backgroundColor: colors.surfaceSecondary,
  },
  input: {
    flex: 1,
    minHeight: 40,
    maxHeight: 120,
    borderWidth: 1,
    borderColor: colors.surfaceTertiary,
    borderRadius: 20,
    paddingHorizontal: spacing.md,
    paddingVertical: 10,
    color: colors.onSurface,
    backgroundColor: colors.surfaceSecondary,
    fontSize: font.sizes.base,
  },
  sendBtn: {
    width: 40,
    height: 40,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 20,
    backgroundColor: colors.brandPrimary,
  },
  pendingRow: {
    flexDirection: "row",
    alignItems: "center",
    padding: spacing.sm,
    gap: spacing.sm,
    backgroundColor: colors.surfaceSecondary,
    borderTopWidth: 1,
    borderColor: colors.surfaceTertiary,
  },
  pendingImg: { width: 60, height: 60, borderRadius: 8 },
  pendingClose: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: colors.brandPrimary,
    alignItems: "center",
    justifyContent: "center",
  },
  blockedBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: colors.brandPrimary,
    padding: spacing.md,
  },
  blockedTxt: { color: colors.onBrandPrimary, flex: 1, fontSize: font.sizes.sm },
  modalBg: { flex: 1, backgroundColor: "rgba(0,0,0,0.55)", justifyContent: "center", padding: spacing.lg },
  reactSheet: {
    backgroundColor: colors.surfaceSecondary,
    padding: spacing.md,
    borderRadius: 16,
    gap: spacing.md,
    borderWidth: 2,
    borderColor: colors.border,
  },
  reactRow: { flexDirection: "row", flexWrap: "wrap", justifyContent: "space-around", gap: spacing.sm },
  reactBtn: { padding: spacing.sm },
  reactAction: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    borderTopWidth: 1,
    borderColor: colors.surfaceTertiary,
    paddingTop: spacing.sm,
  },
  sheetTitle: { fontSize: font.sizes.sm, letterSpacing: 2, textAlign: "center", color: colors.onSurface, fontWeight: "500" },
  menuSheet: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: 16,
    borderWidth: 2,
    borderColor: colors.border,
    overflow: "hidden",
  },
  menuItem: {
    flexDirection: "row",
    alignItems: "center",
    padding: spacing.md,
    gap: spacing.sm,
    borderBottomWidth: 1,
    borderColor: colors.surfaceTertiary,
  },
  menuTxt: { fontSize: font.sizes.base, color: colors.onSurface, letterSpacing: 0.5 },
  reportSheet: {
    backgroundColor: colors.surfaceSecondary,
    padding: spacing.lg,
    borderRadius: 16,
    borderWidth: 2,
    borderColor: colors.border,
    gap: spacing.md,
  },
  reportInput: {
    minHeight: 100,
    maxHeight: 200,
    borderWidth: 1,
    borderColor: colors.surfaceTertiary,
    borderRadius: 8,
    padding: spacing.md,
    color: colors.onSurface,
    textAlignVertical: "top",
  },
  reportBtn: {
    flex: 1,
    padding: spacing.md,
    alignItems: "center",
    borderRadius: 8,
  },
  viewerBg: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.95)",
    alignItems: "center",
    justifyContent: "center",
  },
  viewerCloseBtn: {
    position: "absolute",
    top: 44,
    right: 16,
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: "rgba(0,0,0,0.55)",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 10,
  },
  viewerImg: { width: "100%", height: "100%" },
});
