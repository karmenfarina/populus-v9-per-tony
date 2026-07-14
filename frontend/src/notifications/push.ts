import { Platform } from "react-native";
import * as Notifications from "expo-notifications";
import { api } from "@/src/api";

/**
 * Register the device with Emergent Push (SuprSend relay) so the backend can
 * send mobile notifications to this user. Safe on web (no-op), safe if the
 * user denies the permission (silent skip), and safe on repeated calls
 * (tokens rotate — the backend upserts).
 *
 * Follows the Emergent playbook:
 *  - permissions BEFORE token
 *  - `getDevicePushTokenAsync` (native FCM/APNs), NOT the Expo push API
 *  - swallow failures — push is best-effort, never blocking
 */
export async function registerForPush(): Promise<void> {
  if (Platform.OS === "web") return;
  try {
    const perm = await Notifications.getPermissionsAsync();
    let status = perm.status;
    if (status !== "granted" && perm.canAskAgain) {
      const req = await Notifications.requestPermissionsAsync();
      status = req.status;
    }
    if (status !== "granted") return;
    const tokenResp = await Notifications.getDevicePushTokenAsync();
    await api.registerPush(Platform.OS, tokenResp.data);
  } catch {
    // silent — push is best-effort
  }
}
