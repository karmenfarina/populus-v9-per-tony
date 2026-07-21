import { useCallback } from "react";
import { Platform, BackHandler } from "react-native";
import { useFocusEffect } from "expo-router";

/**
 * Wire the Android hardware back button to the exact same callback
 * used by the screen's own on-screen back button.
 *
 * This is the SIMPLE variant, intentionally decoupled from any custom
 * navigation-stack logic. It exists specifically for detail screens
 * that already own a `goBack` closure (custom or from `useSmartBack`)
 * and just want the hardware back to behave identically.
 *
 * Rules:
 *  1. Only active while the screen is focused (via `useFocusEffect`).
 *  2. Only registers on Android; iOS ignores it (no hardware back);
 *     web falls through to browser history (also ignored here).
 *  3. Always returns `true` from the native handler so Expo Router's
 *     default fallback (which may exit the app or hop to an unrelated
 *     route) never gets a chance to run.
 *
 * IMPORTANT: DO NOT modify the caller's on-screen back button logic —
 * this hook is intentionally a passive listener that just delegates
 * to the same callback.
 *
 * Usage:
 *   const onBack = useCallback(() => router.replace("/profile"), [router]);
 *   useHardwareBack(onBack);
 *   // then: <Pressable onPress={onBack}> … </Pressable>
 */
export function useHardwareBack(onBack: () => void) {
  useFocusEffect(
    useCallback(() => {
      if (Platform.OS !== "android") return;
      const sub = BackHandler.addEventListener("hardwareBackPress", () => {
        try {
          onBack();
        } catch {
          // Swallow — worst case we return true and the OS does
          // nothing; better than crashing the whole app on back-press.
        }
        return true;
      });
      return () => {
        try { sub.remove(); } catch { /* noop */ }
      };
    }, [onBack]),
  );
}
