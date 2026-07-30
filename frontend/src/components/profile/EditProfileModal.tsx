import React from "react";
import {
  View,
  Text,
  StyleSheet,
  Modal,
  Pressable,
  ScrollView,
  ActivityIndicator,
  TextInput,
  Image,
  KeyboardAvoidingView,
  Platform,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, spacing, font } from "@/src/theme";
import type { UserPhoto } from "@/src/api";
import {
  sanitizeNicknameInput,
  NICKNAME_HINT,
  NICKNAME_MAX,
} from "@/src/utils/nickname";
import { Socials, SOCIAL_KEYS, SOCIAL_LABELS } from "@/src/utils/socials";

/**
 * "MODIFICA PROFILO" (edit profile) modal — biggest sheet in the app.
 * Handles nickname, display name, photo management (add / re-crop /
 * delete / set primary), bio and social links.
 *
 * Extracted from profile.tsx to shrink that file below the 1000-line
 * threshold. Purely presentational — all state lives in the parent
 * (nickname text, photos list, bio text, socials map, etc.) and every
 * mutation is delegated back via callbacks. This keeps the parent as
 * the single source of truth so the same photo edit + refresh cycle
 * used by the crop modal keeps working seamlessly.
 */
export type EditProfileModalProps = {
  visible: boolean;
  onClose: () => void;

  // Nickname + display name.
  nickname: string;
  onNicknameChange: (v: string) => void;
  displayName: string;
  onDisplayNameChange: (v: string) => void;

  // Photos.
  photos: UserPhoto[];
  loadingPhotos: boolean;
  photoUris: Record<string, string>;
  primaryPhotoId?: string | null;
  onSetPrimary: (photoId: string) => void;
  onRecropPhoto: (photo: UserPhoto) => void;
  openingRecropId: string | null;
  onDeletePhoto: (photoId: string) => void;
  onPickPhoto: (source: "library" | "camera") => void;

  // Bio + socials.
  bio: string;
  onBioChange: (v: string) => void;
  socials: Socials;
  onSocialsChange: (updater: (prev: Socials) => Socials) => void;

  // Save.
  saving: boolean;
  onSave: () => void;
  error: string | null;
};

