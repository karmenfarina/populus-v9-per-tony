import React, { useEffect, useRef } from "react";
import { Animated, Pressable, StyleSheet, ViewStyle, StyleProp } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { colors, radius } from "@/src/theme";

/**
 * Reusable floating "back to top / bottom" pill button.
 *
 * Usage pattern for a normal (top-down) list:
 *   1. Track scrollY via onScroll from your ScrollView/FlatList.
 *   2. Set `visible = scrollY > 400`.
 *   3. Render <ScrollToTopButton visible={visible} onPress={scrollToTop} />
 *
 * For an inverted list (chat / messages) pass `direction="down"` and call
 * `scrollToOffset({ offset: 0, animated: true })` on your list to jump to
 * the newest message.
 *
 * Positioning:
 * - Anchored bottom-right by default with `bottomOffset` (added on top of
 *   the safe-area inset) so it sits above the tab bar / composer.
 * - The button auto-fades in/out via a native driver Animated.Value.
 */
export type ScrollToTopButtonProps = {
  visible: boolean;
  onPress: () => void;
  /** "up" (default) shows an arrow-up icon; "down" shows arrow-down. */
  direction?: "up" | "down";
  /** Extra bottom offset in px on top of safe-area inset. */
  bottomOffset?: number;
  /** Optional right offset. */
  rightOffset?: number;
  /** Optional testID for automation. */
  testID?: string;
  /** Optional style override applied to the outer wrapper. */
  style?: StyleProp<ViewStyle>;
};

export function ScrollToTopButton({
  visible,
  onPress,
  direction = "up",
  bottomOffset = 20,
  rightOffset = 16,
  testID = "scroll-to-top-btn",
  style,
}: ScrollToTopButtonProps) {  const insets = useSafeAreaInsets();
  const opacity = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.timing(opacity, {
      toValue: visible ? 1 : 0,
      duration: 180,
      useNativeDriver: true,
    }).start();
  }, [visible, opacity]);

  return (
    <Animated.View
      pointerEvents={visible ? "auto" : "none"}
      style={[
        styles.wrapper,
        { bottom: insets.bottom + bottomOffset, right: rightOffset, opacity },
        style,
      ]}
    >
      <Pressable
        onPress={onPress}
        testID={testID}
        accessibilityRole="button"
        accessibilityLabel={direction === "up" ? "Torna all'inizio" : "Vai alla fine"}
        style={styles.btn}
        hitSlop={8}
      >
        <Ionicons
          name={direction === "up" ? "arrow-up" : "arrow-down"}
          size={22}
          color={colors.onBrandSecondary}
        />
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    position: "absolute",
    zIndex: 30,
    elevation: 6,
  },
  btn: {
    width: 44,
    height: 44,
    borderRadius: radius.pill,
    backgroundColor: colors.brandSecondary,
    alignItems: "center",
    justifyContent: "center",
    // Subtle shadow so the pill stands out from any list content
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.35,
    shadowRadius: 6,
  },
});
