import { Tabs } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { colors } from "@/src/theme";

export default function TabsLayout() {
  const insets = useSafeAreaInsets();
  const bottomPad = Math.max(insets.bottom, 12);

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.brandPrimary,
        tabBarInactiveTintColor: colors.muted,
        tabBarStyle: {
          backgroundColor: colors.surfaceInverse,
          borderTopWidth: 2,
          borderTopColor: colors.border,
          height: 60 + bottomPad,
          paddingTop: 8,
          paddingBottom: bottomPad,
        },
        tabBarLabelStyle: { fontSize: 11, letterSpacing: 1 },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "FAIDE",
          tabBarIcon: ({ color, size }) => <Ionicons name="flame" color={color} size={size} />,
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          title: "PROFILO",
          tabBarIcon: ({ color, size }) => <Ionicons name="person" color={color} size={size} />,
        }}
      />
    </Tabs>
  );
}
