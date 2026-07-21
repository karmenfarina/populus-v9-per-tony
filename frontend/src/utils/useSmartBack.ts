import { useCallback } from "react";
import { Platform, BackHandler } from "react-native";
import { useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";

/**
 * Standard "back" behaviour for secondary screens (chat, feud detail,
 * user profile, circle, notification detail, …).
 *
 * Priority:
 *   1. `?from=/some/path` query param — the origin route explicitly
 *      declared by the caller. Preferred because it survives web
 *      history quirks and deep-links.
 *   2. `router.back()` when a navigation stack is present.
 *   3. `fallback` (typically the tab that logically owns the screen,
 *      e.g. "/messages" for chats or "/" for feeds).
 *
 * The hook ALSO wires the Android hardware-back button so it obeys the
 * exact same rules — no more "back from chat lands on Home" issues.
 */
export function useSmartBack(fallback: string = "/") {
  const router = useRouter();
  const { from } = useLocalSearchParams<{ from?: string }>();

  const goBack = useCallback(() => {
    // 1. Preserve the full navigation chain — pop the stack whenever
    //    the router has history. Every intermediate `router.push` builds
    //    that history, so a user going Home → Profile → Circle → Chat
    //    can walk back all the way one screen at a time.
    if (router.canGoBack()) {
      router.back();
      return;
    }
    // 2. Cold-start / deep link fallback: the caller declared where it
    //    wants us to land via `?from=/some/path`.
    if (typeof from === "string" && from.startsWith("/")) {
      router.replace(from as any);
      return;
    }
    // 3. Last-resort default (the tab that logically owns this screen).
    router.replace(fallback as any);
  }, [router, from, fallback]);

  useFocusEffect(
    useCallback(() => {
      if (Platform.OS !== "android") return;
      const sub = BackHandler.addEventListener("hardwareBackPress", () => {
        goBack();
        return true;
      });
      return () => sub.remove();
    }, [goBack]),
  );

  return goBack;
}
