import { Platform } from "react-native";
import * as FileSystem from "expo-file-system/legacy";
import type { UserPhoto } from "@/src/api";

/**
 * Module-level cache mapping (photo_id + short data hash) → local file URI.
 *
 * Rendering multiple `data:image/jpeg;base64,<huge>` sources back-to-back in
 * the same view can push RN's Image loader into an out-of-memory state on
 * older devices — the error the user sees as a red-screen after saving many
 * photos. Writing each photo to disk once and referencing it by a file:// URI
 * lets the OS stream & cache the bitmap without holding the full base64 in
 * memory.
 */
const photoUriCache: Map<string, string> = new Map();

function _photoHash(data: string): string {
  // Cheap fingerprint over the payload so a re-crop with different content
  // busts the cache automatically.
  let h = 0;
  const step = Math.max(1, Math.floor(data.length / 128));
  for (let i = 0; i < data.length; i += step) {
    h = (h * 31 + data.charCodeAt(i)) | 0;
  }
  return `${data.length}_${(h >>> 0).toString(36)}`;
}

export async function resolvePhotoUri(photo: UserPhoto): Promise<string> {
  const key = `${photo.photo_id}_${_photoHash(photo.data)}`;
  const hit = photoUriCache.get(key);
  if (hit) return hit;
  if (Platform.OS === "web") {
    const uri = `data:image/jpeg;base64,${photo.data}`;
    photoUriCache.set(key, uri);
    return uri;
  }
  try {
    const dir = (FileSystem as any).cacheDirectory || (FileSystem as any).documentDirectory;
    const safe = photo.photo_id.replace(/[^a-zA-Z0-9_]/g, "_");
    const uri = `${dir}profphoto_${safe}.jpg`;
    await FileSystem.writeAsStringAsync(uri, photo.data, {
      encoding: FileSystem.EncodingType.Base64,
    });
    photoUriCache.set(key, uri);
    return uri;
  } catch {
    // Fallback to data URI if FS write fails — still shows the picture.
    const uri = `data:image/jpeg;base64,${photo.data}`;
    photoUriCache.set(key, uri);
    return uri;
  }
}
