import { useState, useCallback } from "react";
import { View, Text, StyleSheet, Pressable, ScrollView } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, spacing, font } from "@/src/theme";

/**
 * Compact emoji picker used inside the comment / reply composer.
 *
 * Renders a smile-face toggle button that, when tapped, expands into a
 * horizontal strip of common emojis. Tapping an emoji calls back to the
 * parent so it can be appended to the input text.
 *
 * Kept intentionally minimal — no full-keyboard picker, no search — because
 * users primarily need quick reactions (❤️ 😂 🔥 …) not exotic glyphs.
 */

const EMOJIS = [
  // Row 1: reactions & sentiment
  "❤️", "😂", "🔥", "😍", "😭", "👏", "🙌", "😱", "🥰", "🤣",
  // Row 2: emotions
  "😊", "😉", "😅", "😎", "🤔", "😴", "🙄", "😤", "😡", "🤯",
  // Row 3: hand & thumbs
  "👍", "👎", "🤝", "✌️", "🙏", "💪", "👀", "🤦", "🤷", "💅",
  // Row 4: symbols
  "💯", "✨", "⚡", "💥", "❓", "❗", "🚀", "💔", "🌹", "🎉",
];

type Props = {
  onPick: (emoji: string) => void;
  /** When true the picker starts open (rare; usually toggled by the icon). */
  initiallyOpen?: boolean;
  /** Optional test-ID prefix, so multiple pickers on the same screen can be
   *  addressed independently by e2e tests. */
  testIDPrefix?: string;
};

export default function EmojiPickerBar({ onPick, initiallyOpen = false, testIDPrefix = "emoji" }: Props) {
  const [open, setOpen] = useState(initiallyOpen);

  const pick = useCallback((e: string) => {
    onPick(e);
  }, [onPick]);

  return (
    <View style={styles.wrap}>
      <Pressable
        onPress={() => setOpen((o) => !o)}
        hitSlop={8}
        testID={`${testIDPrefix}-toggle`}
        style={[styles.toggleBtn, open && styles.toggleBtnOn]}
      >
        <Ionicons name={open ? "close" : "happy-outline"} size={18} color={colors.onSurface} />
        <Text style={styles.toggleTxt}>{open ? "EMOJI ×" : "EMOJI"}</Text>
      </Pressable>

      {open && (
        <View style={styles.stripWrap} testID={`${testIDPrefix}-strip`}>
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.stripInner}
          >
            {EMOJIS.map((e) => (
              <Pressable
                key={e}
                onPress={() => pick(e)}
                testID={`${testIDPrefix}-${e}`}
                style={styles.emojiBtn}
              >
                <Text style={styles.emojiTxt}>{e}</Text>
              </Pressable>
            ))}
          </ScrollView>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { alignSelf: "stretch" },
  toggleBtn: {
    alignSelf: "flex-start",
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    borderWidth: 2,
    borderColor: colors.border,
    paddingHorizontal: spacing.sm,
    paddingVertical: 4,
    backgroundColor: colors.surfaceSecondary,
  },
  toggleBtnOn: { backgroundColor: colors.surfaceTertiary },
  toggleTxt: {
    fontSize: font.sizes.xs,
    letterSpacing: 1,
    color: colors.onSurface,
    fontWeight: "500",
  },
  stripWrap: {
    marginTop: 6,
    borderWidth: 2,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  stripInner: { paddingHorizontal: 4, paddingVertical: 6, gap: 2 },
  emojiBtn: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: "center",
    justifyContent: "center",
    marginHorizontal: 2,
  },
  emojiTxt: { fontSize: 26 },
});
