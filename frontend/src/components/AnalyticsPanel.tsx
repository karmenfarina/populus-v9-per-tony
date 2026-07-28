/* Analytics panel for the /admin screen — developer dashboard.
 *
 * Design intent:
 *   • KPI cards with growth thresholds (verde/giallo/rosso).
 *   • Additional insights (categorie, profili, funnel, DAU/WAU/MAU serie).
 *   • Zero external chart deps: everything is drawn with View/Text so the
 *     look matches the rest of the brutalist UI and the bundle stays small.
 *   • Reads exclusively from /api/admin/analytics/* which already excludes
 *     dev accounts (DEV_ACCOUNT_EMAILS).
 *   • Refresh + reset controls live in the parent header — this component
 *     exposes them via a ref instead of rendering its own header row.
 */
import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useState } from "react";
import { View, Text, StyleSheet, Pressable, ActivityIndicator, ScrollView } from "react-native";
import { colors, spacing, font } from "@/src/theme";

const BASE = process.env.EXPO_PUBLIC_BACKEND_URL || "";

export type AnalyticsPanelHandle = {
  reload: () => Promise<void>;
  snapshot: () => any;
};

async function fetchJson(path: string, key: string): Promise<any> {
  const res = await fetch(`${BASE}/api${path}`, { headers: { "X-Admin-Key": key } });
  const text = await res.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : `HTTP ${res.status}`);
  }
  return data;
}

// ─── KPI colour thresholds from the growth plan (page 14) ───────────
// A KPI turns green when it beats `positive`, red when it's under
// `critical`, otherwise it's yellow (da migliorare).
type Threshold = { positive: number; critical: number; direction: "higher_better" | "lower_better" };

function statusColor(value: number | null | undefined, t: Threshold): string {
  if (value == null) return colors.muted;
  if (t.direction === "higher_better") {
    if (value >= t.positive) return "#0a8a3a"; // green
    if (value <= t.critical) return "#c81f1f"; // red
    return "#c48b1a"; // amber
  }
  if (value <= t.positive) return "#0a8a3a";
  if (value >= t.critical) return "#c81f1f";
  return "#c48b1a";
}

function statusLabel(value: number | null | undefined, t: Threshold): string {
  if (value == null) return "IN ACCUMULO";
  if (t.direction === "higher_better") {
    if (value >= t.positive) return "POSITIVO";
    if (value <= t.critical) return "CRITICO";
    return "DA MIGLIORARE";
  }
  if (value <= t.positive) return "POSITIVO";
  if (value >= t.critical) return "CRITICO";
  return "DA MIGLIORARE";
}

// Thresholds from page 14.
const THRESH_D30: Threshold = { positive: 10, critical: 5, direction: "higher_better" };
const THRESH_WAU_MAU: Threshold = { positive: 35, critical: 25, direction: "higher_better" };
const THRESH_DEEP: Threshold = { positive: 25, critical: 15, direction: "higher_better" };
const THRESH_TOP24H: Threshold = { positive: 20, critical: 10, direction: "higher_better" };

