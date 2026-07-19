import { useCallback } from "react";
import { View, Text, StyleSheet, Pressable, ScrollView } from "react-native";
import { colors } from "@/src/theme";

/**
 * Always-visible emoji strip for the comment / reply composer.
 *
 * Renders a horizontal, scrollable row of the most common emojis directly
 * under the input — no toggle button, no drawer. Tapping an emoji fires the
 * `onPick` callback so the parent can append it to the text field.
 */

const EMOJIS = [
  "❤️", "😂", "🔥", "😍", "😭", "👏", "🙌", "😱", "🥰", "🤣",
  "😊", "😉", "😅", "😎", "🤔", "🙄", "😤", "😡", "🤯",
  "👍", "👎", "🤝", "✌️", "🙏", "💪", "👀", "🤦", "🤷",
  "💯", "✨", "⚡", "💥", "❓", "❗", "🚀", "💔", "🌹", "🎉",
];

type Props = {
  onPick: (emoji: string) => void;
  testIDPrefix?: string;
};

export default function EmojiPickerBar({ onPick, testIDPrefix = "emoji" }: Props) {
  const pick = useCallback((e: string) => { onPick(e); }, [onPick]);
  return (
    <View style={styles.wrap} testID={`${testIDPrefix}-strip`}>
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
  );
}

const styles = StyleSheet.create({
  wrap: {
    borderWidth: 2,
    borderColor: colors.border,
    backgroundColor: colors.surfaceSecondary,
    flex: 1,
  },
  stripInner: { paddingHorizontal: 4, paddingVertical: 4, alignItems: "center" },
  emojiBtn: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: "center",
    justifyContent: "center",
    marginHorizontal: 2,
  },
  emojiTxt: { fontSize: 22, lineHeight: 26 },
});