export default function EditProfileModal(props: EditProfileModalProps) {
  const {
    visible,
    onClose,
    nickname,
    onNicknameChange,
    displayName,
    onDisplayNameChange,
    photos,
    loadingPhotos,
    photoUris,
    primaryPhotoId,
    onSetPrimary,
    onRecropPhoto,
    openingRecropId,
    onDeletePhoto,
    onPickPhoto,
    bio,
    onBioChange,
    socials,
    onSocialsChange,
    saving,
    onSave,
    error,
  } = props;

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <Pressable style={styles.modalBackdrop} onPress={onClose}>
        <Pressable
          style={[styles.modalSheet, styles.modalSheetTall]}
          onPress={(e) => e.stopPropagation()}
          testID="profile-edit-modal"
        >
          <View style={styles.modalHead}>
            <Text style={styles.modalTitle}>MODIFICA PROFILO</Text>
            <Pressable onPress={onClose} testID="profile-edit-close" hitSlop={10}>
              <Ionicons name="close" size={26} color={colors.onSurface} />
            </Pressable>
          </View>
          <KeyboardAvoidingView
            style={{ flex: 1 }}
            behavior={Platform.OS === "ios" ? "padding" : undefined}
          >
            <ScrollView
              style={{ flex: 1 }}
              contentContainerStyle={styles.modalBody}
              keyboardShouldPersistTaps="handled"
              showsVerticalScrollIndicator={false}
            >
              <Text style={styles.editSectionTitle}>NICKNAME</Text>
              <TextInput
                testID="edit-nickname-input"
                value={nickname}
                onChangeText={(t) => onNicknameChange(sanitizeNicknameInput(t))}
                placeholder="es. gossip_queen"
                placeholderTextColor={colors.muted}
                autoCapitalize="none"
                autoCorrect={false}
                maxLength={NICKNAME_MAX}
                style={styles.identInput}
              />
              <Text style={styles.identHint}>
                2-24 caratteri. {NICKNAME_HINT} Deve essere unico.
              </Text>

              <Text style={[styles.editSectionTitle, { marginTop: spacing.md }]}>NOME</Text>
              <TextInput
                testID="edit-display-input"
                value={displayName}
                onChangeText={(t) => onDisplayNameChange(t.slice(0, 40))}
                placeholder="Es. Mario Rossi (opzionale)"
                placeholderTextColor={colors.muted}
                maxLength={40}
                style={styles.identInput}
              />
              <Text style={styles.identHint}>
                Nome visibile sotto al nickname. Lascia vuoto per rimuoverlo.
              </Text>

              <Text style={[styles.editSectionTitle, { marginTop: spacing.md }]}>
                FOTO ({photos.length}/7)
              </Text>
              <View style={styles.photosGrid}>
                {loadingPhotos ? (
                  <ActivityIndicator color={colors.brandPrimary} />
                ) : (
                  <>
                    {photos.map((p) => {
                      const isPrimary = p.photo_id === primaryPhotoId;
                      return (
                        <View
                          key={p.photo_id}
                          style={styles.photoBox}
                          testID={`photo-${p.photo_id}`}
                        >
                          <Image
                            source={{
                              uri:
                                photoUris[p.photo_id] ||
                                `data:image/jpeg;base64,${p.data}`,
                            }}
                            style={styles.photoImg}
                          />
                          {isPrimary && (
                            <View style={styles.primaryBadge}>
                              <Ionicons
                                name="star"
                                size={12}
                                color={colors.onBrandSecondary}
                              />
                            </View>
                          )}
                          <View style={styles.photoActions}>
                            {!isPrimary && (
                              <Pressable
                                onPress={() => onSetPrimary(p.photo_id)}
                                testID={`photo-set-primary-${p.photo_id}`}
                                style={styles.photoAct}
                              >
                                <Ionicons
                                  name="star-outline"
                                  size={14}
                                  color={colors.onSurface}
                                />
                              </Pressable>
                            )}
                            <Pressable
                              onPress={() => onRecropPhoto(p)}
                              disabled={openingRecropId === p.photo_id}
                              testID={`photo-recrop-${p.photo_id}`}
                              style={styles.photoAct}
                            >
                              {openingRecropId === p.photo_id ? (
                                <ActivityIndicator
                                  size="small"
                                  color={colors.onSurface}
                                />
                              ) : (
                                <Ionicons
                                  name="crop-outline"
                                  size={14}
                                  color={colors.onSurface}
                                />
                              )}
                            </Pressable>
                            <Pressable
                              onPress={() => onDeletePhoto(p.photo_id)}
                              testID={`photo-delete-${p.photo_id}`}
                              style={[
                                styles.photoAct,
                                { backgroundColor: colors.brandPrimary },
                              ]}
                            >
                              <Ionicons
                                name="trash"
                                size={14}
                                color={colors.onBrandPrimary}
                              />
                            </Pressable>
                          </View>
                        </View>
                      );
                    })}
                    {photos.length < 7 && (
                      <>
                        <Pressable
                          onPress={() => onPickPhoto("library")}
                          testID="photo-add-library"
                          style={[styles.photoBox, styles.photoAdd]}
                        >
                          <Ionicons
                            name="images-outline"
                            size={30}
                            color={colors.onSurface}
                          />
                          <Text style={styles.photoAddTxt}>GALLERIA</Text>
                        </Pressable>
                        <Pressable
                          onPress={() => onPickPhoto("camera")}
                          testID="photo-add-camera"
                          style={[styles.photoBox, styles.photoAdd]}
                        >
                          <Ionicons
                            name="camera-outline"
                            size={30}
                            color={colors.onSurface}
                          />
                          <Text style={styles.photoAddTxt}>FOTOCAMERA</Text>
                        </Pressable>
                      </>
                    )}
                  </>
                )}
              </View>

              <Text style={[styles.editSectionTitle, { marginTop: spacing.md }]}>
                BIO ({bio.length}/200)
              </Text>
              <TextInput
                value={bio}
                onChangeText={(t) => onBioChange(t.slice(0, 200))}
                placeholder="Racconta chi sei..."
                placeholderTextColor={colors.muted}
                multiline
                style={styles.bioInput}
                testID="bio-input"
              />

              <Text style={[styles.editSectionTitle, { marginTop: spacing.md }]}>SOCIAL</Text>
              {SOCIAL_KEYS.map((k) => (
                <View key={k} style={styles.socialField}>
                  <Text style={styles.socialFieldLabel}>{SOCIAL_LABELS[k]}</Text>
                  <TextInput
                    value={socials[k]}
                    onChangeText={(t) =>
                      onSocialsChange((s) => ({ ...s, [k]: t }))
                    }
                    placeholder={k === "website" ? "esempio.it" : `@handle o url`}
                    placeholderTextColor={colors.muted}
                    autoCapitalize="none"
                    keyboardType="url"
                    style={styles.socialInput}
                    testID={`social-input-${k}`}
                  />
                </View>
              ))}

              {error && (
                <Text style={styles.prefsErr} testID="details-error">
                  {error}
                </Text>
              )}
            </ScrollView>
            {error ? (
              <View style={styles.saveErrorBar} testID="details-error-bar">
                <Ionicons name="alert-circle" size={16} color="#FFFFFF" />
                <Text style={styles.saveErrorTxt} numberOfLines={2}>
                  {error}
                </Text>
              </View>
            ) : null}
            <Pressable
              onPress={onSave}
              disabled={saving}
              testID="profile-edit-save"
              style={styles.prefsSaveBtn}
            >
              {saving ? (
                <ActivityIndicator color={colors.onBrandPrimary} />
              ) : (
                <Text style={styles.prefsSaveTxt}>SALVA</Text>
              )}
            </Pressable>
          </KeyboardAvoidingView>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "flex-end" },
  modalSheet: { backgroundColor: colors.surface, borderTopWidth: 2, borderColor: colors.border, maxHeight: "85%" },
  modalSheetTall: { height: "92%", maxHeight: "92%" },
  modalHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", padding: spacing.lg, borderBottomWidth: 1, borderColor: colors.border, backgroundColor: colors.surfaceSecondary },
  modalTitle: { color: colors.onSurface, fontSize: font.sizes.xl, letterSpacing: 2, fontWeight: "800" },
  modalBody: { padding: spacing.lg, gap: spacing.md },
  editSectionTitle: { fontSize: font.sizes.sm, letterSpacing: 2, fontWeight: "700", color: colors.brandSecondary },
  // Ident inputs (nickname, display name). Previously these two styles
  // were empty objects — the TextInput inherited the transparent bg
  // with black text, which is unreadable on the dark surface. Now they
  // match the bio/social inputs below.
  identInput: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 8,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm + 2,
    fontSize: font.sizes.base,
    color: colors.onSurface,
    backgroundColor: colors.surfaceSecondary,
  },
  identHint: {
    fontSize: font.sizes.xs,
    color: colors.muted,
    lineHeight: 18,
  },
  photosGrid: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm, marginTop: spacing.xs },
  photoBox: { width: 90, height: 90, borderWidth: 2, borderColor: colors.border, position: "relative", overflow: "hidden", backgroundColor: colors.surfaceSecondary },
  photoImg: { width: "100%", height: "100%" },
  primaryBadge: { position: "absolute", top: 4, left: 4, width: 20, height: 20, backgroundColor: colors.brandSecondary, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: colors.border },
  photoActions: { position: "absolute", bottom: 4, right: 4, flexDirection: "row", gap: 4 },
  photoAct: { width: 26, height: 26, borderWidth: 2, borderColor: colors.border, backgroundColor: colors.surface, alignItems: "center", justifyContent: "center" },
  photoAdd: { alignItems: "center", justifyContent: "center", gap: 4, backgroundColor: colors.surfaceSecondary, borderStyle: "dashed" },
  photoAddTxt: { fontSize: 10, letterSpacing: 1, color: colors.onSurface, fontWeight: "500" },
  bioInput: { borderWidth: 2, borderColor: colors.border, padding: spacing.sm, minHeight: 90, fontSize: font.sizes.base, color: colors.onSurface, backgroundColor: colors.surfaceSecondary, textAlignVertical: "top" },
  socialField: { gap: 4 },
  socialFieldLabel: { fontSize: font.sizes.xs, letterSpacing: 1, color: colors.muted },
  socialInput: { borderWidth: 2, borderColor: colors.border, padding: spacing.sm, fontSize: font.sizes.base, color: colors.onSurface, backgroundColor: colors.surfaceSecondary },
  prefsErr: { color: colors.error, borderWidth: 2, borderColor: colors.error, padding: spacing.sm, fontSize: font.sizes.base },
  saveErrorBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: colors.error,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
  },
  saveErrorTxt: { flex: 1, color: "#FFFFFF", fontSize: font.sizes.sm, fontWeight: "600" },
  prefsSaveBtn: { backgroundColor: colors.brandPrimary, borderTopWidth: 2, borderColor: colors.border, paddingVertical: spacing.lg, alignItems: "center" },
  prefsSaveTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.xl, letterSpacing: 2, fontWeight: "500" },
});
