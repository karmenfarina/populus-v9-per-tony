import { Tabs } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { View, Text, StyleSheet } from "react-native";
import { colors } from "@/src/theme";
import { useNotifications } from "@/src/notifications/NotificationsContext";
import { useMessaging } from "@/src/messaging/MessagingContext";

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
          tabBarIcon: ({ color, size }) => <Ionicons name="scale" color={color} size={size} />,
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