export default forwardRef<AnalyticsPanelHandle, { adminKey: string }>(function AnalyticsPanel(
  { adminKey },
  ref,
) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [overview, setOverview] = useState<any>(null);
  const [series, setSeries] = useState<any>(null);
  const [retention, setRetention] = useState<any>(null);
  const [deep, setDeep] = useState<any>(null);
  const [top24h, setTop24h] = useState<any>(null);
  const [categories, setCategories] = useState<any>(null);
  const [profiles, setProfiles] = useState<any>(null);
  const [funnel, setFunnel] = useState<any>(null);
  const [devAccts, setDevAccts] = useState<any>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [o, s, r, d, t, c, p, f, da] = await Promise.all([
        fetchJson("/admin/analytics/overview", adminKey),
        fetchJson("/admin/analytics/active-users?days=30", adminKey),
        fetchJson("/admin/analytics/retention", adminKey),
        fetchJson("/admin/analytics/deep-action-rate?days=7", adminKey),
        fetchJson("/admin/analytics/top-feuds-24h", adminKey),
        fetchJson("/admin/analytics/categories", adminKey),
        fetchJson("/admin/analytics/profiles", adminKey),
        fetchJson("/admin/analytics/funnel", adminKey),
        fetchJson("/admin/analytics/dev-accounts", adminKey),
      ]);
      setOverview(o); setSeries(s); setRetention(r); setDeep(d);
      setTop24h(t); setCategories(c); setProfiles(p); setFunnel(f);
      setDevAccts(da);
    } catch (e: any) {
      setError(e?.message || "Errore");
    } finally { setLoading(false); }
  }, [adminKey]);

  useEffect(() => { load(); }, [load]);

  useImperativeHandle(ref, () => ({
    reload: load,
    snapshot: () => ({
      overview, series, retention, deep, top24h,
      categories, profiles, funnel, devAccts,
    }),
  }), [load, overview, series, retention, deep, top24h, categories, profiles, funnel, devAccts]);

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={colors.brandPrimary} />
      </View>
    );
  }
  if (error) {
    return (
      <View style={styles.centered}>
        <Text style={styles.err}>{error}</Text>
        <Pressable onPress={load} style={styles.retryBtn}>
          <Text style={styles.retryTxt}>RIPROVA</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <ScrollView contentContainerStyle={styles.content}>
      {overview?.baseline_at ? (
        <Text style={styles.baselineHint}>
          Baseline azzeramento: {new Date(overview.baseline_at).toLocaleString("it-IT")}
        </Text>
      ) : null}

      {/* KPI cards */}
      <View style={styles.kpiGrid}>
        <KpiCard
          label="RITORNO A 30 GIORNI"
          value={retention?.overall_d30_pct}
          suffix="%"
          threshold={THRESH_D30}
          note={retention?.overall_d30_pct == null ? "cohort non ancora mature" : undefined}
        />
        <KpiCard
          label="WAU / MAU"
          value={overview?.active_users?.wau_mau_ratio_pct}
          suffix="%"
          threshold={THRESH_WAU_MAU}
        />
        <KpiCard
          label="AZIONE PROFONDA (7g)"
          value={deep?.pct}
          suffix="%"
          threshold={THRESH_DEEP}
          note={`${deep?.deep_action_users ?? 0}/${deep?.active ?? 0} utenti attivi hanno votato o commentato`}
        />
        <KpiCard
          label="MEDIANA VOTI 24h"
          value={top24h?.median_votes_first_24h}
          threshold={THRESH_TOP24H}
          note={`campione: ${top24h?.sample_size ?? 0} faide`}
        />
      </View>

      {/* Overview counters */}
      <Section title="PANORAMICA" hint="Numeri assoluti (dev esclusi)">
        <MiniRow items={[
          { label: "Utenti totali", value: overview?.users?.total ?? 0 },
          { label: "Registrati", value: overview?.users?.registered ?? 0 },
          { label: "Anonimi", value: overview?.users?.anonymous ?? 0 },
        ]}/>
        <MiniRow items={[
          { label: "Signup 24h", value: overview?.users?.signups_24h ?? 0 },
          { label: "Signup 7g", value: overview?.users?.signups_7d ?? 0 },
          { label: "Signup 30g", value: overview?.users?.signups_30d ?? 0 },
        ]}/>
        <MiniRow items={[
          { label: "DAU", value: overview?.active_users?.dau ?? 0 },
          { label: "WAU", value: overview?.active_users?.wau ?? 0 },
          { label: "MAU", value: overview?.active_users?.mau ?? 0 },
        ]}/>
        <MiniRow items={[
          { label: "Voti totali", value: overview?.engagement?.total_votes ?? 0 },
          { label: "Voti 24h", value: overview?.engagement?.votes_24h ?? 0 },
          { label: "Voti 7g", value: overview?.engagement?.votes_7d ?? 0 },
        ]}/>
        <MiniRow items={[
          { label: "Commenti tot.", value: overview?.engagement?.total_comments ?? 0 },
          { label: "Commenti 24h", value: overview?.engagement?.comments_24h ?? 0 },
          { label: "Commenti 7g", value: overview?.engagement?.comments_7d ?? 0 },
        ]}/>
      </Section>

      {/* DAU trend */}
      <Section title="UTENTI ATTIVI GIORNALIERI (30g)">
        <MiniLineChart series={series?.series || []} />
      </Section>

      {/* Retention cohorts */}
      <Section title="RETENTION PER COORTE"
        hint="Cohort settimanali degli ultimi 90 giorni. — = non ancora rilevabile.">
        {(!retention?.cohorts || retention.cohorts.length === 0) ? (
          <Text style={styles.emptyTxt}>Nessuna coorte disponibile.</Text>
        ) : (
          <View style={styles.cohortTable}>
            <View style={styles.cohortHead}>
              <Text style={[styles.cohortCell, styles.cohortHeadTxt, { flex: 1.4 }]}>COORTE</Text>
              <Text style={[styles.cohortCell, styles.cohortHeadTxt]}>N.</Text>
              <Text style={[styles.cohortCell, styles.cohortHeadTxt]}>D1</Text>
              <Text style={[styles.cohortCell, styles.cohortHeadTxt]}>D7</Text>
              <Text style={[styles.cohortCell, styles.cohortHeadTxt]}>D30</Text>
            </View>
            {retention.cohorts.map((c: any) => (
              <View style={styles.cohortRow} key={c.cohort}>
                <Text style={[styles.cohortCell, { flex: 1.4 }]}>{c.cohort}</Text>
                <Text style={styles.cohortCell}>{c.size}</Text>
                <Text style={styles.cohortCell}>{c.d1_pct == null ? "—" : `${c.d1_pct}%`}</Text>
                <Text style={styles.cohortCell}>{c.d7_pct == null ? "—" : `${c.d7_pct}%`}</Text>
                <Text style={styles.cohortCell}>{c.d30_pct == null ? "—" : `${c.d30_pct}%`}</Text>
              </View>
            ))}
          </View>
        )}
      </Section>

      {/* Categorie */}
      <Section title="CATEGORIE PIÙ FREQUENTATE"
        hint="Voti, visualizzazioni, commenti, utenti attivi per categoria.">
        {(!categories?.categories || categories.categories.length === 0) ? (
          <Text style={styles.emptyTxt}>Nessun dato.</Text>
        ) : (
          <View style={styles.catBlock}>
            {categories.categories.map((c: any) => {
              const maxVotes = Math.max(1, ...categories.categories.map((x: any) => x.votes || 0));
              const pct = Math.round(100 * (c.votes || 0) / maxVotes);
              return (
                <View style={styles.catRow} key={c.category}>
                  <Text style={styles.catLabel}>{(c.category || "n/d").toUpperCase()}</Text>
                  <View style={styles.catTrack}>
                    <View style={[styles.catFill, { width: `${Math.max(pct, 3)}%` }]} />
                  </View>
                  <Text style={styles.catCounters}>
                    {c.votes} voti · {c.views} view · {c.comments} cmt · {c.active_users} utenti
                  </Text>
                </View>
              );
            })}
          </View>
        )}
      </Section>

      {/* Funnel */}
      <Section title="FUNNEL DI CONVERSIONE (30g)"
        hint="% di utenti registrati negli ultimi 30 giorni che hanno completato ogni step.">
        <MiniRow items={[
          { label: "Signup", value: funnel?.signups ?? 0 },
          { label: "→ Voto", value: `${funnel?.with_vote ?? 0} (${funnel?.with_vote_pct ?? 0}%)` },
          { label: "→ Commento", value: `${funnel?.with_comment ?? 0} (${funnel?.with_comment_pct ?? 0}%)` },
        ]}/>
      </Section>

      {/* Profili */}
      <Section title="STATISTICHE PROFILI"
        hint="Composizione dei profili non-dev.">
        <MiniRow items={[
          { label: "Con foto", value: `${profiles?.with_photo_pct ?? 0}%` },
          { label: "Con bio", value: `${profiles?.with_bio_pct ?? 0}%` },
          { label: "Con display name", value: `${profiles?.with_display_name_pct ?? 0}%` },
        ]}/>
        <MiniRow items={[
          { label: "Con cerchia >0", value: `${profiles?.with_circle_pct ?? 0}%` },
          { label: "Onboarded", value: `${profiles?.onboarded_pct ?? 0}%` },
          { label: "Push attive", value: `${profiles?.push_enabled_pct ?? 0}%` },
        ]}/>
        <MiniRow items={[
          { label: "Cerchia media", value: profiles?.avg_circle_size ?? 0 },
          { label: "Totale profili", value: profiles?.total ?? 0 },
        ]}/>
        <SubTitle title="Provider auth" />
        <BreakdownList data={profiles?.auth_providers || {}} />
        <SubTitle title="Fasce d'età" />
        <BreakdownList data={profiles?.ages || {}} />
        <SubTitle title="Sesso" />
        <BreakdownList data={profiles?.sex || {}} />
        <SubTitle title="Prime 15 regioni" />
        {(profiles?.regions || []).map((r: any) => (
          <View key={r.region} style={styles.kvRow}>
            <Text style={styles.kvKey}>{r.region === "unknown" ? "Sconosciuta" : r.region}</Text>
            <Text style={styles.kvVal}>{r.count}</Text>
          </View>
        ))}
      </Section>

      {/* Top faide 24h */}
      <Section title="TOP FAIDE (voti nelle prime 24h)"
        hint="Ranking basato sui voti raccolti entro 24h dalla pubblicazione (ultimi 30 giorni).">
        {(!top24h?.top || top24h.top.length === 0) ? (
          <Text style={styles.emptyTxt}>Nessun dato ancora.</Text>
        ) : top24h.top.map((r: any) => (
          <View key={r.feud_id} style={styles.topRow}>
            <Text style={styles.topCat}>{(r.category_label || "").toUpperCase()}</Text>
            <Text style={styles.topTitle} numberOfLines={2}>{r.title}</Text>
            <Text style={styles.topVotes}>{r.votes_first_24h} voti</Text>
          </View>
        ))}
      </Section>

      {/* Dev accounts */}
      <Section title="ACCOUNT ESCLUSI (dev)"
        hint="Questi utenti sono esclusi da ogni metrica. Configura via DEV_ACCOUNT_EMAILS.">
        {(!devAccts?.dev_accounts || devAccts.dev_accounts.length === 0) ? (
          <Text style={styles.emptyTxt}>Nessun account di sviluppo.</Text>
        ) : devAccts.dev_accounts.map((u: any) => (
          <View key={u.user_id} style={styles.devRow}>
            <Text style={styles.devEmail}>{u.email || "—"}</Text>
            <Text style={styles.devMeta}>@{u.nickname} · {u.auth_provider} · {u.user_id}</Text>
          </View>
        ))}
      </Section>
    </ScrollView>
  );
});

