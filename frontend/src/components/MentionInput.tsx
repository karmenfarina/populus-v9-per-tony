import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  TextInput,
  Pressable,
  StyleSheet,
  Image,
  NativeSyntheticEvent,
  TextInputSelectionChangeEventData,
  ActivityIndicator,
  TextInputProps,
} from "react-native";
import { api, MiniUser } from "@/src/api";
import { colors, spacing, font, radius } from "@/src/theme";
import { detectMentionQuery } from "@/src/utils/mentions";

/**
 * A drop-in replacement for `<TextInput>` that shows an @mention
 * autocomplete dropdown while the user is typing. When the caret is on
 * a live `@partial` token (see `detectMentionQuery`) it queries
 * `/search/users?q=partial`, debounces requests, and renders a small
 * bordered popup ABOVE the input with up to 6 clickable rows.
 *
 * Tapping a suggestion:
 *  1. Replaces the partial with the full `@nickname `.
 *  2. Notifies the parent via `onChangeText` with the resolved text.
 *  3. Closes the popup.
 *
 * The popup is fully controlled by internal state and disappears the
 * moment the caret leaves the mention token OR the user types a
 * space. There is no imperative ref API — the whole dropdown is a
 * regular child of the container so it also plays nicely inside
 * `ScrollView` / KeyboardAvoidingView layouts.
 */
export type MentionInputProps = Omit<TextInputProps, "onChangeText"> & {
  value: string;
  onChangeText: (v: string) => void;
  /** Optional testID passed straight through to the underlying TextInput. */
  inputTestID?: string;
  /** Custom container style. */
  containerStyle?: TextInputProps["style"];
  /**
   * If provided, the mention autocomplete boosts users who have
   * commented on THIS feud. Passing it turns the popup from a generic
   * "who's around" list into a context-aware "who's in this thread"
   * shortlist — huge UX win for replies.
   */
  feudId?: string;
};

export default function MentionInput({
  value,
  onChangeText,
  inputTestID,
  containerStyle,
  feudId,
  ...rest
}: MentionInputProps) {
  const [selection, setSelection] = useState<{ start: number; end: number }>({
    start: 0,
    end: 0,
  });
  const [suggestions, setSuggestions] = useState<MiniUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [mentionRange, setMentionRange] = useState<{
    start: number;
    end: number;
  } | null>(null);
  const searchTimer = useRef<any>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Latest caret / text tracked in a ref so a stale timer callback
  // doesn't act on obsolete positions after fast typing.
  const stateRef = useRef({ value, caret: 0 });
  stateRef.current = { value, caret: selection.end };

  const closePopup = useCallback(() => {
    setSuggestions([]);
    setMentionRange(null);
    setLoading(false);
    if (searchTimer.current) {
      clearTimeout(searchTimer.current);
      searchTimer.current = null;
    }
  }, []);

  // Whenever the caret or the text changes, re-evaluate whether we're
  // inside an @mention token. Debounce the network call so we don't
  // hammer /search/users on every keystroke.
  useEffect(() => {
    const detect = detectMentionQuery(value, selection.end);
    if (!detect) {
      closePopup();
      return;
    }
    setMentionRange({ start: detect.start, end: detect.end });
    if (searchTimer.current) clearTimeout(searchTimer.current);
    setLoading(true);
    searchTimer.current = setTimeout(async () => {
      // Cancel any in-flight request from a previous keystroke.
      try {
        abortRef.current?.abort();
      } catch {
        /* noop */
      }
      const q = detect.query.trim();
      // Empty query is now VALID: the user just typed `@` — show the
      // top proximity-ranked candidates (Cerchia, DM contacts, reply
      // partners, thread commenters) so they can pick a friend without
      // typing anything else. This is the "Instagram-style" default
      // suggestion list the user asked for.
      try {
        const res: any = await api.mentionSuggest(q, feudId, 6);
        // Ignore if the caret has since left the mention token.
        const still = detectMentionQuery(
          stateRef.current.value,
          stateRef.current.caret,
        );
        if (!still) {
          setSuggestions([]);
          setLoading(false);
          return;
        }
        setSuggestions(Array.isArray(res?.users) ? res.users : []);
      } catch {
        setSuggestions([]);
      } finally {
        setLoading(false);
      }
    }, 180);
    return () => {
      if (searchTimer.current) clearTimeout(searchTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, selection.end, feudId]);

  const onSelectionChange = (
    e: NativeSyntheticEvent<TextInputSelectionChangeEventData>,
  ) => {
    setSelection(e.nativeEvent.selection);
  };

  const applySuggestion = (u: MiniUser) => {
    if (!mentionRange) return;
    const { start, end } = mentionRange;
    const nickname = (u.nickname || "").toLowerCase();
    const inserted = `@${nickname} `;
    const next = value.slice(0, start) + inserted + value.slice(end);
    onChangeText(next);
    closePopup();
  };

  return (
    <View style={styles.wrap}>
      {mentionRange && (suggestions.length > 0 || loading) ? (
        <View style={styles.popup} testID="mention-popup">
          {loading && suggestions.length === 0 ? (
            <View style={styles.loadingRow}>
              <ActivityIndicator size="small" color={colors.brandPrimary} />
              <Text style={styles.loadingTxt}>Cerco utenti…</Text>
            </View>
          ) : (
            suggestions.map((u) => (
              <Pressable
                key={u.user_id}
                onPress={() => applySuggestion(u)}
                style={({ pressed }) => [
                  styles.row,
                  pressed && { backgroundColor: colors.surfaceSecondary },
                ]}
                testID={`mention-suggestion-${u.nickname}`}
              >
                {u.photo_data ? (
                  <Image
                    source={{ uri: `data:image/*;base64,${u.photo_data}` }}
                    style={styles.avatar}
                  />
                ) : (
                  <View style={[styles.avatar, styles.avatarPlaceholder]}>
                    <Text style={styles.avatarInitials}>
                      {(u.nickname || "?").slice(0, 1).toUpperCase()}
                    </Text>
                  </View>
                )}
                <View style={styles.rowText}>
                  <Text style={styles.nick} numberOfLines={1}>
                    @{u.nickname}
                  </Text>
                  {u.display_name ? (
                    <Text style={styles.displayName} numberOfLines={1}>
                      {u.display_name}
                    </Text>
                  ) : null}
                </View>
              </Pressable>
            ))
          )}
        </View>
      ) : null}
      <TextInput
        {...rest}
        value={value}
        onChangeText={onChangeText}
        onSelectionChange={onSelectionChange}
        testID={inputTestID}
        style={containerStyle}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: "relative" },
  popup: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: "100%",
    marginBottom: spacing.xs,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    borderRadius: radius.md,
    paddingVertical: spacing.xs,
    zIndex: 20,
    // Elevation on Android + shadow on iOS so the popup floats above
    // the surrounding comment cards.
    elevation: 8,
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.4,
    shadowRadius: 8,
    maxHeight: 220,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  loadingRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  loadingTxt: {
    color: colors.muted,
    fontSize: font.sizes.sm,
  },
  avatar: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: colors.surfaceSecondary,
  },
  avatarPlaceholder: {
    alignItems: "center",
    justifyContent: "center",
  },
  avatarInitials: {
    color: colors.onSurface,
    fontSize: font.sizes.sm,
    fontWeight: "700",
  },
  nick: {
    color: colors.onSurface,
    fontSize: font.sizes.base,
    fontWeight: "600",
  },
  rowText: { flex: 1, minWidth: 0 },
  displayName: {
    color: colors.muted,
    fontSize: font.sizes.xs,
    marginTop: 1,
  },
});
