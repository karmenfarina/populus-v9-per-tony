import { useCallback } from "react";
import { Platform, BackHandler } from "react-native";
import {
  useFocusEffect,
  useLocalSearchParams,
  usePathname,
  useRouter,
} from "expo-router";
import { navStack } from "./navStack";

/**
 * Universal "back" behaviour for secondary/detail screens (chat, feud
 * detail, user profile, circle, notification detail, …).
 *
 * Why a custom stack? Expo Router's built-in `router.back()` is
 * unreliable inside a Tabs layout on web: `router.push` to nested
 * routes doesn't grow the browser history in a way that lets `back()`
 * walk one screen at a time — the first press collapses back to `/`.
 *
 * How the stack works:
 *   1. Every screen using `useSmartBack` pushes its own `pathname`
 *      to `navStack` on focus (deduped against the current top so
 *      re-focus events don't duplicate entries).
 *   2. `goBack()` pops the current entry and navigates to the new top
 *      — preserving the FULL chain of visited screens, one press per
 *      screen.
 *   3. When the stack is empty we honour the caller-supplied
 *      `?from=/path` param (useful on cold-start / deep-links).
 *   4. Final fallback is the `fallback` argument, typically the tab
 *      that logically owns the screen.
 *
 * The hook also wires the Android hardware-back button so it obeys
 * the same rules.
 */
export function useSmartBack(fallback: string = "/") {
  const router = useRouter();
  const pathname = usePathname();
  const { from } = useLocalSearchParams<{ from?: string }>();

  const goBack = useCallback(() => {
    // 1. Custom stack: pop current, jump to whatever's underneath. This
    //    walks the FULL chain of pushed detail screens even on web,
    //    where router.back() alone is unreliable.
    const prev = navStack.popAndPeek();
    if (prev && prev !== pathname) {
      router.replace(prev as any);
      return;
    }
    // 2. Explicit caller hint (cold-start / deep-link).
    if (typeof from === "string" && from.startsWith("/")) {
      router.replace(from as any);
      return;
    }
    // 3. Native router.back() as a last-ditch effort when we still
    //    have OS history (mostly relevant on native).
    if (router.canGoBack()) {
      router.back();
      return;
    }
    // 4. Home tab / caller-defined fallback.
    router.replace(fallback as any);
  }, [router, from, fallback, pathname]);

  useFocusEffect(
    useCallback(() => {
      // Record this screen in our stack so subsequent nested pushes can
      // walk back through it. Deduped by pathname so re-focus events
      // don't grow the stack.
      if (pathname) navStack.push(pathname);

      if (Platform.OS !== "android") return;
      const sub = BackHandler.addEventListener("hardwareBackPress", () => {
        goBack();
        return true;
      });
      return () => sub.remove();
    }, [pathname, goBack]),
  );

  return goBack;
}