// ─── Sub-components ────────────────────────────────────────────────
function KpiCard({ label, value, suffix, threshold, note }: {
  label: string; value: number | null | undefined; suffix?: string; threshold: Threshold; note?: string;
}) {
  const c = statusColor(value, threshold);
  const s = statusLabel(value, threshold);
  return (
    <View style={[styles.kpiCard, { borderColor: c }]}>
      <Text style={styles.kpiLabel}>{label}</Text>
      <Text style={[styles.kpiValue, { color: c }]}>
        {value == null ? "—" : `${value}${suffix || ""}`}
      </Text>
      <Text style={[styles.kpiStatus, { color: c }]}>{s}</Text>
      {note ? <Text style={styles.kpiNote}>{note}</Text> : null}
    </View>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {hint ? <Text style={styles.sectionHint}>{hint}</Text> : null}
      <View style={{ gap: spacing.sm, marginTop: spacing.xs }}>{children}</View>
    </View>
  );
}

function SubTitle({ title }: { title: string }) {
  return <Text style={styles.subTitle}>{title}</Text>;
}

function MiniRow({ items }: { items: { label: string; value: number | string }[] }) {
  return (
    <View style={styles.miniRow}>
      {items.map((it) => (
        <View key={it.label} style={styles.miniCell}>
          <Text style={styles.miniVal}>{it.value}</Text>
          <Text style={styles.miniLbl}>{it.label}</Text>
        </View>
      ))}
    </View>
  );
}

