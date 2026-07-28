import { useEffect, useRef, useState, useCallback } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, TextInput, ActivityIndicator,
  KeyboardAvoidingView, Platform, Modal,
} from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as FileSystem from "expo-file-system/legacy";
import * as Sharing from "expo-sharing";
import { storage } from "@/src/utils/storage";
import { colors, spacing, font } from "@/src/theme";
import AnalyticsPanel, { AnalyticsPanelHandle } from "@/src/components/AnalyticsPanel";
import { useAuth } from "@/src/auth/AuthContext";

const KEY_STORAGE = "populus_admin_key";
const BASE = process.env.EXPO_PUBLIC_BACKEND_URL || "";
const ADMIN_EMAIL = "carlofarinapayme@gmail.com";

type Stats = {
  total_users: number;
  onboarded_users: number;
  total_votes: number;
  by_region: { region: string; count: number }[];
  by_sex: Record<string, number>;
  by_age: Record<string, number>;
  top_feuds: {
    feud_id: string; title: string; category_label: string;
    party_a: string; party_b: string; total: number; pct_a: number; pct_b: number;
  }[];
};

async function fetchStats(key: string): Promise<Stats> {
  const res = await fetch(`${BASE}/api/admin/stats`, { headers: { "X-Admin-Key": key } });
  const text = await res.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : `HTTP ${res.status}`);
  }
  return data as Stats;
}

