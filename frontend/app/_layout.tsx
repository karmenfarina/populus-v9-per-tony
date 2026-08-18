import { Stack, useRouter } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { useEffect } from "react";
import { LogBox, Platform } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";
import * as Notifications from "expo-notifications";
import * as Linking from "expo-linking";
import { StatusBar } from "expo-status-bar";
import * as NavigationBar from "expo-navigation-bar";
import * as SystemUI from "expo-system-ui";

import { useIconFonts } from "@/src/hooks/use-icon-fonts";
import { AuthProvider } from "@/src/auth/AuthContext";
import { UIPrefsProvider } from "@/src/ui/UIPrefs";
import { NotificationsProvider } from "@/src/notifications/NotificationsContext";
import { MessagingProvider } from "@/src/messaging/MessagingContext";
import { StoryUploadProvider } from "@/src/stories/StoryUploadContext";
import { reviewManager } from "@/src/utils/reviewManager";
import NetworkBanner from "@/src/components/NetworkBanner";
import Constants from "expo-constants";

LogBox.ignoreAllLogs(true);
SplashScreen.preventAutoHideAsync();

// Foreground notification handler — module-scope per playbook. Guarded on web
// because expo-notifications APIs crash on react-native-web.
if (Platform.OS !== "web") {
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowAlert: true,
      shouldPlaySound: true,
      shouldSetBadge: false,
      shouldShowBanner: true,
      shouldShowList: true,
    }),
  });
}

// Android notification channel — must exist BEFORE any push arrives.
if (Platform.OS === "android") {
  Notifications.setNotificationChannelAsync("default", {
    name: "Populus",
    importance: Notifications.AndroidImportance.MAX,
    sound: "default",
  });
}

export default function RootLayout() {
  const [loaded, error] = useIconFonts();
  const router = useRouter();

  // Force dark system UI (status bar + Android navigation bar) regardless of
  // the device's light/dark theme setting. Populus is a dark-only app.
  // NOTE: on Expo Go we're limited to runtime APIs — the config-plugin
  // values (`enforceContrast: false`, `windowLightNavigationBar: false`)
  // only take effect once a native build is generated. In Expo Go we still
  // do our best to make the icons white via repeated imperative calls.
  useEffect(() => {
    if (Platform.OS === "web") return;
    // Root window background — prevents white flashes during transitions
    // and keeps the area behind the status/navigation bars black.
    SystemUI.setBackgroundColorAsync("#000000").catch(() => {});

    if (Platform.OS === "android") {
      const applyDarkSystemBars = () => {
        // Two complementary calls: setStyle('dark') = new declarative API
        // that maps to a "dark bar" (i.e. LIGHT/white content). We also
        // call setButtonStyleAsync('light') as an older imperative fallback
        // — some Expo Go builds honour one but not the other.
        try { NavigationBar.setStyle?.("dark"); } catch { /* noop */ }
        NavigationBar.setButtonStyleAsync("light").catch(() => {});
        // In edge-to-edge mode setBackgroundColorAsync is a no-op (OS
        // manages the bg) but on older Android it does apply a solid
        // black bar — set it defensively.
        NavigationBar.setBackgroundColorAsync("#000000").catch(() => {});
      };
      // First pass immediately, then again after Expo Go finishes its own
      // theme initialisation (~400ms) — otherwise it can override us.
      applyDarkSystemBars();
      const t1 = setTimeout(applyDarkSystemBars, 300);
      const t2 = setTimeout(applyDarkSystemBars, 1200);
      return () => { clearTimeout(t1); clearTimeout(t2); };
    }
  }, []);

  useEffect(() => {
    if (loaded || error) {
      SplashScreen.hideAsync();
    }
  }, [loaded, error]);

  // Session tracker for the native store-review request. Records a
  // debounced "session opened" event at cold start so the review
  // prompt gate ("≥3 sessions") reflects real usage. Web/Expo Go
  // are a no-op inside reviewManager itself.
  useEffect(() => {
    reviewManager.markSessionOpen().catch(() => { /* noop */ });
  }, []);

  // ── Google Mobile Ads (AdMob) SDK initialisation ──
  // Fires once at app cold start. Skipped on web (no SDK there) and
  // in Expo Go (native module not linked → would throw). Wrapped in
  // a try/catch so a bad AdMob config never crashes the entire app.
  useEffect(() => {
    if (Platform.OS === "web") return;
    if (Constants.appOwnership === "expo") return; // Expo Go — skip
    (async () => {
      try {
        // Uses the platform-aware wrapper — the web build gets a no-op
        // stub, so this whole init sequence collapses to a Promise
        // that resolves immediately on web (and is short-circuited
        // above anyway by the Platform.OS check).
        const { mobileAds } = await import("@/src/ads/mobileAds");
        await mobileAds().initialize();
      } catch (e) {
        // AdMob init failure is non-fatal — the app should keep
        // running, just without ads.
        console.warn("[AdMob] initialize failed", e);
      }
    })();
  }, []);

  // Deep-link routing when the user taps a push notification.
  useEffect(() => {
    if (Platform.OS === "web") return;
    // Warm tap (app in background/foreground).
    const tapSub = Notifications.addNotificationResponseReceivedListener((response) => {
      const data: any = response.notification.request.content.data || {};
      const url: string | undefined = data.deeplink || data.action_url;
      if (!url) return;
      if (url.startsWith("http")) Linking.openURL(url);
      else router.push(url as any);
    });
    // Cold-start tap (app was killed and launched by the notification).
    Notifications.getLastNotificationResponseAsync().then((response) => {
      if (!response) return;
      const data: any = response.notification.request.content.data || {};
      const url: string | undefined = data.deeplink || data.action_url;
      if (!url) return;
      if (url.startsWith("http")) Linking.openURL(url);
      else router.push(url as any);
    });
    return () => { tapSub.remove(); };
  }, [router]);

  if (!loaded && !error) return null;

  return (
    <GestureHandlerRootView style={{ flex: 1, backgroundColor: "#000000" }}>
      <SafeAreaProvider>
        <AuthProvider>
          <UIPrefsProvider>
            <NotificationsProvider>
              <MessagingProvider>
                <StoryUploadProvider>
                  {/* Force light status-bar icons on a transparent bg (the
                      underlying screen is always dark). translucent=true on
                      Android lets the screen draw behind the status bar. */}
                  <StatusBar style="light" translucent backgroundColor="transparent" />
                  <Stack screenOptions={{ headerShown: false, animation: "fade", contentStyle: { backgroundColor: "#000000" } }} />
                  {/* Banner globale che appare in caso di rete lenta/assente. Non
                      dipende da NetInfo (che su alcuni provider mobili è ottimista):
                      si basa sui retry effettivi eseguiti dal wrapper API. */}
                  <NetworkBanner topInset={Platform.OS === "ios" ? 44 : 8} />
                </StoryUploadProvider>
              </MessagingProvider>
            </NotificationsProvider>
          </UIPrefsProvider>
        </AuthProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
