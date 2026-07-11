import { useCallback, useEffect, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, TextInput, ActivityIndicator,
  KeyboardAvoidingView, Platform, ImageBackground, Linking, Share,
} from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { useLocalSearchParams, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { api, ApiError, Comment, Feud, Reply, Sponsor } from "@/src/api";
import { colors, spacing, font, sideColor, onSideColor } from "@/src/theme";
import FeudMediaBlock from "@/src/components/FeudMediaBlock";

export default function FeudDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const [feud, setFeud] = useState<Feud | null>(null);
  const [sponsor, setSponsor] = useState<Sponsor | null>(null);
  const [sideA, setSideA] = useState<Comment[]>([]);
  const [sideB, setSideB] = useState<Comment[]>([]);
  const [loading, setLoading] = useState(true);
  const [voting, setVoting] = useState(false);
  const [commentText, setCommentText] = useState("");
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [gone, setGone] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, Reply[]>>({});
  const [replyingTo, setReplyingTo] = useState<string | null>(null);
  const [replyText, setReplyText] = useState("");

  const loadAll = useCallback(async () => {
    const f = await api.feud(id!);
    setFeud(f.feud);
    const [c, s] = await Promise.all([
      api.comments(id!),
      api.sponsors(f.feud.category).catch(() => ({ sponsors: [] })),
    ]);
    setSideA(c.side_a); setSideB(c.side_b);
    if (s.sponsors && s.sponsors.length > 0) setSponsor(s.sponsors[0]);
  }, [id]);

  useEffect(() => {
    (async () => {
      try { await loadAll(); }
      catch (e: any) {
        if (e instanceof ApiError && (e.status === 410 || e.status === 404)) {
          setGone(true);
        } else {
          setError(e?.message || "Errore");
        }
      }
      finally { setLoading(false); }
    })();
  }, [loadAll]);

  const vote = async (side: "A" | "B") => {
    if (!feud || feud.my_vote) return;
    setVoting(true); setError(null);
    try {
      try { await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium); } catch {}
      const res = await api.vote(feud.feud_id, side);
      setFeud(res.feud);
    } catch (e: any) { setError(e?.message || "Errore"); }
    finally { setVoting(false); }
  };

  const submitComment = async () => {
    if (!feud || !commentText.trim()) return;
    setPosting(true);
    try {
      await api.addComment(feud.feud_id, commentText.trim());
      setCommentText("");
      const c = await api.comments(feud.feud_id);
      setSideA(c.side_a); setSideB(c.side_b);
    } catch (e: any) { setError(e?.message || "Errore"); }
    finally { setPosting(false); }
  };

  const toggleReplies = async (commentId: string) => {
    if (expanded[commentId]) {
      const copy = { ...expanded }; delete copy[commentId]; setExpanded(copy);
      return;
    }
    const r = await api.replies(commentId);
    setExpanded((prev) => ({ ...prev, [commentId]: r.replies }));
  };

  const submitReply = async (commentId: string) => {
    if (!replyText.trim()) return;
    try {
      await api.addReply(commentId, replyText.trim());
      setReplyText(""); setReplyingTo(null);
      const r = await api.replies(commentId);
      setExpanded((prev) => ({ ...prev, [commentId]: r.replies }));
    } catch (e: any) { setError(e?.message || "Errore"); }
  };

  const onShare = async () => {
    if (!feud) return;
    try {
      const base = process.env.EXPO_PUBLIC_BACKEND_URL || "";
      const url = `${base}/api/share/${feud.feud_id}/html`;
      await Share.share({
        title: feud.title,
        message: `${feud.title}\n\nCon chi ti schieri? ${feud.party_a} vs ${feud.party_b}\n${url}`,
      });
    } catch {}
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]}>
        <View style={styles.centerFill}><ActivityIndicator size="large" color={colors.brandPrimary} /></View>
      </SafeAreaView>
    );
  }
  if (gone) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]} testID="feud-gone-screen">
        <View style={styles.topbar}>
          <Pressable onPress={() => router.back()} style={styles.backBtn} testID="gone-back-button">
            <Ionicons name="chevron-back" size={22} color={colors.onSurfaceInverse} />
            <Text style={styles.backTxt}>INDIETRO</Text>
          </Pressable>
        </View>
        <View style={styles.goneBox}>
          <Ionicons name="hourglass-outline" size={64} color={colors.brandSecondary} />
          <Text style={styles.goneTitle}>FAIDA SCADUTA</Text>
          <Text style={styles.goneMsg}>
            Errore: stai provando a visualizzare una faida che ha più di due settimane.
          </Text>
          <Text style={styles.goneHint}>
            Le faide vengono conservate per 14 giorni. Dopo questo periodo la discussione e i commenti vengono rimossi definitivamente.
          </Text>
          <Pressable onPress={() => router.replace("/")} style={styles.goneBtn} testID="gone-home-btn">
            <Text style={styles.goneBtnTxt}>TORNA ALLE FAIDE ATTIVE</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    );
  }
  if (!feud) return null;

  return (
    <SafeAreaView style={styles.safe} edges={["top"]} testID="feud-detail-screen">
      <View style={styles.topbar}>
        <Pressable onPress={() => router.back()} style={styles.backBtn} testID="back-button">
          <Ionicons name="chevron-back" size={22} color={colors.onSurfaceInverse} />
          <Text style={styles.backTxt}>INDIETRO</Text>
        </Pressable>
        <View style={{ flexDirection: "row", alignItems: "center", gap: spacing.sm }}>
          <Text style={styles.topCat}>{feud.category_label.toUpperCase()}</Text>
          <Pressable onPress={onShare} testID="share-button" style={styles.shareBtn}>
            <Ionicons name="share-outline" size={18} color={colors.brandSecondary} />
          </Pressable>
        </View>
      </View>

      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined} keyboardVerticalOffset={80}>
        <ScrollView contentContainerStyle={{ paddingBottom: spacing.xxxl }}>
          <ImageBackground source={{ uri: feud.image_url }} style={styles.hero}>
            <LinearGradient colors={["rgba(0,0,0,0)", "rgba(0,0,0,0.9)"]} style={StyleSheet.absoluteFill} />
            <View style={styles.heroContent}>
              <Text style={styles.title}>{feud.title}</Text>
            </View>
          </ImageBackground>

          <View style={styles.article}>
            <Text style={styles.sectionKicker}>LA FAIDA</Text>
            <Text style={styles.summary}>{feud.summary}</Text>
          </View>

          {feud.media && (
            <View style={styles.mediaSection} testID="feud-media-section">
              <Text style={styles.sectionKicker}>
                {feud.media.type === "youtube" || feud.media.type === "video" ? "VIDEO" : "IMMAGINE"}
              </Text>
              <FeudMediaBlock
                media={feud.media}
                fallbackImage={feud.image_url}
                title={feud.title}
              />
            </View>
          )}

          {feud.sources && feud.sources.length > 0 && (
            <View style={styles.sourcesBox} testID="sources-box">
              <Text style={styles.sectionKicker}>FONTI</Text>
              {feud.sources.map((s, i) => (
                <Pressable
                  key={i}
                  style={styles.sourceItem}
                  onPress={() => Linking.openURL(s.link)}
                  testID={`source-${i}`}
                >
                  <Text style={styles.sourceName}>{s.source.toUpperCase()}</Text>
                  <Text style={styles.sourceTitle} numberOfLines={2}>{s.title}</Text>
                  <Text style={styles.sourceLink}>{s.link.replace(/^https?:\/\//, '').slice(0, 45)}...  ›</Text>
                </Pressable>
              ))}
            </View>
          )}

          {sponsor && (
            <View style={styles.sponsorBox} testID="sponsor-banner">
              <Text style={styles.sponsorLabel}>SPONSOR · {sponsor.sponsor.toUpperCase()}</Text>
              <Text style={styles.sponsorHeadline}>{sponsor.headline}</Text>
              <Pressable style={styles.sponsorCta}>
                <Text style={styles.sponsorCtaTxt}>{sponsor.cta}</Text>
              </Pressable>
            </View>
          )}

          <View style={styles.pollWrap}>
            <Text style={styles.question}>{feud.question}</Text>
            <View style={styles.pollSplit}>
              <Pressable
                testID="vote-a-button"
                onPress={() => vote("A")}
                disabled={voting || !!feud.my_vote}
                style={[styles.pollHalf, { backgroundColor: colors.brandPrimary }, feud.my_vote === "B" && { opacity: 0.35 }]}
              >
                <Text style={styles.pollPct}>{feud.revealed ? `${feud.pct_a}%` : "?"}</Text>
                <Text style={styles.pollName}>{feud.party_a}</Text>
                <Text style={styles.pollVotes}>{feud.revealed ? `${feud.votes_a} voti` : "voti nascosti"}</Text>
                {feud.my_vote === "A" && <View style={styles.checkPill}><Ionicons name="checkmark" size={14} color={colors.brandPrimary} /></View>}
              </Pressable>
              <Pressable
                testID="vote-b-button"
                onPress={() => vote("B")}
                disabled={voting || !!feud.my_vote}
                style={[styles.pollHalf, { backgroundColor: colors.brandSecondary }, feud.my_vote === "A" && { opacity: 0.35 }]}
              >
                <Text style={[styles.pollPct, { color: colors.onBrandSecondary }]}>{feud.revealed ? `${feud.pct_b}%` : "?"}</Text>
                <Text style={[styles.pollName, { color: colors.onBrandSecondary }]}>{feud.party_b}</Text>
                <Text style={[styles.pollVotes, { color: colors.onBrandSecondary }]}>{feud.revealed ? `${feud.votes_b} voti` : "voti nascosti"}</Text>
                {feud.my_vote === "B" && <View style={[styles.checkPill, { backgroundColor: colors.onBrandSecondary }]}><Ionicons name="checkmark" size={14} color={colors.brandSecondary} /></View>}
              </Pressable>
            </View>
            {!feud.my_vote && <Text style={styles.pollHint}>Vota per svelare i risultati e sbloccare i commenti.</Text>}
          </View>

          {error && <Text style={styles.err}>{error}</Text>}

          {feud.my_vote && (
            <View style={styles.commentInputWrap}>
              <TextInput
                testID="comment-input"
                style={styles.commentInput}
                placeholder="Scrivi il tuo commento..."
                placeholderTextColor={colors.muted}
                value={commentText}
                onChangeText={setCommentText}
                multiline
              />
              <Pressable
                testID="submit-comment"
                style={[styles.postBtn, { backgroundColor: sideColor(feud.my_vote) }]}
                onPress={submitComment}
                disabled={posting}
              >
                <Text style={[styles.postBtnTxt, { color: onSideColor(feud.my_vote) }]}>
                  {posting ? "..." : "PUBBLICA"}
                </Text>
              </Pressable>
            </View>
          )}

          <View style={styles.commentsHeader}>
            <View style={[styles.commentsHeaderHalf, { backgroundColor: colors.brandPrimary }]}>
              <Text style={styles.commentsHeaderTxt}>PRO {feud.party_a.toUpperCase()}</Text>
            </View>
            <View style={[styles.commentsHeaderHalf, { backgroundColor: colors.brandSecondary }]}>
              <Text style={[styles.commentsHeaderTxt, { color: colors.onBrandSecondary }]}>PRO {feud.party_b.toUpperCase()}</Text>
            </View>
          </View>

          <View style={styles.commentsRow}>
            <View style={styles.commentsCol} testID="comments-col-a">
              {sideA.length === 0 ? (
                <Text style={styles.noCmt}>Nessun commento.</Text>
              ) : sideA.map((c) => (
                <CommentItem key={c.comment_id} c={c} expanded={expanded[c.comment_id]} onToggle={() => toggleReplies(c.comment_id)}
                  replyingTo={replyingTo} setReplyingTo={setReplyingTo} replyText={replyText} setReplyText={setReplyText}
                  onSubmitReply={() => submitReply(c.comment_id)} canReply={!!feud.my_vote}
                />
              ))}
            </View>
            <View style={styles.commentsCol} testID="comments-col-b">
              {sideB.length === 0 ? (
                <Text style={styles.noCmt}>Nessun commento.</Text>
              ) : sideB.map((c) => (
                <CommentItem key={c.comment_id} c={c} expanded={expanded[c.comment_id]} onToggle={() => toggleReplies(c.comment_id)}
                  replyingTo={replyingTo} setReplyingTo={setReplyingTo} replyText={replyText} setReplyText={setReplyText}
                  onSubmitReply={() => submitReply(c.comment_id)} canReply={!!feud.my_vote}
                />
              ))}
            </View>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function CommentItem({
  c, expanded, onToggle, replyingTo, setReplyingTo, replyText, setReplyText, onSubmitReply, canReply,
}: {
  c: Comment; expanded?: Reply[]; onToggle: () => void;
  replyingTo: string | null; setReplyingTo: (v: string | null) => void;
  replyText: string; setReplyText: (v: string) => void;
  onSubmitReply: () => void; canReply: boolean;
}) {
  const router = useRouter();
  const isReplying = replyingTo === c.comment_id;
  return (
    <View style={cs.item} testID={`comment-${c.comment_id}`}>
      <Pressable onPress={() => router.push(`/user/${c.user_id}`)} testID={`comment-user-${c.user_id}`}>
        <Text style={[cs.nick, { color: sideColor(c.side) }]}>@{c.nickname}</Text>
      </Pressable>
      <Text style={cs.text}>{c.text}</Text>
      <View style={cs.actions}>
        <Pressable onPress={onToggle}>
          <Text style={cs.actionTxt}>
            {expanded ? "↑" : `↓ ${c.reply_count ?? 0}`}
          </Text>
        </Pressable>
        {canReply && (
          <Pressable onPress={() => setReplyingTo(isReplying ? null : c.comment_id)} testID={`reply-btn-${c.comment_id}`}>
            <Text style={cs.actionTxt}>{isReplying ? "annulla" : "rispondi"}</Text>
          </Pressable>
        )}
      </View>
      {expanded && expanded.length > 0 && (
        <View style={cs.replies}>
          {expanded.map((r) => (
            <View key={r.reply_id} style={cs.reply}>
              <Pressable onPress={() => router.push(`/user/${r.user_id}`)}>
                <Text style={[cs.nick, { color: sideColor(r.side), fontSize: font.sizes.xs }]}>@{r.nickname}</Text>
              </Pressable>
              <Text style={[cs.text, { fontSize: font.sizes.xs }]}>{r.text}</Text>
            </View>
          ))}
        </View>
      )}
      {isReplying && (
        <View style={cs.replyInputWrap}>
          <TextInput
            style={cs.replyInput}
            value={replyText}
            onChangeText={setReplyText}
            placeholder="Rispondi..."
            placeholderTextColor={colors.muted}
            multiline
            testID={`reply-input-${c.comment_id}`}
          />
          <Pressable onPress={onSubmitReply} style={cs.replySend} testID={`reply-send-${c.comment_id}`}>
            <Text style={cs.replySendTxt}>INVIA</Text>
          </Pressable>
        </View>
      )}
    </View>
  );
}

const cs = StyleSheet.create({
  item: { borderWidth: 2, borderColor: colors.border, padding: spacing.sm, backgroundColor: colors.surfaceSecondary, marginBottom: spacing.sm },
  nick: { fontSize: font.sizes.sm, fontWeight: "500", marginBottom: 2, letterSpacing: 0.5 },
  text: { fontSize: font.sizes.base, color: colors.onSurface, lineHeight: 18 },
  actions: { flexDirection: "row", gap: spacing.md, marginTop: spacing.xs },
  actionTxt: { fontSize: font.sizes.xs, color: colors.muted, letterSpacing: 1 },
  replies: { marginTop: spacing.sm, paddingLeft: spacing.sm, borderLeftWidth: 2, borderColor: colors.border, gap: spacing.xs },
  reply: { paddingVertical: spacing.xs },
  replyInputWrap: { marginTop: spacing.sm, gap: spacing.xs },
  replyInput: { borderWidth: 2, borderColor: colors.border, padding: spacing.xs, fontSize: font.sizes.sm, color: colors.onSurface, minHeight: 40 },
  replySend: { backgroundColor: colors.onSurface, paddingVertical: spacing.xs, alignItems: "center" },
  replySendTxt: { color: colors.onSurfaceInverse, fontSize: font.sizes.xs, letterSpacing: 1 },
});

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  centerFill: { flex: 1, alignItems: "center", justifyContent: "center" },
  topbar: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", backgroundColor: colors.surfaceInverse, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderBottomWidth: 2, borderColor: colors.border },
  backBtn: { flexDirection: "row", alignItems: "center", gap: 4 },
  backTxt: { color: colors.onSurfaceInverse, fontSize: font.sizes.sm, letterSpacing: 1 },
  topCat: { color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 2 },
  hero: { height: 220, justifyContent: "flex-end" },
  heroContent: { padding: spacing.lg },
  title: { color: "#FFFFFF", fontSize: font.sizes.xxxl, lineHeight: 36, letterSpacing: 0.5, fontWeight: "500" },
  article: { padding: spacing.lg, borderBottomWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceSecondary },
  sectionKicker: { fontSize: font.sizes.sm, letterSpacing: 2, color: colors.brandPrimary, marginBottom: spacing.xs },
  mediaSection: { paddingHorizontal: spacing.lg, marginBottom: spacing.md },
  summary: { fontSize: font.sizes.lg, lineHeight: 24, color: colors.onSurface },
  sourcesBox: { padding: spacing.lg, borderBottomWidth: 2, borderColor: colors.border, backgroundColor: colors.surface, gap: spacing.sm },
  sourceItem: { borderWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceSecondary, padding: spacing.sm },
  sourceName: { fontSize: font.sizes.xs, letterSpacing: 2, color: colors.brandPrimary },
  sourceTitle: { fontSize: font.sizes.base, color: colors.onSurface, marginTop: 2, lineHeight: 18 },
  sourceLink: { fontSize: font.sizes.xs, color: colors.muted, marginTop: 4 },
  shareBtn: { width: 36, height: 36, borderWidth: 2, borderColor: colors.brandSecondary, alignItems: "center", justifyContent: "center" },
  sponsorBox: { padding: spacing.md, borderBottomWidth: 2, borderColor: colors.border, backgroundColor: colors.brandSecondary, gap: spacing.xs },
  sponsorLabel: { fontSize: font.sizes.xs, letterSpacing: 2, color: colors.onBrandSecondary, opacity: 0.7 },
  sponsorHeadline: { fontSize: font.sizes.lg, color: colors.onBrandSecondary, lineHeight: 22 },
  sponsorCta: { alignSelf: "flex-start", borderWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceInverse, paddingVertical: spacing.xs, paddingHorizontal: spacing.md, marginTop: spacing.xs },
  sponsorCtaTxt: { color: colors.onSurfaceInverse, fontSize: font.sizes.sm, letterSpacing: 2, fontWeight: "500" },
  pollWrap: { padding: spacing.lg, backgroundColor: colors.surfaceInverse, borderBottomWidth: 2, borderColor: colors.border },
  question: { color: colors.onSurfaceInverse, fontSize: font.sizes.xl, letterSpacing: 0.5, marginBottom: spacing.md, textAlign: "center" },
  pollSplit: { flexDirection: "row", borderWidth: 2, borderColor: colors.border },
  pollHalf: { flex: 1, paddingVertical: spacing.lg, alignItems: "center", position: "relative" },
  pollPct: { color: colors.onBrandPrimary, fontSize: font.sizes.giant, fontWeight: "500", letterSpacing: 1 },
  pollName: { color: colors.onBrandPrimary, fontSize: font.sizes.base, letterSpacing: 1, marginTop: 4, textAlign: "center", paddingHorizontal: spacing.sm },
  pollVotes: { color: colors.onBrandPrimary, fontSize: font.sizes.xs, opacity: 0.85, marginTop: 2 },
  checkPill: { position: "absolute", top: 6, right: 6, width: 22, height: 22, borderRadius: 11, backgroundColor: colors.onBrandPrimary, alignItems: "center", justifyContent: "center" },
  pollHint: { color: colors.brandSecondary, fontSize: font.sizes.sm, textAlign: "center", marginTop: spacing.md, letterSpacing: 1 },
  err: { color: colors.error, padding: spacing.md, borderWidth: 2, borderColor: colors.error, margin: spacing.lg },
  commentInputWrap: { padding: spacing.md, borderBottomWidth: 2, borderColor: colors.border, backgroundColor: colors.surface, gap: spacing.sm },
  commentInput: { borderWidth: 2, borderColor: colors.border, padding: spacing.sm, minHeight: 60, fontSize: font.sizes.base, color: colors.onSurface, backgroundColor: colors.surfaceSecondary },
  postBtn: { paddingVertical: spacing.sm, alignItems: "center", borderWidth: 2, borderColor: colors.border },
  postBtnTxt: { fontSize: font.sizes.base, letterSpacing: 2, fontWeight: "500" },
  commentsHeader: { flexDirection: "row", borderTopWidth: 2, borderBottomWidth: 2, borderColor: colors.border },
  commentsHeaderHalf: { flex: 1, paddingVertical: spacing.sm, alignItems: "center" },
  commentsHeaderTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.sm, letterSpacing: 1, fontWeight: "500" },
  commentsRow: { flexDirection: "row" },
  commentsCol: { flex: 1, padding: spacing.sm, borderRightWidth: 1, borderColor: colors.border },
  noCmt: { color: colors.muted, fontSize: font.sizes.sm, textAlign: "center", padding: spacing.md },
  goneBox: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xxl, gap: spacing.md },
  goneTitle: { color: colors.onSurface, fontSize: font.sizes.xxxl, letterSpacing: 2, fontWeight: "500" },
  goneMsg: { color: colors.onSurface, fontSize: font.sizes.lg, textAlign: "center", lineHeight: 24 },
  goneHint: { color: colors.muted, fontSize: font.sizes.sm, textAlign: "center", lineHeight: 18, marginTop: spacing.xs },
  goneBtn: { marginTop: spacing.lg, paddingVertical: spacing.md, paddingHorizontal: spacing.xl, borderWidth: 2, borderColor: colors.onSurface, backgroundColor: colors.brandPrimary },
  goneBtnTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.base, letterSpacing: 2, fontWeight: "500" },
});