function BreakdownList({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data || {}).sort((a, b) => (b[1] as number) - (a[1] as number));
  const total = entries.reduce((s, [, n]) => s + Number(n || 0), 0);
  if (!total) return <Text style={styles.emptyTxt}>Nessun dato.</Text>;
  return (
    <View style={{ gap: 4 }}>
      {entries.map(([k, n]) => {
        const pct = Math.round(100 * Number(n) / total);
        return (
          <View style={styles.kvRow} key={k}>
            <Text style={styles.kvKey}>{k}</Text>
            <Text style={styles.kvVal}>{n} ({pct}%)</Text>
          </View>
        );
      })}
    </View>
  );
}

function MiniLineChart({ series }: { series: { date: string; dau: number }[] }) {
  if (!series || series.length === 0) {
    return <Text style={styles.emptyTxt}>Serie temporale non ancora disponibile.</Text>;
  }
  const max = Math.max(1, ...series.map(s => s.dau));
  return (
    <View style={styles.lineChart}>
      {series.slice(-30).map((s) => {
        const h = Math.max(3, Math.round((s.dau / max) * 80));
        return (
          <View key={s.date} style={styles.lineChartBar}>
            <View style={[styles.lineChartFill, { height: h }]} />
          </View>
        );
      })}
      <View style={styles.lineChartFooter}>
        <Text style={styles.lineChartMeta}>max {max} · ultimo {series[series.length - 1]?.dau ?? 0}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xxxl },
  centered: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl, gap: spacing.md },
  err: { color: colors.error, borderWidth: 2, borderColor: colors.error, padding: spacing.sm, fontSize: font.sizes.base },
  retryBtn: { borderWidth: 2, borderColor: colors.border, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, backgroundColor: colors.surfaceInverse },
  retryTxt: { color: colors.onSurfaceInverse, fontSize: font.sizes.sm, letterSpacing: 2, fontWeight: "500" },
  headerRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  moduleTitle: { fontSize: font.sizes.xl, letterSpacing: 2, fontWeight: "500", color: colors.onSurface },
  moduleHint: { fontSize: font.sizes.xs, color: colors.muted, lineHeight: 16 },
  baselineHint: { fontSize: font.sizes.xs, color: colors.muted, fontStyle: "italic" },
  iconBtn: { width: 36, height: 36, borderWidth: 2, borderColor: colors.brandSecondary, alignItems: "center", justifyContent: "center" },
  kpiGrid: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm },
  kpiCard: { width: "48%", borderWidth: 2, padding: spacing.sm, backgroundColor: colors.surfaceSecondary, gap: 2, minHeight: 108 },
  kpiLabel: { fontSize: font.sizes.xs, letterSpacing: 1, color: colors.muted },
  kpiValue: { fontSize: 28, fontWeight: "500", lineHeight: 32 },
  kpiStatus: { fontSize: font.sizes.xs, letterSpacing: 2, fontWeight: "500" },
  kpiNote: { fontSize: 10, color: colors.muted, marginTop: 2 },
  section: { borderWidth: 2, borderColor: colors.border, padding: spacing.md, backgroundColor: colors.surfaceSecondary, gap: spacing.xs },
  sectionTitle: { fontSize: font.sizes.lg, letterSpacing: 2, fontWeight: "500", color: colors.onSurface },
  sectionHint: { fontSize: font.sizes.xs, color: colors.muted, lineHeight: 15 },
  subTitle: { fontSize: font.sizes.sm, letterSpacing: 1.5, color: colors.brandPrimary, marginTop: spacing.sm },
  miniRow: { flexDirection: "row", gap: spacing.sm },
  miniCell: { flex: 1, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface, padding: spacing.xs, alignItems: "center" },
  miniVal: { fontSize: font.sizes.lg, fontWeight: "500", color: colors.onSurface },
  miniLbl: { fontSize: 10, letterSpacing: 1, color: colors.muted, textAlign: "center" },
  emptyTxt: { fontSize: font.sizes.sm, color: colors.muted, fontStyle: "italic" },
  cohortTable: { gap: 2 },
  cohortHead: { flexDirection: "row", backgroundColor: colors.surfaceInverse, paddingVertical: 4 },
  cohortHeadTxt: { color: colors.onSurfaceInverse, letterSpacing: 1.5, fontWeight: "500" },
  cohortRow: { flexDirection: "row", paddingVertical: 4, borderBottomWidth: 1, borderColor: colors.border },
  cohortCell: { flex: 1, fontSize: font.sizes.xs, color: colors.onSurface, textAlign: "center" },
  catBlock: { gap: spacing.xs },
  catRow: { gap: 2 },
  catLabel: { fontSize: font.sizes.xs, letterSpacing: 1.5, color: colors.brandPrimary },
  catTrack: { height: 12, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border },
  catFill: { height: "100%", backgroundColor: colors.brandPrimary },
  catCounters: { fontSize: 10, color: colors.muted },
  kvRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingVertical: 2 },
  kvKey: { fontSize: font.sizes.sm, color: colors.onSurface },
  kvVal: { fontSize: font.sizes.sm, color: colors.muted, fontWeight: "500" },
  topRow: { paddingVertical: spacing.xs, borderBottomWidth: 1, borderColor: colors.border },
  topCat: { fontSize: font.sizes.xs, letterSpacing: 1.5, color: colors.brandPrimary },
  topTitle: { fontSize: font.sizes.sm, color: colors.onSurface, lineHeight: 17 },
  topVotes: { fontSize: font.sizes.xs, color: colors.muted, marginTop: 2 },
  devRow: { paddingVertical: 4, borderBottomWidth: 1, borderColor: colors.border },
  devEmail: { fontSize: font.sizes.sm, color: colors.onSurface, fontWeight: "500" },
  devMeta: { fontSize: 10, color: colors.muted, marginTop: 1 },
  lineChart: { flexDirection: "row", alignItems: "flex-end", gap: 2, height: 100, marginTop: spacing.xs },
  lineChartBar: { flex: 1, justifyContent: "flex-end" },
  lineChartFill: { width: "100%", backgroundColor: colors.brandPrimary, minHeight: 3 },
  lineChartFooter: { position: "absolute", right: 0, bottom: -18 },
  lineChartMeta: { fontSize: 10, color: colors.muted },
});
