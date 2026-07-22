import React, { useEffect, useRef } from "react";
import { Animated, StyleSheet, View, Easing } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { colors } from "@/src/theme";

/**
 * Instagram-style animated ring used by the home StoriesBar and the
 * fullscreen viewer.
 *
 * • `variant="unseen"` → dynamic rotating gradient border (pink → red
 *   → orange, the same triad Instagram uses for fresh stories).
 * • `variant="seen"` → static faded outline, no animation.
 * • `variant="mine"` → static neutral outline (matches the previous
 *   look for "your own" ring).
 *
 * The rotation is driven by a native-driver Animated loop so it stays
 * smooth even on lower-end devices. The gradient is captured inside a
 * masked circular view — the inner "hole" (padding equal to the ring
 * width) reveals the avatar beneath.
 */

export type AnimatedStoryRingProps = {
  size: number;
  ringWidth: number;
  variant: "unseen" | "seen" | "mine";
  /**
   * When true the ring runs its rotating gradient animation to signal
   * that a new story is still LOADING (typical use: while the stories
   * feed is being fetched). Once the caller flips this back to false
   * the ring transitions to a static gradient — matches Instagram's
   * "loading → loaded" distinction. Only applies to `variant='unseen'`.
   * Defaults to `false` (static gradient) so pages that don't opt in
   * keep the previous "always static" look.
   */
  loading?: boolean;
  children: React.ReactNode;
};

export default function AnimatedStoryRing({
  size,
  ringWidth,
  variant,
  loading = false,
  children,
}: AnimatedStoryRingProps) {
  const spin = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (variant !== "unseen" || !loading) {
      // Reset rotation so the transition from "loading" to "loaded"
      // snaps the gradient back to its zero-degree resting state
      // instead of freezing mid-turn.
      spin.stopAnimation();
      spin.setValue(0);
      return;
    }
    // Continuous slow rotation while loading — 1.4s per full turn is
    // fast enough to read as a spinner but not distracting.
    const loop = Animated.loop(
      Animated.timing(spin, {
        toValue: 1,
        duration: 1400,
        easing: Easing.linear,
        useNativeDriver: true,
      }),
    );
    loop.start();
    return () => loop.stop();
  }, [variant, loading, spin]);

  const rotate = spin.interpolate({
    inputRange: [0, 1],
    outputRange: ["0deg", "360deg"],
  });

  // Radii for the container and the hole (inner mask that reveals the
  // avatar) — computed once from props so the ring stays perfectly
  // circular at any size.
  const outerR = size / 2;
  const innerSize = size - ringWidth * 2;

  if (variant === "unseen") {
    return (
      <View style={[styles.wrap, { width: size, height: size, borderRadius: outerR }]}>
        <Animated.View
          pointerEvents="none"
          style={[
            StyleSheet.absoluteFillObject,
            { borderRadius: outerR, transform: [{ rotate }] },
          ]}
        >
          <LinearGradient
            colors={["#FF3040", "#F97316", "#F59E0B", "#FF3040"]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={[
              StyleSheet.absoluteFillObject,
              { borderRadius: outerR },
            ]}
            pointerEvents="none"
          />
        </Animated.View>
        {/* Inner mask — reveals the avatar photo. */}
        <View
          pointerEvents="none"
          style={{
            width: innerSize,
            height: innerSize,
            borderRadius: innerSize / 2,
            backgroundColor: colors.surface,
            alignItems: "center",
            justifyContent: "center",
            overflow: "hidden",
          }}
        >
          {children}
        </View>
      </View>
    );
  }

  const outerColor =
    variant === "mine" ? colors.border : colors.muted + "55";
  return (
    <View
      style={[
        styles.wrap,
        {
          width: size,
          height: size,
          borderRadius: outerR,
          backgroundColor: outerColor,
        },
      ]}
    >
      <View
        pointerEvents="none"
        style={{
          width: innerSize,
          height: innerSize,
          borderRadius: innerSize / 2,
          backgroundColor: colors.surface,
          alignItems: "center",
          justifyContent: "center",
          overflow: "hidden",
        }}
      >
        {children}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    alignItems: "center",
    justifyContent: "center",
    overflow: "hidden",
  },
});
