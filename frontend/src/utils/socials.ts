/**
 * Shared type + constants for user social links.
 *
 * Extracted from profile.tsx so both the Profile screen and the extracted
 * `EditProfileModal` component can share the exact same shape without
 * cross-importing (which would create a circular dep between profile.tsx
 * and its own modal).
 */
export type Socials = {
  instagram: string;
  tiktok: string;
  twitter: string;
  youtube: string;
  website: string;
};

export const EMPTY_SOCIALS: Socials = {
  instagram: "",
  tiktok: "",
  twitter: "",
  youtube: "",
  website: "",
};

export const SOCIAL_KEYS: (keyof Socials)[] = [
  "instagram",
  "tiktok",
  "twitter",
  "youtube",
  "website",
];

export const SOCIAL_LABELS: Record<keyof Socials, string> = {
  instagram: "Instagram",
  tiktok: "TikTok",
  twitter: "X (Twitter)",
  youtube: "YouTube",
  website: "Sito web",
};
