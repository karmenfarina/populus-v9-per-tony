import { Tabs, usePathname } from "expo-router";
import { Ionicons, MaterialCommunityIcons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { View, Text, StyleSheet } from "react-native";
import { useEffect } from "react";
import { colors } from "@/src/theme";
import { useNotifications } from "@/src/notifications/NotificationsContext";
import { useMessaging } from "@/src/messaging/MessagingContext";
import { navStack } from "@/src/utils/navStack";
import { useAuth } from "@/src/auth/AuthContext";

/**
 * Top-level tab paths that should reset the nav stack when reached.
 * Landing on any of these means the user "returned home" for that tab,
 * so previously pushed detail-screen entries must not leak into the
 * new browsing session (which caused the "back accidentally opens
 * Cerchia" bug).
 */
const TAB_ROOTS = new Set(["/", "/top", "/messages", "/notifications", "/circle/find", "/profile"]);

function NotifIcon({ color, size }: { color: string; size: number }) {
  const { unread } = useNotifications();
  return (
    <View>
      <Ionicons name="notifications" color={color} size={size} />
      {unread > 0 && (
        <View style={styles.badge} testID="tab-notif-badge">
          <Text style={styles.badgeTxt}>{unread > 99 ? "99+" : String(unread)}</Text>
        </View>
      )}
    </View>
  );
}

function MessagesIcon({ color, size }: { color: string; size: number }) {
  const { unread } = useMessaging();
  return (
    <View>
      <Ionicons name="chatbubbles" color={color} size={size} />
      {unread > 0 && (
        <View style={styles.badge} testID="tab-messages-badge">
          <Text style={styles.badgeTxt}>{unread > 99 ? "99+" : String(unread)}</Text>
        </View>
      )}
    </View>
  );
}

export default function TabsLayout() {
  const insets = useSafeAreaInsets();
  const bottomPad = Math.max(insets.bottom, 12);
  const pathname = usePathname();
  const { user } = useAuth();
  // The "Cerca amici" (find friends) tab is intentionally gated to
  // NON-anonymous accounts. Anonymous users can't be discovered nor
  // discover others via the search endpoint (backend enforces this),
  // so exposing the tab would be dead UI. Hiding it here keeps the
  // tab bar consistent with the actual capabilities of the account.
  const isAnon = user?.is_anonymous === true || user?.auth_provider === 'anonymous';

  // Whenever navigation lands on a top-level tab root, wipe the manual
  // back-stack. Without this, stale entries from a previous detail-
  // screen chain (e.g., an earlier trip through /circle/{me}) would
  // survive a tab switch and the very next back-press from a new
  // detail screen would send the user back into that stale route.
  useEffect(() => {
    if (pathname && TAB_ROOTS.has(pathname)) {
      navStack.clear();
    }
  }, [pathname]);

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.brandPrimary,
        tabBarInactiveTintColor: colors.muted,
        // Icon-only tab bar: labels are permanently hidden per product decision.
        // Any new tab added in the future will automatically follow the same rule.
        tabBarShowLabel: false,
        tabBarStyle: {
          backgroundColor: colors.surfaceInverse,
          borderTopWidth: 2,
          borderTopColor: colors.border,
          height: 56 + bottomPad,
          paddingTop: 8,
          paddingBottom: bottomPad,
        },
        tabBarIconStyle: { marginTop: 2 },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "FAIDE",
          tabBarIcon: ({ color, size }) => (
            // MaterialCommunityIcons render smaller than Ionicons at the same
            // nominal size, so bump the scale-balance glyph by ~18 % to match
            // the visual weight of the other tab icons (bookmark, chatbubbles, …).
            <MaterialCommunityIcons name="scale-balance" color={color} size={Math.round(size * 1.18)} />
          ),
        }}
      />
      <Tabs.Screen
        name="top"
        options={{
          title: "TOP",
          tabBarIcon: ({ color, size }) => <Ionicons name="bookmark" color={color} size={size} />,
        }}
      />
      <Tabs.Screen
        name="messages/index"
        options={{
          title: "MESSAGGI",
          tabBarIcon: ({ color, size }) => <MessagesIcon color={color} size={size} />,
        }}
      />
      <Tabs.Screen
        name="notifications"
        options={{
          title: "NOTIFICHE",
          tabBarIcon: ({ color, size }) => <NotifIcon color={color} size={size} />,
        }}
      />
      {/* Dedicated "Cerca amici" tab — sits immediately to the LEFT of
          the profile tab so it's reachable with a thumb, without hiding
          the profile at the far edge. Uses the person-add glyph so the
          role is unambiguous. */}
      <Tabs.Screen
        name="circle/find"
        options={{
          title: "CERCA AMICI",
          // For anonymous users the whole screen is unavailable — hide
          // the tab entry completely by setting `href: null` so it
          // doesn't render an icon in the tab bar at all.
          href: isAnon ? null : undefined,
          tabBarIcon: ({ color, size }) => <Ionicons name="person-add" color={color} size={size} />,
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          title: "PROFILO",
          tabBarIcon: ({ color, size }) => <Ionicons name="person" color={color} size={size} />,
        }}
      />
      {/* Nested screens that must render inside the tab bar layout but should
          not appear as their own tab entries. Setting `href: null` hides the
          tab icon while still keeping the tab bar visible when navigating to
          these routes. */}
      <Tabs.Screen name="feud/[id]" options={{ href: null }} />
      <Tabs.Screen name="user/[id]" options={{ href: null }} />
      <Tabs.Screen name="hashtag/[key]" options={{ href: null }} />
      <Tabs.Screen name="messages/[userId]" options={{ href: null }} />
      <Tabs.Screen name="circle/[userId]" options={{ href: null }} />
      <Tabs.Screen name="archive" options={{ href: null }} />
      <Tabs.Screen name="support" options={{ href: null }} />
      <Tabs.Screen name="admin" options={{ href: null }} />
    </Tabs>
  );
}

const styles = StyleSheet.create({
  badge: {
    position: "absolute",
    top: -4,
    right: -10,
    minWidth: 16,
    height: 16,
    paddingHorizontal: 4,
    borderRadius: 8,
    backgroundColor: colors.brandPrimary,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: colors.surfaceInverse,
  },
  badgeTxt: { color: colors.onBrandPrimary, fontSize: 9, fontWeight: "700" },
});