export default function AdminScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const [key, setKey] = useState<string>("");
  const [keyInput, setKeyInput] = useState<string>("");
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);
  // Two-tab switch — Analytics (KPI + retention + categorie + profili) is
  // the growth-plan dashboard; Demografia is the legacy one (utile per
  // controllare voti per regione/sesso/età al volo).
  const [tab, setTab] = useState<"analytics" | "demographics">("analytics");
  const analyticsRef = useRef<AnalyticsPanelHandle | null>(null);

  // Reset confirmation flow — two-step: first Modal asks "azzerare?", then
  // if user says yes a second Modal offers the JSON snapshot download.
  const [resetStep, setResetStep] = useState<"idle" | "confirm" | "download" | "working">("idle");
  const [resetError, setResetError] = useState<string | null>(null);
  const snapshotRef = useRef<any>(null);

  // Only the primary developer account is allowed past this screen.
  // Anyone else — even if they somehow reach this route — sees an
  // "accesso negato" wall.
  const userEmail = (user?.email || "").toLowerCase();
  const isAllowed = userEmail === ADMIN_EMAIL;

  useEffect(() => {
    (async () => {
      const saved = await storage.secureGet<string>(KEY_STORAGE, "");
      if (saved) { setKey(saved); setKeyInput(saved); }
      setHydrated(true);
    })();
  }, []);

  const load = useCallback(async (k: string) => {
    setLoading(true); setError(null);
    try {
      const s = await fetchStats(k);
      setStats(s);
    } catch (e: any) {
      setError(e?.message || "Errore");
      setStats(null);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (hydrated && key && tab === "demographics") load(key);
  }, [hydrated, key, load, tab]);

  const submitKey = async () => {
    if (!keyInput.trim()) return;
    await storage.secureSet(KEY_STORAGE, keyInput.trim());
    setKey(keyInput.trim());
  };

  const clearKey = async () => {
    await storage.secureRemove(KEY_STORAGE);
    setKey(""); setKeyInput(""); setStats(null);
  };

  // Refresh works on whichever tab is currently mounted.
  const refreshCurrent = useCallback(async () => {
    if (tab === "analytics") {
      await analyticsRef.current?.reload();
    } else if (key) {
      await load(key);
    }
  }, [tab, key, load]);

  // Fetch a full snapshot (analytics + legacy demografia) so the
  // report file the user downloads is a complete "before-reset" record.
  const fetchFullSnapshot = useCallback(async (k: string) => {
    const paths = [
      "/admin/analytics/overview",
      "/admin/analytics/active-users?days=30",
      "/admin/analytics/retention",
      "/admin/analytics/deep-action-rate?days=7",
      "/admin/analytics/top-feuds-24h",
      "/admin/analytics/categories",
      "/admin/analytics/profiles",
      "/admin/analytics/funnel",
      "/admin/analytics/dev-accounts",
      "/admin/stats",
    ];
    const results = await Promise.all(paths.map(async (p) => {
      try {
        const r = await fetch(`${BASE}/api${p}`, { headers: { "X-Admin-Key": k } });
        return r.ok ? await r.json() : null;
      } catch { return null; }
    }));
    return {
      exported_at: new Date().toISOString(),
      exported_by: userEmail,
      analytics: {
        overview: results[0],
        active_users_series: results[1],
        retention: results[2],
        deep_action_rate: results[3],
        top_feuds_24h: results[4],
        categories: results[5],
        profiles: results[6],
        funnel: results[7],
        dev_accounts: results[8],
      },
      demographics: results[9],
    };
  }, [userEmail]);

  // Build a mobile-friendly HTML report from the raw snapshot. HTML
  // opens in any browser (including on iPhone/Android without extra
  // apps), stays readable, and includes tables the user can scroll
  // through without needing a JSON viewer.
  const buildHtmlReport = useCallback((snap: any): string => {
    const esc = (s: any) => String(s ?? "").replace(/[&<>"']/g,
      (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch] || ch));
    const fmt = (v: any) => v == null || v === "" ? "—" : typeof v === "number" ? v.toLocaleString("it-IT") : esc(v);
    const section = (title: string, body: string) =>
      `<section><h2>${esc(title)}</h2>${body}</section>`;
    const table = (headers: string[], rows: any[][]) => {
      const th = headers.map((h) => `<th>${esc(h)}</th>`).join("");
      const tr = rows.map((r) => `<tr>${r.map((c) => `<td>${fmt(c)}</td>`).join("")}</tr>`).join("");
      return `<table><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table>`;
    };
    const kv = (obj: Record<string, any>) => table(["Chiave", "Valore"], Object.entries(obj || {}));

    const a = snap?.analytics || {};
    const d = snap?.demographics || {};
    const ov = a.overview || {};

    const sections: string[] = [];
    sections.push(section("Riepilogo", kv({
      "Utenti totali": ov.users?.total,
      "Registrati": ov.users?.registered,
      "Anonimi": ov.users?.anonymous,
      "Nuovi (24h)": ov.users?.new_24h,
      "Nuovi (7g)": ov.users?.new_7d,
      "Nuovi (30g)": ov.users?.new_30d,
      "Voti totali": ov.votes?.total,
      "Voti (24h)": ov.votes?.last_24h,
      "Voti (7g)": ov.votes?.last_7d,
      "Voti (30g)": ov.votes?.last_30d,
      "Commenti totali": ov.comments?.total,
      "Commenti (24h)": ov.comments?.last_24h,
      "Commenti (7g)": ov.comments?.last_7d,
      "DAU": ov.active_users?.dau,
      "WAU": ov.active_users?.wau,
      "MAU": ov.active_users?.mau,
      "WAU/MAU (%)": ov.active_users?.wau_over_mau,
      "Baseline reset": ov.baseline_at,
    })));

    if (a.retention?.cohorts?.length) {
      sections.push(section("Retention per coorte settimanale", table(
        ["Coorte", "Utenti", "W1", "W2", "W4"],
        a.retention.cohorts.map((c: any) => [c.cohort, c.size, c.w1_pct, c.w2_pct, c.w4_pct]),
      )));
    }
    if (a.deep_action_rate) {
      sections.push(section("Azione profonda (7g)", kv({
        "Utenti attivi": a.deep_action_rate.active_users,
        "Con almeno un voto": a.deep_action_rate.with_vote_pct,
        "Con almeno un commento": a.deep_action_rate.with_comment_pct,
      })));
    }
    if (a.top_feuds_24h?.top?.length) {
      sections.push(section("Top faide (voti prime 24h)", table(
        ["Categoria", "Titolo", "Voti 24h"],
        a.top_feuds_24h.top.map((f: any) => [f.category_label, f.title, f.votes_first_24h]),
      )));
    }
    if (Array.isArray(a.categories) && a.categories.length) {
      sections.push(section("Categorie", table(
        ["Categoria", "Voti", "Commenti", "Views", "Utenti attivi"],
        a.categories.map((c: any) => [c.category, c.votes, c.comments, c.views, c.active_users]),
      )));
    }
    if (a.profiles) {
      const p = a.profiles;
      sections.push(section("Profili (utenti registrati)", kv({
        "Totale": p.total,
        "Con foto (%)": p.with_photo_pct,
        "Con bio (%)": p.with_bio_pct,
        "Con display name (%)": p.with_display_name_pct,
        "Con cerchia (%)": p.with_circle_pct,
        "Onboarded (%)": p.onboarded_pct,
        "Push abilitato (%)": p.push_enabled_pct,
        "Cerchia media": p.avg_circle_size,
      })));
      if (p.regions?.length) {
        sections.push(section("Distribuzione regionale", table(
          ["Regione", "N", "%"],
          p.regions.map((r: any) => [r.region, r.count, r.pct]),
        )));
      }
      if (p.ages && Object.keys(p.ages).length) {
        sections.push(section("Fasce d'età", table(["Fascia", "N"], Object.entries(p.ages))));
      }
      if (p.sex && Object.keys(p.sex).length) {
        sections.push(section("Sesso", table(["Sesso", "N"], Object.entries(p.sex))));
      }
      if (p.auth_providers && Object.keys(p.auth_providers).length) {
        sections.push(section("Provider di autenticazione", table(["Provider", "N"], Object.entries(p.auth_providers))));
      }
    }
    if (a.funnel) {
      sections.push(section("Funnel (30g)", kv({
        "Nuovi utenti": a.funnel.new_users,
        "Con almeno un voto (%)": a.funnel.with_vote_pct,
        "Con almeno un commento (%)": a.funnel.with_comment_pct,
      })));
    }
    if (d) {
      sections.push(section("Demografia (legacy)", kv({
        "Utenti totali": d.total_users,
        "Onboarded": d.onboarded_users,
        "Voti totali": d.total_votes,
      })));
      if (d.by_region?.length) {
        sections.push(section("Voti per regione", table(
          ["Regione", "Voti", "% side A", "% side B"],
          d.by_region.map((r: any) => [r.region, r.total, r.a_pct, r.b_pct]),
        )));
      }
      if (d.by_sex) {
        sections.push(section("Voti per sesso", kv(d.by_sex)));
      }
      if (d.by_age) {
        sections.push(section("Voti per età", kv(d.by_age)));
      }
    }
    if (Array.isArray(a.dev_accounts?.emails) && a.dev_accounts.emails.length) {
      sections.push(section("Account di sviluppo esclusi", table(
        ["Email"], a.dev_accounts.emails.map((e: string) => [e]),
      )));
    }

    const generated = new Date().toLocaleString("it-IT");
    return `<!doctype html>
<html lang="it"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Populus · Report analytics ${esc(generated)}</title>
<style>
  :root{color-scheme:light dark}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;margin:0;padding:24px 16px;max-width:900px;margin:0 auto;background:#faf9f6;color:#111}
  h1{font-size:22px;letter-spacing:2px;margin:0 0 4px}
  h2{font-size:15px;letter-spacing:2px;margin:24px 0 8px;padding-bottom:6px;border-bottom:2px solid #111;text-transform:uppercase}
  .meta{color:#666;font-size:13px;margin-bottom:16px}
  table{width:100%;border-collapse:collapse;font-size:14px}
  th,td{padding:6px 8px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}
  th{background:#111;color:#faf9f6;font-weight:600;font-size:12px;letter-spacing:1px}
  section{background:#fff;border:1px solid #ddd;padding:12px 16px;margin-bottom:12px}
  @media (prefers-color-scheme: dark){
    body{background:#111;color:#faf9f6}
    section{background:#1a1a1a;border-color:#333}
    th{background:#faf9f6;color:#111}
    td{border-color:#333}
  }
</style></head>
<body>
  <h1>POPULUS · REPORT ANALYTICS</h1>
  <div class="meta">Generato il ${esc(generated)} · Da: ${esc(snap?.exported_by || "")}${
      ov.baseline_at ? ` · Baseline: ${esc(new Date(ov.baseline_at).toLocaleString("it-IT"))}` : ""
    }</div>
  ${sections.join("\n")}
</body></html>`;
  }, []);

  // Persist the snapshot as a mobile-friendly HTML report. Web triggers
  // a browser download; native writes to cache + opens the share sheet
  // so the user can save to Files/Photos or send via Airdrop/Drive.
  const downloadSnapshot = useCallback(async (snapshot: any) => {
    const ts = new Date().toISOString().replace(/[:.]/g, "-");
    const fileName = `populus-analytics-${ts}.html`;
    const html = buildHtmlReport(snapshot);
    if (Platform.OS === "web") {
      const blob = new Blob([html], { type: "text/html;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fileName;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      return;
    }
    const path = `${FileSystem.cacheDirectory}${fileName}`;
    await FileSystem.writeAsStringAsync(path, html, { encoding: FileSystem.EncodingType.UTF8 });
    if (await Sharing.isAvailableAsync()) {
      await Sharing.shareAsync(path, { mimeType: "text/html", dialogTitle: "Salva report analytics" });
    }
  }, [buildHtmlReport]);

  // Second half of the reset flow: hit the reset endpoint, reload the
  // active tab, then surface any error to the confirmation modal.
  const performReset = useCallback(async () => {
    setResetStep("working"); setResetError(null);
    try {
      const res = await fetch(`${BASE}/api/admin/analytics/reset`, {
        method: "POST", headers: { "X-Admin-Key": key },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await refreshCurrent();
      setResetStep("idle");
    } catch (e: any) {
      setResetError(e?.message || "Reset non riuscito");
      setResetStep("confirm");
    }
  }, [key, refreshCurrent]);

  // First step of the reset flow. Takes a snapshot first so we can
  // offer the download in the follow-up step even if the reset succeeds.
  const openResetFlow = useCallback(async () => {
    setResetError(null);
    try {
      snapshotRef.current = await fetchFullSnapshot(key);
    } catch {
      snapshotRef.current = null;
    }
    setResetStep("confirm");
  }, [fetchFullSnapshot, key]);

  const totalRegion = stats?.by_region.reduce((s, r) => s + r.count, 0) || 0;
  const totalSex = stats ? Object.values(stats.by_sex).reduce((s, n) => s + n, 0) : 0;
  const totalAge = stats ? Object.values(stats.by_age).reduce((s, n) => s + n, 0) : 0;

  if (!hydrated) {
    return (
      <SafeAreaView style={styles.safe}>
        <ActivityIndicator size="large" color={colors.brandPrimary} />
      </SafeAreaView>
    );
  }

  // Hard gate: only the owner account may enter this screen. If someone
  // else lands here (deep link, stale session, curiosity) we show a
  // friendly denial screen instead of the login form.
  if (!isAllowed) {
    return (
      <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
        <View style={styles.gateWrap}>
          <Pressable onPress={() => router.back()} style={styles.gateBack} testID="admin-back">
            <Ionicons name="chevron-back" size={22} color={colors.onSurface} />
            <Text style={styles.gateBackTxt}>INDIETRO</Text>
          </Pressable>
          <View style={styles.gateHeader}>
            <Ionicons name="lock-closed" size={64} color={colors.onSurface} />
            <Text style={styles.gateTitle}>ACCESSO NEGATO</Text>
            <Text style={styles.gateSub}>
              Questa area è riservata al proprietario dell&apos;app.
            </Text>
          </View>
        </View>
      </SafeAreaView>
    );
  }

  if (!key) {
    return (
      <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
        <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
          <View style={styles.gateWrap}>
            <Pressable onPress={() => router.back()} style={styles.gateBack} testID="admin-back">
              <Ionicons name="chevron-back" size={22} color={colors.onSurface} />
              <Text style={styles.gateBackTxt}>INDIETRO</Text>
            </Pressable>
            <View style={styles.gateHeader}>
              <Ionicons name="shield-checkmark" size={64} color={colors.onSurface} />
              <Text style={styles.gateTitle}>PANNELLO ADMIN</Text>
              <Text style={styles.gateSub}>Inserisci la chiave amministratore.</Text>
            </View>
            <TextInput
              value={keyInput}
              onChangeText={setKeyInput}
              placeholder="Chiave admin"
              placeholderTextColor={colors.muted}
              secureTextEntry
              autoCapitalize="none"
              style={styles.gateInput}
              testID="admin-key-input"
              onSubmitEditing={submitKey}
            />
            <Pressable onPress={submitKey} style={styles.gateCta} testID="admin-key-submit">
              <Text style={styles.gateCtaTxt}>ACCEDI</Text>
            </Pressable>
          </View>
        </KeyboardAvoidingView>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]} testID="admin-screen">
      <View style={styles.header}>
        <View style={{ flex: 1 }}>
          <Text style={styles.brand}>ADMIN</Text>
          <Text style={styles.subtitle}>
            {tab === "analytics" ? "Analytics" : "Statistiche demografiche"}
          </Text>
        </View>
        <Pressable onPress={refreshCurrent} style={styles.iconBtn} testID="admin-refresh">
          <Ionicons name="refresh" size={20} color={colors.brandSecondary} />
        </Pressable>
        <Pressable onPress={openResetFlow} style={styles.iconBtn} testID="admin-reset">
          <Ionicons name="trash-outline" size={20} color="#ff5c5c" />
        </Pressable>
        <Pressable onPress={clearKey} style={styles.iconBtn} testID="admin-logout">
          <Ionicons name="log-out-outline" size={20} color={colors.brandSecondary} />
        </Pressable>
      </View>

      {/* Tab switcher */}
      <View style={styles.tabsRow}>
        <Pressable
          style={[styles.tabBtn, tab === "analytics" && styles.tabBtnActive]}
          onPress={() => setTab("analytics")}
          testID="admin-tab-analytics"
        >
          <Text style={[styles.tabTxt, tab === "analytics" && styles.tabTxtActive]}>ANALYTICS</Text>
        </Pressable>
        <Pressable
          style={[styles.tabBtn, tab === "demographics" && styles.tabBtnActive]}
          onPress={() => setTab("demographics")}
          testID="admin-tab-demographics"
        >
          <Text style={[styles.tabTxt, tab === "demographics" && styles.tabTxtActive]}>DEMOGRAFIA</Text>
        </Pressable>
      </View>

      {tab === "analytics" ? (
        <AnalyticsPanel adminKey={key} ref={analyticsRef} />
      ) : loading ? (
        <View style={styles.centerFill}><ActivityIndicator size="large" color={colors.brandPrimary} /></View>
      ) : error ? (
        <View style={styles.centerFill}>
          <Text style={styles.err} testID="admin-error">{error}</Text>
          <Pressable onPress={clearKey} style={styles.smallBtn}><Text style={styles.smallBtnTxt}>CAMBIA CHIAVE</Text></Pressable>
        </View>
      ) : stats ? (
        <ScrollView contentContainerStyle={styles.content}>
          <View style={styles.statsRow}>
            <StatCard label="UTENTI" value={stats.total_users} />
            <StatCard label="ONBOARDED" value={stats.onboarded_users} />
            <StatCard label="VOTI" value={stats.total_votes} highlight />
          </View>

          <SectionBlock title="VOTI PER REGIONE" empty={totalRegion === 0}>
            {stats.by_region.slice(0, 12).map((r) => (
              <BarRow key={r.region} label={r.region === "unknown" ? "Sconosciuta" : r.region}
                value={r.count} total={totalRegion} color={colors.brandPrimary}
                testID={`region-${r.region}`}
              />
            ))}
          </SectionBlock>

          <SectionBlock title="VOTI PER SESSO" empty={totalSex === 0}>
            {[
              { k: "F", label: "Femmina" },
              { k: "M", label: "Maschio" },
              { k: "other", label: "Altro" },
              { k: "na", label: "Preferisco non dire" },
              { k: "unknown", label: "Sconosciuto" },
            ].map(({ k, label }) => (
              <BarRow key={k} label={label} value={stats.by_sex[k] ?? 0} total={totalSex}
                color={colors.brandSecondary} onColor={colors.onBrandSecondary}
                testID={`sex-${k}`}
              />
            ))}
          </SectionBlock>

          <SectionBlock title="VOTI PER FASCIA ETÀ" empty={totalAge === 0}>
            {["13-17","18-24","25-34","35-44","45-54","55-64","65+","unknown"].map((b) => (
              <BarRow key={b} label={b === "unknown" ? "Sconosciuta" : b}
                value={stats.by_age[b] ?? 0} total={totalAge}
                color={colors.brandPrimary}
                testID={`age-${b}`}
              />
            ))}
          </SectionBlock>

          <SectionBlock title="TOP 5 FAIDE" empty={stats.top_feuds.length === 0}>
            {stats.top_feuds.map((f) => (
              <View key={f.feud_id} style={styles.topFeud} testID={`top-${f.feud_id}`}>
                <Text style={styles.topCat}>{f.category_label.toUpperCase()} · {f.total} VOTI</Text>
                <Text style={styles.topTitle} numberOfLines={2}>{f.title}</Text>
                <View style={styles.topSplit}>
                  <View style={[styles.topHalf, { backgroundColor: colors.brandPrimary, flex: Math.max(f.pct_a, 5) }]}>
                    <Text style={styles.topHalfTxt}>{f.pct_a}%</Text>
                  </View>
                  <View style={[styles.topHalf, { backgroundColor: colors.brandSecondary, flex: Math.max(f.pct_b, 5) }]}>
                    <Text style={[styles.topHalfTxt, { color: colors.onBrandSecondary }]}>{f.pct_b}%</Text>
                  </View>
                </View>
                <View style={styles.topPartiesRow}>
                  <Text style={styles.topParty}>{f.party_a}</Text>
                  <Text style={[styles.topParty, { textAlign: "right" }]}>{f.party_b}</Text>
                </View>
              </View>
            ))}
          </SectionBlock>
        </ScrollView>
      ) : null}

      {/* Reset confirmation flow — a proper Modal (not Alert.alert)
          because Alert on react-native-web only supports a single OK
          button, so multi-choice confirmations silently no-op there. */}
      <Modal
        transparent
        visible={resetStep !== "idle"}
        animationType="fade"
        onRequestClose={() => setResetStep("idle")}
      >
        <View style={styles.modalBackdrop}>
          <View style={styles.modalCard} testID="admin-reset-modal">
            {resetStep === "confirm" || resetStep === "working" ? (
              <>
                <Text style={styles.modalTitle}>AZZERARE LE STATISTICHE?</Text>
                <Text style={styles.modalBody}>
                  Tutti i KPI del dashboard (voti, commenti, categorie, demografia,
                  DAU/WAU/MAU) torneranno a zero. Gli utenti e i voti reali NON
                  vengono cancellati — solo il dashboard riparte da zero.
                </Text>
                {resetError ? (
                  <Text style={styles.modalError}>{resetError}</Text>
                ) : null}
                <View style={styles.modalActions}>
                  <Pressable
                    onPress={() => setResetStep("idle")}
                    style={[styles.modalBtn, styles.modalBtnGhost]}
                    disabled={resetStep === "working"}
                    testID="admin-reset-cancel"
                  >
                    <Text style={styles.modalBtnGhostTxt}>ANNULLA</Text>
                  </Pressable>
                  <Pressable
                    onPress={async () => {
                      await performReset();
                      // If reset succeeded, offer download step.
                      // (performReset flips to idle on success — we
                      // re-open with 'download' only when we still have
                      // a snapshot in hand.)
                      if (snapshotRef.current) setResetStep("download");
                    }}
                    style={[styles.modalBtn, styles.modalBtnDanger]}
                    disabled={resetStep === "working"}
                    testID="admin-reset-confirm"
                  >
                    {resetStep === "working" ? (
                      <ActivityIndicator color="#fff" />
                    ) : (
                      <Text style={styles.modalBtnDangerTxt}>AZZERA</Text>
                    )}
                  </Pressable>
                </View>
              </>
            ) : resetStep === "download" ? (
              <>
                <Text style={styles.modalTitle}>SCARICARE IL REPORT?</Text>
                <Text style={styles.modalBody}>
                  Vuoi salvare un file JSON con lo snapshot completo delle
                  statistiche appena azzerate (analytics + demografia)? Utile
                  per archiviare i dati storici prima del reset.
                </Text>
                <View style={styles.modalActions}>
                  <Pressable
                    onPress={() => { snapshotRef.current = null; setResetStep("idle"); }}
                    style={[styles.modalBtn, styles.modalBtnGhost]}
                    testID="admin-download-skip"
                  >
                    <Text style={styles.modalBtnGhostTxt}>NO</Text>
                  </Pressable>
                  <Pressable
                    onPress={async () => {
                      try {
                        if (snapshotRef.current) await downloadSnapshot(snapshotRef.current);
                      } catch {
                        // Non-fatal — user can retry from the analytics view.
                      }
                      snapshotRef.current = null;
                      setResetStep("idle");
                    }}
                    style={[styles.modalBtn, styles.modalBtnConfirm]}
                    testID="admin-download-confirm"
                  >
                    <Text style={styles.modalBtnConfirmTxt}>SÌ, SCARICA</Text>
                  </Pressable>
                </View>
              </>
            ) : null}
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

function StatCard({ label, value, highlight }: { label: string; value: number; highlight?: boolean }) {
  return (
    <View style={[styles.statCard, highlight && { backgroundColor: colors.brandPrimary, borderColor: colors.brandPrimary }]}>
      <Text style={[styles.statCardValue, highlight && { color: colors.onBrandPrimary }]}>{value}</Text>
      <Text style={[styles.statCardLabel, highlight && { color: colors.onBrandPrimary }]}>{label}</Text>
    </View>
  );
}

function SectionBlock({ title, children, empty }: { title: string; children: React.ReactNode; empty?: boolean }) {
  return (
    <View style={styles.sectionBlock}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {empty ? <Text style={styles.sectionEmpty}>Nessun dato ancora.</Text> : children}
    </View>
  );
}

function BarRow({ label, value, total, color, onColor, testID }: {
  label: string; value: number; total: number; color: string; onColor?: string; testID?: string;
}) {
  if (value === 0) return null;
  const pct = total ? Math.round((100 * value) / total) : 0;
  return (
    <View style={styles.barRow} testID={testID}>
      <Text style={styles.barLabel}>{label}</Text>
      <View style={styles.barTrack}>
        <View style={[styles.barFill, { width: `${Math.max(pct, 4)}%`, backgroundColor: color }]}>
          <Text style={[styles.barVal, { color: onColor || colors.onBrandPrimary }]}>{value}</Text>
        </View>
      </View>
      <Text style={styles.barPct}>{pct}%</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  centerFill: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl, gap: spacing.md },
  header: { flexDirection: "row", alignItems: "center", gap: spacing.sm, padding: spacing.lg, backgroundColor: colors.surfaceInverse, borderBottomWidth: 2, borderColor: colors.border },
  brand: { color: colors.onSurfaceInverse, fontSize: font.sizes.xxxl, fontWeight: "500", letterSpacing: 2 },
  subtitle: { color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 2, marginTop: 2 },
  iconBtn: { width: 40, height: 40, borderWidth: 2, borderColor: colors.brandSecondary, alignItems: "center", justifyContent: "center" },
  content: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xxxl },
  statsRow: { flexDirection: "row", gap: spacing.sm },
  statCard: { flex: 1, borderWidth: 2, borderColor: colors.border, padding: spacing.md, backgroundColor: colors.surfaceSecondary, alignItems: "center" },
  statCardValue: { fontSize: font.sizes.xxxl, fontWeight: "500", color: colors.onSurface },
  statCardLabel: { fontSize: font.sizes.xs, letterSpacing: 1, color: colors.muted, marginTop: 2 },
  sectionBlock: { gap: spacing.sm, borderWidth: 2, borderColor: colors.border, padding: spacing.md, backgroundColor: colors.surfaceSecondary },
  sectionTitle: { fontSize: font.sizes.lg, letterSpacing: 2, fontWeight: "500", color: colors.onSurface, marginBottom: spacing.xs },
  sectionEmpty: { fontSize: font.sizes.base, color: colors.muted },
  barRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  barLabel: { flex: 1.2, fontSize: font.sizes.sm, color: colors.onSurface },
  barTrack: { flex: 2.5, height: 22, backgroundColor: colors.surface, borderWidth: 2, borderColor: colors.border, justifyContent: "center" },
  barFill: { height: "100%", justifyContent: "center", paddingHorizontal: 6, minWidth: 24 },
  barVal: { fontSize: font.sizes.xs, fontWeight: "500", letterSpacing: 0.5 },
  barPct: { width: 42, fontSize: font.sizes.xs, letterSpacing: 1, color: colors.muted, textAlign: "right" },
  topFeud: { borderWidth: 2, borderColor: colors.border, padding: spacing.sm, backgroundColor: colors.surface, gap: spacing.xs },
  topCat: { fontSize: font.sizes.xs, letterSpacing: 2, color: colors.brandPrimary },
  topTitle: { fontSize: font.sizes.base, color: colors.onSurface, lineHeight: 18 },
  topSplit: { flexDirection: "row", height: 20, borderWidth: 2, borderColor: colors.border, marginTop: 4 },
  topHalf: { justifyContent: "center", alignItems: "center" },
  topHalfTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.xs, fontWeight: "500", letterSpacing: 0.5 },
  topPartiesRow: { flexDirection: "row", justifyContent: "space-between", gap: spacing.sm },
  topParty: { flex: 1, fontSize: font.sizes.xs, letterSpacing: 0.5, color: colors.muted },
  err: { color: colors.error, borderWidth: 2, borderColor: colors.error, padding: spacing.sm, fontSize: font.sizes.base },
  smallBtn: { borderWidth: 2, borderColor: colors.border, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, backgroundColor: colors.surfaceInverse },
  smallBtnTxt: { color: colors.onSurfaceInverse, fontSize: font.sizes.sm, letterSpacing: 2, fontWeight: "500" },
  gateWrap: { flex: 1, padding: spacing.lg, gap: spacing.lg, justifyContent: "center" },
  gateBack: { flexDirection: "row", alignItems: "center", gap: 4, alignSelf: "flex-start", position: "absolute", top: spacing.lg, left: spacing.lg },
  gateBackTxt: { fontSize: font.sizes.sm, letterSpacing: 2, color: colors.onSurface, fontWeight: "500" },
  gateHeader: { alignItems: "center", gap: spacing.sm },
  gateTitle: { fontSize: font.sizes.xxxl, letterSpacing: 2, fontWeight: "500", color: colors.onSurface },
  gateSub: { fontSize: font.sizes.base, color: colors.muted },
  gateInput: { borderWidth: 2, borderColor: colors.border, padding: spacing.md, fontSize: font.sizes.lg, color: colors.onSurface, backgroundColor: colors.surfaceSecondary },
  gateCta: { backgroundColor: colors.brandPrimary, borderWidth: 2, borderColor: colors.border, paddingVertical: spacing.md, alignItems: "center" },
  gateCtaTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.xl, letterSpacing: 2, fontWeight: "500" },
  tabsRow: {
    flexDirection: "row",
    borderBottomWidth: 2,
    borderColor: colors.border,
    backgroundColor: colors.surfaceSecondary,
  },
  tabBtn: {
    flex: 1,
    paddingVertical: spacing.sm,
    alignItems: "center",
    borderRightWidth: 2,
    borderColor: colors.border,
  },
  tabBtnActive: {
    backgroundColor: colors.brandPrimary,
  },
  tabTxt: {
    fontSize: font.sizes.sm,
    letterSpacing: 2,
    color: colors.muted,
    fontWeight: "500",
  },
  tabTxtActive: {
    color: colors.onBrandPrimary,
  },
  // Reset confirmation modal
  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.55)",
    alignItems: "center",
    justifyContent: "center",
    padding: spacing.lg,
  },
  modalCard: {
    width: "100%",
    maxWidth: 420,
    backgroundColor: colors.surface,
    borderWidth: 2,
    borderColor: colors.border,
    padding: spacing.lg,
    gap: spacing.md,
  },
  modalTitle: {
    fontSize: font.sizes.xl,
    letterSpacing: 2,
    fontWeight: "500",
    color: colors.onSurface,
  },
  modalBody: {
    fontSize: font.sizes.base,
    color: colors.onSurface,
    lineHeight: 20,
  },
  modalError: {
    color: colors.error,
    fontSize: font.sizes.sm,
    borderWidth: 1,
    borderColor: colors.error,
    padding: spacing.xs,
  },
  modalActions: {
    flexDirection: "row",
    gap: spacing.sm,
    justifyContent: "flex-end",
    marginTop: spacing.xs,
  },
  modalBtn: {
    borderWidth: 2,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    minWidth: 100,
    alignItems: "center",
    justifyContent: "center",
  },
  modalBtnGhost: {
    borderColor: colors.border,
    backgroundColor: colors.surfaceSecondary,
  },
  modalBtnGhostTxt: {
    color: colors.onSurface,
    fontSize: font.sizes.sm,
    letterSpacing: 2,
    fontWeight: "500",
  },
  modalBtnDanger: {
    borderColor: "#c81f1f",
    backgroundColor: "#c81f1f",
  },
  modalBtnDangerTxt: {
    color: "#fff",
    fontSize: font.sizes.sm,
    letterSpacing: 2,
    fontWeight: "600",
  },
  modalBtnConfirm: {
    borderColor: colors.brandPrimary,
    backgroundColor: colors.brandPrimary,
  },
  modalBtnConfirmTxt: {
    color: colors.onBrandPrimary,
    fontSize: font.sizes.sm,
    letterSpacing: 2,
    fontWeight: "600",
  },
});
