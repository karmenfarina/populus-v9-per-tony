import React, { useState } from "react";
import {
  View,
  Text,
  Modal,
  Pressable,
  TextInput,
  StyleSheet,
  Alert,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Image,
  ScrollView,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { api, ApiError } from "@/src/api";
import { useStoryUpload } from "@/src/stories/StoryUploadContext";
import { colors, spacing, font } from "@/src/theme";

/**
 * Story composer bottom sheet.
 *
 * Users land here from the feud share flow after tapping "Aggiungi alla
 * tua storia". Shows a compact preview of the feud that will be posted
 * plus an optional 200-char comment field. Publishing runs through the
 * standard AI moderation pipeline server-side.
 *
 * On success we invoke `onPublished()` so the caller can dismiss any
 * parent sheet and show a confirmation toast. Errors surface via
 * Alert.alert so the user knows exactly why a comment was rejected.
 */

const COMMENT_MAX = 200;

type FeudPreview = {
  feud_id: string;
  title?: string;
  category_label?: string;
  party_a?: string;
  party_b?: string;
  image_url?: string;
};

type Props = {
  visible: boolean;
  feud: FeudPreview | null;
  onClose: () => void;
  onPublished?: () => void;
};

export default function StoryComposerModal({ visible, feud, onClose, onPublished }: Props) {
  const [comment, setComment] = useState("");
  const [publishing, setPublishing] = useState(false);
  const { beginUpload, endUpload } = useStoryUpload();

  const remaining = COMMENT_MAX - comment.length;

  const publish = async () => {
    if (!feud?.feud_id) return;
    setPublishing(true);
    // Signal the global "story uploading" flag so the StoriesBar can
    // animate the user's ring for the ENTIRE duration of the upload
    // (from tap to server confirmation), matching the loading-state
    // behaviour on Instagram. Local `publishing` continues to gate
    // the modal's own submit button.
    beginUpload();
    try {
      await api.createStory(feud.feud_id, comment.trim() || undefined);
      setComment("");
      onPublished?.();
      onClose();
      // Confirmation via native alert — will be replaced by an in-app
      // toast once we have a shared toast primitive.
      setTimeout(() => Alert.alert("Storia pubblicata", "La tua storia è ora visibile alla tua Cerchia."), 100);
    } catch (e: any) {
      // Map backend errors to human-readable Italian messages with a
      // context-appropriate title. The `ApiError.status` is set by the
      // request() helper in `src/api.ts` and mirrors the HTTP status
      // returned by the server.
      const status = e instanceof ApiError ? e.status : 0;
      const detail = (e?.message || "").trim();
      let title = "Impossibile pubblicare";
      let message = detail || "Errore sconosciuto";
      if (status === 429) {
        // Daily quota exhausted (STORY_DAILY_QUOTA on backend, currently 20).
        title = "Limite giornaliero raggiunto";
        message =
          detail && detail.toLowerCase().includes("limite")
            ? `${detail}\n\nRiprova domani.`
            : "Hai raggiunto il limite di storie che puoi pubblicare oggi. Riprova domani.";
      } else if (status === 403) {
        title = "Operazione non consentita";
        message = detail || "Non puoi pubblicare storie con questo account.";
      } else if (status === 404) {
        title = "Faida non trovata";
        message = "La faida che stai cercando di condividere non è più disponibile.";
      } else if (status === 400) {
        // Moderation-blocked comment or similar validation error.
        title = "Contenuto non pubblicabile";
        message = detail || "Il commento non rispetta le regole della community.";
      } else if (status === 0) {
        title = "Connessione assente";
        message = "Impossibile contattare il server. Controlla la connessione e riprova.";
      } else if (status >= 500) {
        title = "Errore del server";
        message = "Si è verificato un problema sul server. Riprova tra poco.";
      }
      // Keep the tech-side breadcrumb for local debug builds; harmless in prod.
      console.warn("[StoryComposer] publish failed", { status, detail });
      Alert.alert(title, message);
    } finally {
      setPublishing(false);
      endUpload();
    }
  };

  const handleClose = () => {
    if (publishing) return;
    setComment("");
    onClose();
  };

  return (
    <Modal
      animationType="slide"
      transparent
      visible={visible}
      onRequestClose={handleClose}
    >
      <Pressable style={styles.backdrop} onPress={handleClose}>
        <Pressable style={styles.sheet} onPress={() => { /* consume */ }}>
          <KeyboardAvoidingView
            behavior={Platform.OS === "ios" ? "padding" : undefined}
            style={{ flex: 1 }}
          >
            <View style={styles.handleWrap}><View style={styles.handle} /></View>
            <View style={styles.header}>
              <Text style={styles.title}>NUOVA STORIA</Text>
              <Pressable onPress={handleClose} hitSlop={8} testID="story-composer-close">
                <Ionicons name="close" size={22} color={colors.onSurface} />
              </Pressable>
            </View>

            <ScrollView
              contentContainerStyle={styles.body}
              keyboardShouldPersistTaps="handled"
              showsVerticalScrollIndicator={false}
            >
              {feud ? (
                <View style={styles.preview}>
                  {feud.image_url ? (
                    <Image source={{ uri: feud.image_url }} style={styles.previewImage} />
                  ) : (
                    <View style={[styles.previewImage, styles.previewImageFallback]}>
                      <Ionicons name="newspaper-outline" size={36} color={colors.muted} />
                    </View>
                  )}
                  <View style={styles.previewBody}>
                    {feud.category_label ? (
                      <Text style={styles.previewCat} numberOfLines={1}>
                        {feud.category_label.toUpperCase()}
                      </Text>
                    ) : null}
                    <Text style={styles.previewTitle} numberOfLines={3}>
                      {feud.title}
                    </Text>
                    {(feud.party_a || feud.party_b) ? (
                      <Text style={styles.previewVs} numberOfLines={1}>
                        {feud.party_a} <Text style={styles.previewVsSep}>vs</Text> {feud.party_b}
                      </Text>
                    ) : null}
                  </View>
                </View>
              ) : null}

              <View style={styles.commentBlock}>
                <View style={styles.commentLabelRow}>
                  <Text style={styles.commentLabel}>La tua opinione (opzionale)</Text>
                  <Text style={[styles.commentCounter, remaining < 20 ? styles.commentCounterWarn : null]}>
                    {remaining}
                  </Text>
                </View>
                <TextInput
                  style={styles.commentInput}
                  value={comment}
                  onChangeText={(t) => t.length <= COMMENT_MAX && setComment(t)}
                  placeholder="Scrivi cosa ne pensi… (max 200 caratteri)"
                  placeholderTextColor={colors.muted}
                  multiline
                  textAlignVertical="top"
                  maxLength={COMMENT_MAX}
                  editable={!publishing}
                  testID="story-composer-comment"
                />
              </View>

              <View style={styles.disclaimer}>
                <Ionicons name="time-outline" size={14} color={colors.muted} />
                <Text style={styles.disclaimerTxt}>
                  La storia sarà visibile alla tua Cerchia per 24 ore.
                </Text>
              </View>

              <Pressable
                onPress={publish}
                disabled={publishing || !feud?.feud_id}
                style={[styles.publishBtn, (publishing || !feud) && { opacity: 0.5 }]}
                testID="story-composer-publish"
              >
                {publishing ? (
                  <ActivityIndicator color={colors.onBrandPrimary} />
                ) : (
                  <>
                    <Ionicons name="camera-outline" size={18} color={colors.onBrandPrimary} />
                    <Text style={styles.publishTxt}>PUBBLICA STORIA</Text>
                  </>
                )}
              </Pressable>
            </ScrollView>
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
    paddingBottom: spacing.xl,
    maxHeight: "85%",
  },
  handleWrap: { alignItems: "center", paddingVertical: spacing.xs },
  handle: { width: 44, height: 4, borderRadius: 2, backgroundColor: colors.border },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.sm,
    borderBottomWidth: 2,
    borderColor: colors.border,
    marginBottom: spacing.md,
  },
  title: {
    color: colors.onSurface,
    fontSize: font.sizes.xl,
    letterSpacing: 2,
    fontWeight: "500",
  },
  body: {
    paddingBottom: spacing.md,
  },
  preview: {
    flexDirection: "row",
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 8,
    padding: spacing.sm,
    gap: spacing.sm,
  },
  previewImage: {
    width: 84,
    height: 84,
    borderRadius: 6,
    backgroundColor: colors.surfaceTertiary,
  },
  previewImageFallback: {
    alignItems: "center",
    justifyContent: "center",
  },
  previewBody: {
    flex: 1,
  },
  previewCat: {
    color: colors.brandPrimary,
    fontSize: 10,
    fontWeight: "700",
    letterSpacing: 1.5,
    marginBottom: 2,
  },
  previewTitle: {
    color: colors.onSurface,
    fontSize: font.sizes.sm,
    fontWeight: "700",
    lineHeight: 18,
  },
  previewVs: {
    color: colors.muted,
    fontSize: font.sizes.xs,
    marginTop: 6,
  },
  previewVsSep: {
    fontWeight: "700",
    color: colors.brandPrimary,
  },
  commentBlock: {
    marginTop: spacing.md,
  },
  commentLabelRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-end",
    marginBottom: 6,
  },
  commentLabel: {
    color: colors.onSurface,
    fontSize: font.sizes.xs,
    fontWeight: "700",
    letterSpacing: 1,
  },
  commentCounter: {
    color: colors.muted,
    fontSize: font.sizes.xs,
  },
  commentCounterWarn: {
    color: colors.brandPrimary,
  },
  commentInput: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 8,
    padding: spacing.sm,
    minHeight: 100,
    color: colors.onSurface,
    backgroundColor: colors.surface,
    fontSize: font.sizes.sm,
  },
  disclaimer: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    marginTop: spacing.sm,
    marginBottom: spacing.md,
  },
  disclaimerTxt: {
    color: colors.muted,
    fontSize: font.sizes.xs,
  },
  publishBtn: {
    backgroundColor: colors.brandPrimary,
    borderRadius: 8,
    paddingVertical: spacing.md,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    marginTop: spacing.sm,
  },
  publishTxt: {
    color: colors.onBrandPrimary,
    fontSize: font.sizes.sm,
    fontWeight: "700",
    letterSpacing: 1.5,
  },
});
