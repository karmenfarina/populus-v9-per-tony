import React from "react";
import { Text, TextStyle, StyleProp } from "react-native";
import { useRouter } from "expo-router";
import { splitMentions } from "@/src/utils/mentions";
import type { Mention } from "@/src/api";

/**
 * Render a comment/reply text with tappable `@nickname` highlights.
 * Any `@nickname` matched by `MENTION_REGEX` is rendered in the
 * accent color; if the nickname is present in the `mentions` array
 * (i.e. resolved server-side to an actual user), the span is also
 * tappable — pressing it navigates to that user's public profile.
 * Non-resolved handles fall back to plain highlighted text so the
 * user isn't misled by a broken link.
 */
export default function MentionText({
  text,
  mentions,
  style,
  accentColor,
}: {
  text: string;
  mentions?: Mention[] | null;
  style?: StyleProp<TextStyle>;
  accentColor: string;
}) {
  const router = useRouter();
  const byNick = new Map<string, string>();
  (mentions || []).forEach((m) => {
    if (m?.nickname && m?.user_id) byNick.set(m.nickname.toLowerCase(), m.user_id);
  });
  const segments = splitMentions(text || "");
  return (
    <Text style={style}>
      {segments.map((seg, i) => {
        if (seg.type === "text") return <Text key={i}>{seg.value}</Text>;
        const uid = byNick.get(seg.nickname);
        if (uid) {
          return (
            <Text
              key={i}
              onPress={() => router.push(`/user/${uid}` as any)}
              style={{ color: accentColor, fontWeight: "700" }}
              suppressHighlighting
              testID={`mention-${seg.nickname}`}
            >
              {seg.value}
            </Text>
          );
        }
        return (
          <Text key={i} style={{ color: accentColor, fontWeight: "700" }}>
            {seg.value}
          </Text>
        );
      })}
    </Text>
  );
}
