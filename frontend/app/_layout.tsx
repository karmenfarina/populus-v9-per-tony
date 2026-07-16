import { Stack, useRouter } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { useEffect } from "react";
import { LogBox, Platform } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";
import * as Notifications from "expo-notifications";
import * as Linking from "expo-linking";

import { useIconFonts } from "@/src/hooks/use-icon-fonts";
import { AuthProvider } from "@/src/auth/AuthContext";
import { UIPrefsProvider } from "@/src/ui/UIPrefs";
import { NotificationsProvider } from "@/src/notifications/NotificationsContext";
import { MessagingProvider } from "@/src/messaging/MessagingContext";

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

  useEffect(() => {
    if (loaded || error) {
      SplashScreen.hideAsync();
    }
  }, [loaded, error]);

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
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <AuthProvider>
          <UIPrefsProvider>
            <NotificationsProvider>
              <MessagingProvider>
                <Stack screenOptions={{ headerShown: false, animation: "fade" }} />
              </MessagingProvider>
            </NotificationsProvider>
          </UIPrefsProvider>
        </AuthProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
