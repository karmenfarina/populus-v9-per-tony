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
  children: React.ReactNode;
};

export default function AnimatedStoryRing({
  size,
  ringWidth,
  variant,
  children,
}: AnimatedStoryRingProps) {
  const spin = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (variant !== "unseen") return;
    // Continuous slow rotation — 3s per full turn gives that
    // "alive/loading" cadence Instagram uses without being distracting.
    // useNativeDriver:true keeps the animation off the JS thread.
    const loop = Animated.loop(
      Animated.timing(spin, {
        toValue: 1,
        duration: 3000,
        easing: Easing.linear,
        useNativeDriver: true,
      }),
    );
    loop.start();
    return () => loop.stop();
  }, [variant, spin]);

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
          />
        </Animated.View>
        {/* Inner mask — reveals the avatar photo. */}
        <View
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
