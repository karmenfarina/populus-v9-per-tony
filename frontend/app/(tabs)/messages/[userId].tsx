import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import * as Clipboard from "expo-clipboard";
import * as FileSystem from "expo-file-system/legacy";
import { api, ChatMessage, MiniUser } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { useMessaging } from "@/src/messaging/MessagingContext";
import { colors, spacing, font, radius } from "@/src/theme";
import { ScrollToTopButton } from "@/src/components/ScrollToTopButton";
import { useSmartBack } from "@/src/utils/useSmartBack";

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
/**
 * Module-level cache of last-fetched messages per `other_user_id`.
 *
 * Opening a chat used to flash a full-screen loading spinner before
 * the messages appeared — that "millisecond of something different"
 * the user reported. By keeping the last-seen messages in memory (per
 * session) we can render them INSTANTLY on re-mount and refresh in
 * the background, matching Instagram/WhatsApp behaviour.
 */
type ChatCacheEntry = {
  other_user: MiniUser;
  messages: ChatMessage[];
  i_blocked: boolean;
  they_blocked: boolean;
};
const chatCache: Map<string, ChatCacheEntry> = new Map();

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
  void from; // kept for backwards compat — useSmartBack reads the param itself
  const router = useRouter();
  const { user } = useAuth();
  const { subscribe, refresh: refreshBadge } = useMessaging();

  // Standard back behaviour — respects `?from=…` and Android hardware back.
  const goBackToMessagesList = useSmartBack("/messages");

  // Boot from the in-memory cache if we've already fetched this chat
  // during this session — makes re-opens instant instead of flashing a
  // full-screen loading spinner.
  const initialCache = userId ? chatCache.get(String(userId)) : null;
  const [otherUser, setOtherUser] = useState<MiniUser | null>(initialCache?.other_user ?? null);
  const [messages, setMessages] = useState<ChatMessage[]>(initialCache?.messages ?? []);
  // `loading` only gates the full-page spinner. If we already have
  // cached content we skip the spinner entirely — the background
  // refetch below runs silently under the visible messages.
  const [loading, setLoading] = useState(!initialCache);
  const [sending, setSending] = useState(false);
  const [text, setText] = useState("");
  const [iBlocked, setIBlocked] = useState(initialCache?.i_blocked ?? false);
  const [theyBlocked, setTheyBlocked] = useState(initialCache?.they_blocked ?? false);
  // Track the userId this state currently belongs to. When Expo Router
  // navigates between two chats (`/messages/A` → `/messages/B`) it REUSES
  // the mounted component — so the initial state above stays bound to
  // chat A until the async loadInitial() for B completes. Rendering
  // chat A's messages while `userId=B` produces the "millisecond of
  // previous chat" flash. Two-layer defence:
  //   (a) The effect below resets state IMMEDIATELY when userId changes
  //       (post-commit).
  //   (b) At RENDER TIME we also gate the visible list on
  //       `boundIdRef.current === userId`. This prevents even a single
  //       paint of the previous chat's messages while the effect is
  //       waiting to fire, so the fix is bullet-proof even on slow
  //       devices.
  const boundIdRef = useRef<string | null>(userId ? String(userId) : null);
  const boundIsCurrent = boundIdRef.current === (userId ? String(userId) : null);
  // Render-time views: while the userId is transitioning, all
  // conversation-scoped fields degrade to a neutral empty state.
  const visibleMessages = boundIsCurrent ? messages : [];
  const visibleOtherUser = boundIsCurrent ? otherUser : null;
  const visibleIBlocked = boundIsCurrent ? iBlocked : false;
  const visibleTheyBlocked = boundIsCurrent ? theyBlocked : false;
  useEffect(() => {
    const uid = userId ? String(userId) : null;
    if (uid === boundIdRef.current) return;
    boundIdRef.current = uid;
    const cache = uid ? chatCache.get(uid) : null;
    setOtherUser(cache?.other_user ?? null);
    setMessages(cache?.messages ?? []);
    setIBlocked(cache?.i_blocked ?? false);
    setTheyBlocked(cache?.they_blocked ?? false);
    setLoading(!cache);
    // Any inline compose / modal state that referenced the previous
    // chat should be cleared to avoid leaking across chats.
    setText("");
    setPendingImage(null);
    setReactTarget(null);
    setMenuOpen(false);
    setReportOpen(false);
    setReportText("");
    setConfirmState(null);
    setViewerUri(null);
    setViewerKey("empty");
  }, [userId]);
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
  // Toggle for the floating "back to latest" pill in the chat.
  const [showLatestBtn, setShowLatestBtn] = useState(false);

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
      // Populate the module cache so the next visit skips the spinner.
      chatCache.set(String(userId), {
        other_user: r.other_user,
        messages: r.messages || [],
        i_blocked: !!r.i_blocked,
        they_blocked: !!r.they_blocked,
      });
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
      // Only show the full-screen spinner when we have NOTHING to
      // render (first-ever visit to this chat this session). If the
      // cache primed our state we already show messages instantly and
      // just refresh silently in the background.
      const hasCached = chatCache.has(String(userId));
      if (!hasCached) setLoading(true);
      await loadInitial();
      if (mounted) setLoading(false);
    })();
    return () => {
      mounted = false;
    };
  }, [user, userId, loadInitial]);

  // Keep the module cache in sync with live-updated state so re-opens
  // reflect any WS events (new messages, reactions, deletes) that
  // arrived while the user was inside the chat.
  useEffect(() => {
    if (!userId || !otherUser) return;
    chatCache.set(String(userId), {
      other_user: otherUser,
      messages,
      i_blocked: iBlocked,
      they_blocked: theyBlocked,
    });
  }, [userId, otherUser, messages, iBlocked, theyBlocked]);

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

  // Reliable scroll-to-bottom for the INVERTED FlatList.
  //
  // CRITICAL: in an inverted FlatList, `scrollToEnd()` goes to the LAST item
  // in the data array — which is the OLDEST message and visually the TOP of
  // the screen. That was the bug the user reported: "when I send a message
  // the chat jumps to the top". We must use `scrollToOffset({ offset: 0 })`
  // which is the visual bottom (= newest) in an inverted list.
  //
  // We retry a few times over animation frames because the offset must be
  // applied AFTER the new row is committed to the render tree.
  const scrollToBottom = useCallback((animated: boolean = false) => {
    if (!listRef.current) return;
    let attempts = 0;
    const tick = () => {
      attempts += 1;
      try {
        listRef.current?.scrollToOffset({ offset: 0, animated });
      } catch { /* ignore */ }
      if (attempts < 6) {
        requestAnimationFrame(tick);
      }
    };
    tick();
  }, []);

  // Auto-scroll to newest on first paint / when the message count changes.
  // Using messages.length as the dep so this fires exactly once per new
  // message. On subsequent renders (e.g. reaction updates that mutate an
  // existing message but not the length) we don't disturb the user's
  // scroll position.
  useEffect(() => {
    if (messages.length === 0) return;
    scrollToBottom(false);
  }, [messages.length, scrollToBottom]);

  // Reversed copy of the visible messages array — the FlatList is
  // rendered with `inverted` so the newest message sits at position 0
  // (visually the bottom of the screen). Uses `visibleMessages`
  // (gated on `boundIsCurrent`) so we NEVER paint the previous
  // conversation's messages while a chat transition is in progress.
  const inverseMessages = useMemo(
    () => visibleMessages.slice().reverse(),
    [visibleMessages],
  );

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
    // Force snap-to-newest regardless of where the user was scrolled. In an
    // inverted list, offset 0 is the visual bottom. We schedule after the
    // next frame so the new bubble is actually laid out first.
    requestAnimationFrame(() => scrollToBottom(true));
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
  }, [sending, text, pendingImage, iBlocked, theyBlocked, user, userId, scrollToBottom]);

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

  // Copy a message body to the OS clipboard. Called from the long-press
  // action sheet — the "COPIA MESSAGGIO" button is offered on both my own
  // and the other user's messages, but only when the message actually
  // contains text (image-only bubbles hide the option).
  const copyMessage = useCallback(async (m: ChatMessage) => {
    setReactTarget(null);
    const body = (m.text || "").trim();
    if (!body) return;
    try {
      await Clipboard.setStringAsync(body);
      // Light non-blocking feedback. On web, Alert.alert falls back to
      // window.alert which is disruptive — the ephemeral toast on RN is
      // enough on native, we simply no-op on web to avoid a popup for
      // such a trivial success.
      if (Platform.OS !== "web") {
        Alert.alert("Copiato", "Testo copiato negli appunti.");
      }
    } catch {
      Alert.alert("Errore", "Impossibile copiare il testo");
    }
  }, []);

  const deleteMessage = useCallback(async (m: ChatMessage) => {
    // Direct deletion — no confirmation modal, as requested by the user.
    // The action is already gated by an explicit long-press + tap on the
    // red "ELIMINA MESSAGGIO" button, so double-confirmation is overkill.
    setReactTarget(null);
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
  }, [viewerKey]);

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
      // In the inverted list, `index+1` is the CHRONOLOGICALLY OLDER
      // message (rendered visually ABOVE this one). We show the day
      // divider on top of any message whose older neighbour is either
      // missing (oldest overall) or on a different calendar day.
      const olderNeighbour = index + 1 < inverseMessages.length ? inverseMessages[index + 1] : null;
      const showDay = !olderNeighbour || !isSameDay(olderNeighbour.created_at, item.created_at);
      const reactions = Object.values(item.reactions || {});
      // Snapshot the item locally so tap handlers can never reference a
      // different message due to FlatList row recycling.
      const bubbleItem = item;
      const bubbleImage = item.image_data;
      const bubbleId = item.message_id;
      const bubbleDeleted = !!item.deleted;
      const handleTap = () => {
        if (bubbleDeleted) return;
        // Simple tap only opens the full-screen image viewer for image
        // bubbles. All other actions (delete, react) are triggered by
        // long-press so the user cannot accidentally destroy a message.
        if (bubbleImage) openViewerForMessage(bubbleItem);
      };
      const handleLongPress = () => {
        if (bubbleDeleted) return;
        // Long-press ANYWHERE on the bubble opens the action sheet.
        // The sheet content is contextual to who sent the message:
        //   • my own messages      → "Copia messaggio" + "Elimina messaggio"
        //   • messages from others → emoji reactions + "Copia messaggio"
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
            delayLongPress={350}
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
                <Text
                  selectable={false}
                  style={[styles.txt, mine ? styles.txtMine : styles.txtTheirs, styles.txtItalic]}
                >
                  Messaggio eliminato
                </Text>
              ) : (
                <>
                  {item.story_ref ? (
                    (() => {
                      const s = item.story_ref!;
                      const openFeud = () => {
                        // The card links to the FEUD (post) rather than
                        // the ephemeral story — the feud stays reachable
                        // even after the 24h story window has expired,
                        // matching how the user expects DMs to work:
                        // a reply-to-story should always keep the
                        // conversation about the underlying topic alive.
                        if (!s.feud_id) return;
                        router.push({
                          pathname: "/feud/[id]",
                          params: {
                            id: s.feud_id,
                            from: "messages",
                            messagesUserId: String(userId || ""),
                          },
                        } as any);
                      };
                      return (
                        <Pressable
                          onPress={openFeud}
                          onLongPress={handleLongPress}
                          delayLongPress={350}
                          disabled={!s.feud_id}
                          style={styles.sharedFeudCard}
                          testID={`msg-story-ref-${bubbleId}`}
                        >
                          {s.feud_image_url ? (
                            <Image
                              source={{ uri: s.feud_image_url }}
                              style={styles.sharedFeudImg}
                            />
                          ) : (
                            <View style={[styles.sharedFeudImg, { backgroundColor: colors.surfaceSecondary, alignItems: "center", justifyContent: "center" }]}>
                              <Ionicons name="newspaper-outline" size={40} color={colors.muted} />
                            </View>
                          )}
                          <View style={styles.sharedFeudBody}>
                            <Text selectable={false} style={styles.sharedFeudCat} numberOfLines={1}>
                              RISPOSTA ALLA STORIA
                            </Text>
                            <Text selectable={false} style={styles.sharedFeudTitle} numberOfLines={3}>
                              {s.feud_title || "Apri il post"}
                            </Text>
                            <View style={styles.sharedFeudCta}>
                              <Ionicons name="open-outline" size={14} color={colors.brandPrimary} />
                              <Text selectable={false} style={styles.sharedFeudCtaTxt}>APRI IL POST</Text>
                            </View>
                          </View>
                        </Pressable>
                      );
                    })()
                  ) : null}
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
                      onLongPress={handleLongPress}
                      delayLongPress={350}
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
                          <Text selectable={false} style={styles.sharedFeudCat} numberOfLines={1}>
                            {(item.shared_feud.category_label || item.shared_feud.category || "").toString().toUpperCase()}
                          </Text>
                        ) : null}
                        <Text selectable={false} style={styles.sharedFeudTitle} numberOfLines={3}>
                          {item.shared_feud.title || "Apri il post"}
                        </Text>
                        <View style={styles.sharedFeudCta}>
                          <Ionicons name="open-outline" size={14} color={colors.brandPrimary} />
                          <Text selectable={false} style={styles.sharedFeudCtaTxt}>APRI IL POST</Text>
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
                    <Text
                      selectable={false}
                      style={[styles.txt, mine ? styles.txtMine : styles.txtTheirs]}
                    >
                      {item.text}
                    </Text>
                  ) : null}
                </>
              )}
              <View style={styles.metaRow}>
                <Text selectable={false} style={[styles.metaTxt, mine ? styles.metaMine : styles.metaTheirs]}>
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
    [user, inverseMessages, openViewerForMessage, deleteMessage],
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
            // We render the list INVERTED — newest message at the
            // bottom of the screen, which is also position 0 in an
            // inverted FlatList. This eliminates the "briefly at the
            // top, then jump to bottom" glitch the user was seeing:
            // there's no scroll animation to reach the last message
            // because the list is already anchored there.
            inverted
            data={inverseMessages}
            keyExtractor={(m) => m.message_id}
            renderItem={renderMessage}
            contentContainerStyle={styles.list}
            // Track how far the user has scrolled UP into older messages
            // (contentOffset.y grows as you drag up, because the list is
            // inverted). Show the floating "back to latest" pill once the
            // user is >600px away from the freshest message.
            onScroll={(e) => setShowLatestBtn(e.nativeEvent.contentOffset.y > 600)}
            scrollEventThrottle={120}
            // No scrollToBottom needed with inverted — new messages
            // being prepended (from a WS push) will already appear
            // at the bottom automatically. We still call it once on
            // content size change to snap-to-bottom AFTER the user
            // has sent a new message while scrolled up.
            onContentSizeChange={() => {
              try { listRef.current?.scrollToOffset({ offset: 0, animated: false }); } catch { /* ignore */ }
            }}
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
        <ScrollToTopButton
          visible={showLatestBtn}
          direction="down"
          onPress={() => listRef.current?.scrollToOffset({ offset: 0, animated: true })}
          bottomOffset={80}
          testID="chat-scroll-latest"
        />

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

      {/* Long-press action sheet — content depends on who sent the message:
          • Mine   → "Copia messaggio" (if text) + "Elimina messaggio" (danger)
          • Theirs → Emoji reactions + "Copia messaggio" (if text) */}
      <Modal visible={!!reactTarget} transparent animationType="fade" onRequestClose={() => setReactTarget(null)}>
        <Pressable style={styles.modalBg} onPress={() => setReactTarget(null)}>
          <Pressable style={styles.reactSheet} onPress={() => { /* swallow */ }}>
            {reactTarget && reactTarget.sender_id !== user.user_id && (
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
            )}
            {reactTarget && !!(reactTarget.text && reactTarget.text.trim()) && (
              <Pressable
                onPress={() => {
                  const target = reactTarget;
                  if (target) copyMessage(target);
                }}
                style={styles.reactAction}
                testID="react-sheet-copy"
              >
                <Ionicons name="copy-outline" size={18} color={colors.onSurface} />
                <Text style={{ color: colors.onSurface, letterSpacing: 1 }}>COPIA MESSAGGIO</Text>
              </Pressable>
            )}
            {reactTarget?.sender_id === user.user_id && (
              <Pressable
                onPress={() => {
                  // Close the reactions sheet BEFORE opening the confirm
                  // modal — otherwise the confirm dialog is stacked
                  // underneath the reactions sheet.
                  const target = reactTarget;
                  setReactTarget(null);
                  setTimeout(() => target && deleteMessage(target), 40);
                }}
                style={styles.reactAction}
                testID="react-sheet-delete"
              >
                <Ionicons name="trash-outline" size={18} color={colors.error} />
                <Text style={{ color: colors.error, letterSpacing: 1 }}>ELIMINA MESSAGGIO</Text>
              </Pressable>
            )}
          </Pressable>
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

      {/* Custom confirm modal (replaces Alert.alert for destructive actions).
          Styled to match the confirmation modals used elsewhere in the app
          (see /circle/[userId].tsx) — icon on top, muted body, ghost/danger
          buttons with rounded corners. */}
      <Modal
        visible={!!confirmState}
        transparent
        animationType="fade"
        onRequestClose={() => setConfirmState(null)}
      >
        <Pressable style={styles.confirmBackdrop} onPress={() => setConfirmState(null)}>
          <Pressable style={styles.confirmCard} onPress={() => { /* swallow */ }}>
            <View style={styles.confirmIconWrap}>
              <Ionicons name="trash-outline" size={26} color={colors.error} />
            </View>
            <Text style={styles.confirmTitle}>{confirmState?.title}</Text>
            <Text style={styles.confirmBody}>{confirmState?.body}</Text>
            <View style={styles.confirmBtnRow}>
              <Pressable
                onPress={() => setConfirmState(null)}
                style={[styles.confirmBtn, styles.confirmBtnGhost]}
                testID="confirm-cancel"
              >
                <Text style={styles.confirmBtnGhostTxt}>ANNULLA</Text>
              </Pressable>
              <Pressable
                onPress={() => {
                  const cb = confirmState?.onConfirm;
                  setConfirmState(null);
                  if (cb) cb();
                }}
                style={[styles.confirmBtn, styles.confirmBtnDanger]}
                testID="confirm-ok"
              >
                <Text style={styles.confirmBtnDangerTxt}>CONFERMA</Text>
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
    gap: spacing.sm,
  },
  backBtn: { padding: spacing.sm },
  menuBtn: { padding: spacing.sm },
  headerCenter: { flex: 1, flexDirection: "row", alignItems: "center", gap: spacing.sm },
  headerAvatar: { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.surfaceTertiary },
  headerAvatarPh: { alignItems: "center", justifyContent: "center" },
  headerNick: { color: colors.brandSecondary, fontSize: font.sizes.base, letterSpacing: 0.5, flexShrink: 1, fontWeight: "800" },
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
    // Disable native text selection across the ENTIRE bubble content
    // (text, timestamp, shared-feud card, etc.) so the parent Pressable
    // always receives the long-press event — no matter where the user
    // touches inside the bubble.
    ...(Platform.OS === "web"
      ? ({ userSelect: "none", WebkitUserSelect: "none", WebkitTouchCallout: "none" } as any)
      : {}),
  },
  bubbleMine: { backgroundColor: colors.brandPrimary, borderBottomRightRadius: 4 },
  bubbleTheirs: { backgroundColor: colors.surfaceSecondary, borderBottomLeftRadius: 4, borderWidth: 1, borderColor: colors.surfaceTertiary },
  bubbleDeleted: { opacity: 0.6 },
  txt: {
    fontSize: font.sizes.base,
    lineHeight: 20,
    // Disable native text selection so long-press on the bubble is always
    // captured by the parent Pressable (long-press = delete/react) instead
    // of triggering the platform text-selection UI. Users can still copy
    // the message via the explicit "COPIA MESSAGGIO" action in the sheet.
    ...(Platform.OS === "web" ? ({ userSelect: "none", WebkitUserSelect: "none", cursor: "default" } as any) : {}),
  },
  txtMine: { color: colors.onBrandPrimary },
  txtTheirs: { color: colors.onSurface },
  txtItalic: { fontStyle: "italic" },
  msgImg: { width: 220, height: 220, borderRadius: 8, marginBottom: 6, backgroundColor: colors.surfaceTertiary },
  sharedFeudCard: {
    width: 240,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    marginBottom: 6,
    overflow: "hidden",
  },
  sharedFeudImg: { width: 240, height: 140, backgroundColor: colors.surfaceTertiary },
  sharedFeudBody: { padding: 12, gap: 6 },
  sharedFeudCat: {
    fontSize: 10,
    letterSpacing: 2,
    color: colors.brandPrimary,
    fontWeight: "800",
  },
  sharedFeudTitle: {
    fontSize: font.sizes.base,
    color: colors.onSurface,
    fontWeight: "700",
    lineHeight: 20,
  },
  sharedFeudCta: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginTop: 4,
  },
  sharedFeudCtaTxt: {
    color: colors.brandPrimary,
    fontSize: 11,
    letterSpacing: 1.2,
    fontWeight: "800",
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
    gap: spacing.sm,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  composeBtn: {
    width: 40,
    height: 40,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 20,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: "transparent",
  },
  input: {
    flex: 1,
    minHeight: 40,
    maxHeight: 120,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    borderRadius: radius.pill,
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

  // ==================================================================
  // Confirm modal — matches the styling in /circle/[userId].tsx so all
  // destructive confirmations across the app look and feel consistent.
  // ==================================================================
  confirmBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.65)",
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: spacing.xl,
  },
  confirmCard: {
    width: "100%",
    maxWidth: 340,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.lg,
    padding: spacing.xl,
    gap: spacing.md,
    alignItems: "center",
  },
  confirmIconWrap: {
    width: 56, height: 56,
    borderRadius: 28,
    borderWidth: 1.5, borderColor: colors.error,
    alignItems: "center", justifyContent: "center",
    marginBottom: spacing.xs,
  },
  confirmTitle: {
    color: colors.onSurface,
    fontSize: font.sizes.xl,
    fontWeight: "800",
    letterSpacing: 0.5,
    textAlign: "center",
  },
  confirmBody: {
    color: colors.muted,
    fontSize: font.sizes.base,
    lineHeight: 22,
    textAlign: "center",
  },
  confirmBtnRow: {
    flexDirection: "row",
    gap: spacing.sm,
    marginTop: spacing.sm,
    width: "100%",
  },
  confirmBtn: {
    flex: 1,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    alignItems: "center",
    borderRadius: radius.pill,
  },
  confirmBtnGhost: {
    borderWidth: 1.5,
    borderColor: colors.borderStrong,
    backgroundColor: "transparent",
  },
  confirmBtnGhostTxt: {
    color: colors.onSurface,
    fontWeight: "800",
    fontSize: font.sizes.sm,
    letterSpacing: 1,
  },
  confirmBtnDanger: {
    backgroundColor: colors.error,
  },
  confirmBtnDangerTxt: {
    color: "#FFFFFF",
    fontWeight: "800",
    fontSize: font.sizes.sm,
    letterSpacing: 1,
  },
});
