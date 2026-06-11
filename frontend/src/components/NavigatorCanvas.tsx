// ─── NavigatorCanvas ─────────────────────────────────────────────────────────
// Config-driven canvas that renders any Navigator Intelligence Config persona.
//
// Layout (per persona):
//   ┌──────────────────────────────────────────────────────────┐
//   │  [Summary card 1]  [Summary card 2]  [Summary card 3]   │  ← AI-written
//   ├──────────────────────────────────────────────────────────┤
//   │  Now · 7D · 30D                            (period bar) │
//   │  Section title                                           │
//   │  [ KpiTile ]  [ KpiTile ]  [ KpiTile ]                  │  ← compact grid
//   └──────────────────────────────────────────────────────────┘
//
// Clicking a tile opens a KpiModal with the full chart + insights.
// Zero hardcoded domain knowledge — everything comes from the config.

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useChartTheme } from "../context/ChartThemeContext";
import { NavigatorKpiChart, canBuildKpiChart } from "./NavigatorKpiChart";
import { mergeWorkbookDimensionLabels, workbookDimensionLabels } from "./NavigatorKpiCard";
import { CHART_FONT, CHART_NUM_FONT, translucent } from "./charts/chartTheme";
import { api } from "../api/client";
import type { NavigatorPersona, NavigatorSummaryCard, NavigatorActionItem, NavigatorKpiDrivers, NavigatorKPI } from "../types/navigator";
import {
  computeL1Value,
  kpiSupportsPeriod,
  l1MatchesConfig,
  personaSupportsPeriod,
  resolvePeriodHeadline,
} from "../lib/metricCompute";
import { formatKpiDescription } from "../lib/kpiDisplay";

// ── Module-level row cache ────────────────────────────────────────────────────
// Keyed by "workbookId::viewName". Persists across component mounts and persona
// switches so switching tabs or opening a modal never re-fetches data that was
// already loaded this session.
//
// TTL matches the backend Hyper cache (1 hour). After 1 hour the next tile mount
// will re-fetch from the API, which will itself re-download the Hyper extract if
// Tableau data has changed since then.
//
// Call clearRowCache(workbookId) to force-expire all entries for a workbook
// (used by NavigatorApp when the freshness hook detects a Tableau data update).
const _ROW_CACHE_TTL_MS = 60 * 60 * 1000; // 1 hour

interface CacheEntry {
  rows: Record<string, unknown>[];
  at:   number;  // Date.now() at store time
}
const _rowCache = new Map<string, CacheEntry>();

function _cacheKey(workbookId: string, viewName: string): string {
  return `${workbookId}::${viewName}`;
}

function _getCached(workbookId: string, viewName: string): Record<string, unknown>[] | undefined {
  const entry = _rowCache.get(_cacheKey(workbookId, viewName));
  if (!entry) return undefined;
  if (Date.now() - entry.at > _ROW_CACHE_TTL_MS) {
    _rowCache.delete(_cacheKey(workbookId, viewName));
    return undefined;
  }
  return entry.rows;
}

function _setCached(workbookId: string, viewName: string, rows: Record<string, unknown>[]): void {
  _rowCache.set(_cacheKey(workbookId, viewName), { rows, at: Date.now() });
}

/** Force-expire all cached rows for a workbook (call on Tableau data refresh). */
export function clearRowCache(workbookId: string): void {
  const prefix = `${workbookId}::`;
  for (const key of _rowCache.keys()) {
    if (key.startsWith(prefix)) _rowCache.delete(key);
  }
}

// ── Period type ───────────────────────────────────────────────────────────────

export type Period = "now" | "7d" | "30d";

const PERIOD_OPTIONS: { key: Period; label: string }[] = [
  { key: "now", label: "Now"  },
  { key: "7d",  label: "7D"   },
  { key: "30d", label: "30D"  },
];

// ── Grid layout helper ────────────────────────────────────────────────────────

function colsForCount(_count: number): number {
  return 12; // kept for any future usage; grid handled by CSS now
}
// suppress unused-var warning — colsForCount is intentionally kept per spec
void colsForCount;

// ── Shared data utilities (mirrored from NavigatorKpiCard) ────────────────────

function norm(s: string): string {
  return String(s).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function findColumn(rows: Record<string, unknown>[], hint: string): string | null {
  if (!rows.length || !hint) return null;
  const cols = Object.keys(rows[0]);
  const h = norm(hint);

  const exact = cols.find((c) => norm(c) === h);
  if (exact) return exact;

  const sub = cols.find((c) => norm(c).includes(h) || h.includes(norm(c)));
  if (sub) return sub;

  const words = h.split(/\s+/).filter((w) => w.length > 2);
  if (!words.length) return null;
  const minMatches = Math.min(2, words.length);
  const scored = cols.reduce<{ col: string; matches: number }[]>((acc, c) => {
    const matches = words.filter((w) => norm(c).includes(w)).length;
    if (matches >= minMatches) acc.push({ col: c, matches });
    return acc;
  }, []);
  if (!scored.length) return null;
  scored.sort((a, b) => b.matches - a.matches);
  return scored[0].col;
}

function parseNum(v: unknown): number | null {
  if (v === null || v === undefined) return null;
  if (typeof v === "number") return v;
  const s = String(v)
    .replace(/[$€£¥₹₩]/g, "")
    .replace(/,/g, "")
    .replace(/%$/, "")
    .trim();
  if (s.startsWith("(") && s.endsWith(")")) {
    const n = parseFloat(s.slice(1, -1));
    return isNaN(n) ? null : -n;
  }
  const n = parseFloat(s);
  return isNaN(n) ? null : n;
}

function aggregateField(rows: Record<string, unknown>[], col: string, agg: string): number | null {
  if (agg === "count") {
    const nonNull = rows.filter((r) => r[col] !== null && r[col] !== undefined && r[col] !== "");
    return nonNull.length > 0 ? nonNull.length : null;
  }
  const vals = rows.map((r) => parseNum(r[col])).filter((v): v is number => v !== null);
  if (!vals.length) return null;
  switch (agg) {
    case "avg": return vals.reduce((a, b) => a + b, 0) / vals.length;
    default:    return vals.reduce((a, b) => a + b, 0);
  }
}

export function parseRowDate(val: unknown): Date | null {
  if (val === null || val === undefined) return null;
  const s = String(val).trim();
  if (!s) return null;

  if (/^\d{4}-\d{2}-\d{2}/.test(s)) {
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }

  const ym = s.match(/^(\d{4})[/-](\d{1,2})$/);
  if (ym) return new Date(+ym[1], +ym[2] - 1, 1);

  const MONTHS: Record<string, number> = {
    jan:1, feb:2, mar:3, apr:4, may:5, jun:6,
    jul:7, aug:8, sep:9, oct:10, nov:11, dec:12,
  };
  const my = s.match(/([A-Za-z]{3})[a-z]*[\s-]+(\d{4})/);
  if (my) {
    const m = MONTHS[my[1].toLowerCase()];
    if (m) return new Date(+my[2], m - 1, 1);
  }

  const q = s.match(/Q(\d)\s+(\d{4})/i);
  if (q) return new Date(+q[2], (+q[1] - 1) * 3, 1);

  const yr = s.match(/^(\d{4})$/);
  if (yr) return new Date(+yr[1], 0, 1);

  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

function computeDateSpanDays(rows: Record<string, unknown>[], dateCol: string): number | null {
  const dates = rows
    .map((r) => parseRowDate(r[dateCol]))
    .filter((d): d is Date => d !== null)
    .map((d) => d.getTime());
  if (dates.length < 2) return null;
  const min = Math.min(...dates);
  const max = Math.max(...dates);
  const spanDays = (max - min) / 86_400_000;
  return spanDays > 0 ? spanDays : null;
}

// L2 projection math lives in lib/metricCompute.ts (mirrors pipeline/metric_contract.py).

function formatL1(value: number | null, unit: string): string {
  if (value === null || value === undefined) return "—";
  const abs = Math.abs(value);
  let formatted: string;

  if (abs >= 1_000_000) {
    formatted = (value / 1_000_000).toFixed(1) + "M";
  } else if (abs >= 1_000) {
    formatted = value.toLocaleString(undefined, { maximumFractionDigits: 0 });
  } else {
    formatted = value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }

  if (unit === "USD") return `$${formatted}`;
  if (unit === "%")   return `${formatted}%`;
  if (unit)           return `${formatted} ${unit}`;
  return formatted;
}

// ── Period bar ────────────────────────────────────────────────────────────────

type Palette = ReturnType<typeof import("./charts/chartTheme").getChartPalette>;

function PeriodBar({
  period,
  onChange,
  palette,
}: {
  period:   Period;
  onChange: (p: Period) => void;
  palette:  Palette;
}) {
  return (
    <div style={{
      display: "inline-flex",
      background: palette.bg2,
      border: `1px solid ${palette.line}`,
      borderRadius: 6,
      padding: 2,
      gap: 2,
    }}>
      {PERIOD_OPTIONS.map(({ key, label }) => {
        const active = period === key;
        return (
          <button
            key={key}
            type="button"
            onClick={() => onChange(key)}
            style={{
              fontFamily:  CHART_NUM_FONT,
              fontSize:    12,
              fontWeight:  active ? 700 : 500,
              padding:     "4px 12px",
              borderRadius: 4,
              border:      "none",
              cursor:      "pointer",
              background:  active ? palette.bg1 : "transparent",
              color:       active ? palette.ink  : palette.ink4,
              boxShadow:   active ? "0 1px 3px rgba(0,0,0,0.08)" : "none",
              transitionProperty: "background-color, color, box-shadow",
          transitionDuration: "0.15s",
              letterSpacing: "0.02em",
            }}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

// ── Briefing card (full 3-part: what to care about / why / action) ────────────

function BriefingCard({
  card,
  action,
  onActionClick,
  palette,
}: {
  card:           NavigatorSummaryCard;
  action:         NavigatorActionItem | null;
  onActionClick?: (kpiName: string) => void;
  palette:        Palette;
}) {
  const [hovered, setHovered] = useState(false);

  const accentColor =
    card.signal === "positive" ? palette.green :
    card.signal === "warning"  ? palette.red   :
    palette.accent;

  const signalLabel =
    card.signal === "positive" ? "Positive" :
    card.signal === "warning"  ? "Watch"    :
    "Insight";

  const actionColor =
    !action               ? palette.ink4 :
    action.signal === "critical" ? palette.red  :
    action.signal === "watch"    ? (palette.amber ?? "#F0C040") :
    palette.green;

  const clickable = !!(action && onActionClick);

  return (
    <div
      onClick={clickable ? () => onActionClick!(action!.kpi_name) : undefined}
      onMouseEnter={clickable ? () => setHovered(true)  : undefined}
      onMouseLeave={clickable ? () => setHovered(false) : undefined}
      style={{
        flex: "1 1 200px",
        minWidth: 180,
        maxWidth: 280,
        borderRadius: 6,
        padding: "8px 10px",
        display: "flex",
        flexDirection: "column",
        gap: 7,
        background: `${accentColor}${hovered ? "18" : "10"}`,
        borderTop: `1px solid ${accentColor}${hovered ? "40" : "2A"}`,
        borderRight: `1px solid ${accentColor}${hovered ? "40" : "2A"}`,
        borderBottom: `1px solid ${accentColor}${hovered ? "40" : "2A"}`,
        borderLeft: `3px solid ${accentColor}`,
        cursor: clickable ? "pointer" : "default",
        transition: "background 0.12s, border-color 0.12s",
        animation: "summaryIn 0.32s ease both",
      }}
    >
      {/* Signal indicator */}
      <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
        <span style={{
          width: 6, height: 6, borderRadius: "50%",
          background: accentColor, flexShrink: 0, display: "inline-block",
        }} />
        <span style={{
          fontFamily: CHART_NUM_FONT, fontSize: 9, fontWeight: 700,
          color: accentColor, letterSpacing: "0.1em", textTransform: "uppercase",
        }}>
          {signalLabel}
        </span>
      </div>

      {/* What to care about */}
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <span style={{
          fontFamily: CHART_NUM_FONT, fontSize: 8, fontWeight: 700,
          color: palette.ink4, textTransform: "uppercase", letterSpacing: "0.08em",
        }}>
          What to care about
        </span>
        <span style={{
          fontFamily: CHART_FONT, fontSize: 11, fontWeight: 700,
          color: palette.ink, lineHeight: 1.3,
        }}>
          {card.title}
        </span>
      </div>

      {/* Why it matters */}
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <span style={{
          fontFamily: CHART_NUM_FONT, fontSize: 8, fontWeight: 700,
          color: palette.ink4, textTransform: "uppercase", letterSpacing: "0.08em",
        }}>
          Why it matters
        </span>
        <p style={{
          fontFamily: CHART_FONT, fontSize: 10,
          color: palette.ink3, lineHeight: 1.45, margin: 0,
          display: "-webkit-box",
          WebkitLineClamp: 3,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}>
          {card.body}
        </p>
      </div>

      {/* What action to take */}
      {action && (
        <div style={{
          display: "flex", flexDirection: "column", gap: 2,
          borderTop: `1px solid ${palette.line}`,
          paddingTop: 6, marginTop: 1,
        }}>
          <span style={{
            fontFamily: CHART_NUM_FONT, fontSize: 8, fontWeight: 700,
            color: actionColor, textTransform: "uppercase", letterSpacing: "0.08em",
          }}>
            What action to take
          </span>
          <p style={{
            fontFamily: CHART_FONT, fontSize: 10,
            color: palette.ink, lineHeight: 1.45, margin: 0,
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}>
            {action.action}
          </p>
        </div>
      )}
    </div>
  );
}

// ── Daily Summary header (full-width horizontal-scroll strip) ──────────────────

function BriefingHeader({
  summaryCards,
  actionItems,
  onActionClick,
  palette,
}: {
  summaryCards:   NavigatorSummaryCard[];
  actionItems:    NavigatorActionItem[];
  onActionClick?: (kpiName: string) => void;
  palette:        Palette;
}) {
  const CARD_ORDER: Record<NavigatorSummaryCard["signal"], number> = {
    warning: 0, neutral: 1, positive: 2,
  };
  const sortedCards = [...summaryCards].sort((a, b) => CARD_ORDER[a.signal] - CARD_ORDER[b.signal]);

  // Match each briefing card to the most relevant action item by keyword overlap.
  // Each action item is used at most once (greedy first-match).
  const usedActionIdx = new Set<number>();
  const cardActions: (NavigatorActionItem | null)[] = sortedCards.map((card) => {
    const normStr = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ");
    const cardText = normStr(card.title + " " + card.body.slice(0, 80));
    let best = -1, bestScore = 0;
    actionItems.forEach((a, i) => {
      if (usedActionIdx.has(i)) return;
      const words = normStr(a.kpi_name).split(" ").filter((w) => w.length > 3);
      const score = words.filter((w) => cardText.includes(w)).length;
      if (score > bestScore) { bestScore = score; best = i; }
    });
    // Fallback: first unused action item so no card is left action-less
    if (best === -1) {
      for (let i = 0; i < actionItems.length; i++) {
        if (!usedActionIdx.has(i)) { best = i; break; }
      }
    }
    if (best >= 0) { usedActionIdx.add(best); return actionItems[best]; }
    return null;
  });

  return (
    <div style={{
      background: palette.bg1,
      border: `1px solid ${palette.line}`,
      borderRadius: 8,
      padding: "8px 10px",
      display: "flex",
      flexDirection: "column",
      gap: 6,
      flexShrink: 0,
    }}>
      {/* Strip label */}
      <div style={{
        fontFamily: CHART_NUM_FONT, fontSize: 9, fontWeight: 700,
        letterSpacing: "0.1em", textTransform: "uppercase",
        color: palette.ink4,
        display: "flex", alignItems: "center", gap: 6, flexShrink: 0,
      }}>
        Daily Summary
        <span style={{
          fontWeight: 400, letterSpacing: 0, textTransform: "none",
          fontSize: 9, color: palette.ink4,
        }}>
          — refreshed with data
        </span>
      </div>

      {/* Horizontally scrollable 3-part cards */}
      <div style={{
        display: "flex", flexDirection: "row", gap: 8,
        overflowX: "auto", paddingBottom: 4,
        scrollbarWidth: "thin",
        scrollbarColor: `${palette.line2} transparent`,
      }}>
        {sortedCards.map((card, i) => (
          <BriefingCard
            key={i + card.title}
            card={card}
            action={cardActions[i]}
            onActionClick={onActionClick}
            palette={palette}
          />
        ))}
      </div>
    </div>
  );
}

// ── KPI Teasers ───────────────────────────────────────────────────────────────
// Compact ~44px visuals for each chart type, rendered inside KpiTile.
// Full chart opens in modal on click. No data fetching — uses pre-fetched rows.

const TEASER_H = 32;
const TEASER_W = 100; // SVG viewBox units

/** Primary accent colour derived from trend direction. */
function teaserColor(kpi: NavigatorKPI, palette: Palette): string {
  return kpi.trend_direction === "up"   ? palette.green
       : kpi.trend_direction === "down" ? palette.red
       : palette.accent;
}

/** Find a numeric column matching hint, or fall back to first numeric column. */
function extractNumericCol(rows: Record<string, unknown>[], hint?: string | null): string | null {
  if (!rows.length) return null;
  const cols = Object.keys(rows[0]);
  if (hint) { const found = findColumn(rows, hint); if (found) return found; }
  return cols.find((c) => rows.slice(0, 5).some((r) => parseNum(r[c]) !== null)) ?? null;
}

// ── 1. Sparkline — line / area / stacked_area ─────────────────────────────────
function SparklineTeaser({
  rows, kpi, palette, filled,
}: { rows: Record<string, unknown>[]; kpi: NavigatorKPI; palette: Palette; filled: boolean }) {
  const color = teaserColor(kpi, palette);
  const pts = useMemo(() => {
    const col = extractNumericCol(rows, kpi.chart?.y_axis);
    if (!col) return null;
    const vals = rows.map((r) => parseNum(r[col])).filter((v): v is number => v !== null);
    if (vals.length < 2) return null;
    const slice = vals.slice(-40);
    const min = Math.min(...slice), max = Math.max(...slice), range = max - min || 1;
    return slice.map((v, i) => ({
      x: (i / (slice.length - 1)) * TEASER_W,
      y: TEASER_H - ((v - min) / range) * (TEASER_H - 5) - 2,
    }));
  }, [rows, kpi]);
  if (!pts) return null;

  const linePoints = pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const areaPath = filled
    ? `M${pts[0].x.toFixed(1)},${pts[0].y.toFixed(1)} ` +
      pts.slice(1).map((p) => `L${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ") +
      ` L${TEASER_W},${TEASER_H} L0,${TEASER_H} Z`
    : null;
  const gradId = `sg-${kpi.id}`;

  return (
    <svg viewBox={`0 0 ${TEASER_W} ${TEASER_H}`} preserveAspectRatio="none"
      style={{ width: "100%", height: TEASER_H, display: "block" }}>
      {filled && (
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stopColor={color} stopOpacity={0.28} />
            <stop offset="100%" stopColor={color} stopOpacity={0.02} />
          </linearGradient>
        </defs>
      )}
      {areaPath && <path d={areaPath} fill={`url(#${gradId})`} />}
      <polyline points={linePoints} fill="none" stroke={color} strokeWidth={1.5}
        strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

// ── 2. Mini vertical bars — bar_chart ────────────────────────────────────────
function MiniBarTeaser({
  rows, kpi, palette,
}: { rows: Record<string, unknown>[]; kpi: NavigatorKPI; palette: Palette }) {
  const color = teaserColor(kpi, palette);
  const bars = useMemo(() => {
    const yCol = extractNumericCol(rows, kpi.chart?.y_axis);
    if (!yCol) return null;
    const xCol = kpi.chart?.x_axis ? findColumn(rows, kpi.chart.x_axis) : null;
    if (xCol) {
      const groups = new Map<string, number>();
      for (const row of rows) {
        const k = String(row[xCol] ?? "");
        const v = parseNum(row[yCol]);
        if (v !== null) groups.set(k, (groups.get(k) ?? 0) + v);
      }
      return [...groups.values()].sort((a, b) => b - a).slice(0, 6);
    }
    return rows.map((r) => parseNum(r[yCol])).filter((v): v is number => v !== null).slice(-6);
  }, [rows, kpi]);
  if (!bars || bars.length < 2) return null;

  const max = Math.max(...bars);
  const gap = 2, barW = (TEASER_W - gap * (bars.length - 1)) / bars.length;
  return (
    <svg viewBox={`0 0 ${TEASER_W} ${TEASER_H}`} preserveAspectRatio="none"
      style={{ width: "100%", height: TEASER_H, display: "block" }}>
      {bars.map((v, i) => {
        const h = (v / max) * (TEASER_H - 2);
        return <rect key={i} x={i * (barW + gap)} y={TEASER_H - h} width={barW} height={h}
          fill={color} opacity={0.4 + (v / max) * 0.55} rx={1} />;
      })}
    </svg>
  );
}

// ── 3. Mini horizontal bars — horizontal_bar_chart ────────────────────────────
function HorizontalBarTeaser({
  rows, kpi, palette,
}: { rows: Record<string, unknown>[]; kpi: NavigatorKPI; palette: Palette }) {
  const color = teaserColor(kpi, palette);
  const bars = useMemo(() => {
    const yCol = extractNumericCol(rows, kpi.chart?.y_axis);
    if (!yCol) return null;
    const xCol = kpi.chart?.x_axis ? findColumn(rows, kpi.chart.x_axis) : null;
    if (xCol) {
      const groups = new Map<string, number>();
      for (const row of rows) {
        const k = String(row[xCol] ?? "");
        const v = parseNum(row[yCol]);
        if (v !== null) groups.set(k, (groups.get(k) ?? 0) + v);
      }
      return [...groups.values()].sort((a, b) => b - a).slice(0, 4);
    }
    return rows.map((r) => parseNum(r[yCol])).filter((v): v is number => v !== null).slice(0, 4);
  }, [rows, kpi]);
  if (!bars || bars.length < 1) return null;

  const max = Math.max(...bars);
  const rowH = Math.floor((TEASER_H - (bars.length - 1) * 3) / bars.length);
  return (
    <svg viewBox={`0 0 ${TEASER_W} ${TEASER_H}`}
      style={{ width: "100%", height: TEASER_H, display: "block" }}>
      {bars.map((v, i) => (
        <rect key={i} x={0} y={i * (rowH + 3)} width={(v / max) * TEASER_W} height={rowH}
          fill={color} opacity={0.4 + (v / max) * 0.55} rx={2} />
      ))}
    </svg>
  );
}

// ── 4. Segmented bar — stacked_bar_chart ──────────────────────────────────────
function SegmentedBarTeaser({
  rows, kpi, palette,
}: { rows: Record<string, unknown>[]; kpi: NavigatorKPI; palette: Palette }) {
  const COLORS = [palette.accent, palette.green, palette.red, palette.amber ?? "#F0A030"];
  const segs = useMemo(() => {
    const yCol = extractNumericCol(rows, kpi.chart?.y_axis);
    if (!yCol) return null;
    const bdCol = (kpi.chart?.breakdown_by ?? kpi.chart?.x_axis)
      ? findColumn(rows, kpi.chart.breakdown_by ?? kpi.chart.x_axis ?? "")
      : null;
    let vals: number[];
    if (bdCol) {
      const groups = new Map<string, number>();
      for (const row of rows) {
        const k = String(row[bdCol] ?? "");
        const v = parseNum(row[yCol]);
        if (v !== null) groups.set(k, (groups.get(k) ?? 0) + v);
      }
      vals = [...groups.values()].sort((a, b) => b - a).slice(0, 4);
    } else {
      vals = rows.map((r) => parseNum(r[yCol])).filter((v): v is number => v !== null).slice(0, 4);
    }
    const total = vals.reduce((a, b) => a + b, 0);
    if (!total || vals.length < 2) return null;
    return vals.map((v, i) => ({ pct: v / total, color: COLORS[i % COLORS.length] }));
  }, [rows, kpi, palette]);
  if (!segs) return null;

  return (
    <div style={{ display: "flex", width: "100%", height: 12, borderRadius: 3,
      overflow: "hidden", marginTop: 18, gap: 1 }}>
      {segs.map((s, i) => (
        <div key={i} style={{ flex: s.pct, background: s.color, opacity: 0.8,
          minWidth: s.pct > 0.02 ? 2 : 0 }} />
      ))}
    </div>
  );
}

// ── 5. Mini pie / donut ───────────────────────────────────────────────────────
function MiniPieTeaser({
  rows, kpi, palette, isDonut,
}: { rows: Record<string, unknown>[]; kpi: NavigatorKPI; palette: Palette; isDonut: boolean }) {
  const COLORS = [palette.accent, palette.green, palette.red, palette.amber ?? "#F0A030"];
  const SZ = 44, CX = 22, CY = 22, R = 19, HOLE = 10;

  const slices = useMemo(() => {
    const yCol = extractNumericCol(rows, kpi.chart?.y_axis);
    if (!yCol) return null;
    const lHint = kpi.chart?.x_axis ?? kpi.chart?.breakdown_by;
    const lCol  = lHint ? findColumn(rows, lHint) : null;
    let vals: number[];
    if (lCol) {
      const groups = new Map<string, number>();
      for (const row of rows) {
        const k = String(row[lCol] ?? "");
        const v = parseNum(row[yCol]);
        if (v !== null && v >= 0) groups.set(k, (groups.get(k) ?? 0) + v);
      }
      vals = [...groups.values()].sort((a, b) => b - a).slice(0, 4);
    } else {
      vals = rows.map((r) => parseNum(r[yCol]))
        .filter((v): v is number => v !== null && v >= 0).slice(0, 4);
    }
    const total = vals.reduce((a, b) => a + b, 0);
    if (!total || vals.length < 2) return null;
    return vals.map((v, i) => ({ pct: v / total, color: COLORS[i % COLORS.length] }));
  }, [rows, kpi, palette]);
  if (!slices) return null;

  const pieData: { d: string; color: string }[] = [];
  let angle = -Math.PI / 2;
  for (const s of slices) {
    const sweep = s.pct * 2 * Math.PI;
    const x1 = CX + R * Math.cos(angle),           y1 = CY + R * Math.sin(angle);
    const x2 = CX + R * Math.cos(angle + sweep),   y2 = CY + R * Math.sin(angle + sweep);
    const large = sweep > Math.PI ? 1 : 0;
    let d: string;
    if (isDonut) {
      const ix1 = CX + HOLE * Math.cos(angle),         iy1 = CY + HOLE * Math.sin(angle);
      const ix2 = CX + HOLE * Math.cos(angle + sweep), iy2 = CY + HOLE * Math.sin(angle + sweep);
      d = `M${x1.toFixed(2)},${y1.toFixed(2)} A${R},${R} 0 ${large},1 ${x2.toFixed(2)},${y2.toFixed(2)} L${ix2.toFixed(2)},${iy2.toFixed(2)} A${HOLE},${HOLE} 0 ${large},0 ${ix1.toFixed(2)},${iy1.toFixed(2)} Z`;
    } else {
      d = `M${CX},${CY} L${x1.toFixed(2)},${y1.toFixed(2)} A${R},${R} 0 ${large},1 ${x2.toFixed(2)},${y2.toFixed(2)} Z`;
    }
    pieData.push({ d, color: s.color });
    angle += sweep;
  }

  return (
    <svg viewBox={`0 0 ${SZ} ${SZ}`}
      style={{ width: TEASER_H, height: TEASER_H, display: "block" }}>
      {pieData.map((p, i) => <path key={i} d={p.d} fill={p.color} opacity={0.85} />)}
    </svg>
  );
}

// ── 6. Gauge teaser (compact horizontal bar — fits tile height without clipping) ─
function gaugeFillPct(val: number, unit: string, rows: Record<string, unknown>[], kpi: NavigatorKPI): number {
  if (unit === "%") return Math.min(1, Math.max(0, Math.abs(val) / 100));
  const col = extractNumericCol(rows, kpi.chart?.y_axis);
  if (!col) return 0.5;
  const vals = rows.map((r) => parseNum(r[col])).filter((v): v is number => v !== null);
  const max  = vals.length ? Math.max(...vals, Math.abs(val)) : Math.abs(val);
  return max > 0 ? Math.min(1, Math.max(0, Math.abs(val) / max)) : 0.5;
}

function ArcGaugeTeaser({
  rows, kpi, palette,
}: { rows: Record<string, unknown>[]; kpi: NavigatorKPI; palette: Palette }) {
  const unit = kpi.l1?.unit ?? "";
  const val  = typeof kpi.l1?.value === "number" ? kpi.l1.value : null;
  const pct  = useMemo(
    () => (val !== null ? gaugeFillPct(val, unit, rows, kpi) : 0),
    [rows, kpi, unit, val],
  );
  const isBipolar = unit === "%" && val !== null && val < 0;
  const color = isBipolar ? palette.red : teaserColor(kpi, palette);
  const trackH = 10;
  const bipolarW = val !== null ? Math.min(50, (Math.abs(val) / 100) * 50) : 0;

  return (
    <div style={{
      width: "100%",
      height: TEASER_H,
      display: "flex",
      alignItems: "center",
      paddingTop: 2,
      boxSizing: "border-box",
    }}>
      <div style={{
        position: "relative",
        width: "100%",
        height: trackH,
        borderRadius: trackH / 2,
        background: palette.bg3 ?? palette.line2,
        boxShadow: `inset 0 0 0 1px ${palette.line2}`,
        overflow: "hidden",
      }}>
        {isBipolar ? (
          <>
            <div style={{
              position: "absolute",
              left: "50%",
              top: 2,
              bottom: 2,
              width: 1,
              transform: "translateX(-0.5px)",
              background: palette.line2,
              opacity: 0.85,
            }} />
            <div style={{
              position: "absolute",
              top: 0,
              bottom: 0,
              right: "50%",
              width: `${bipolarW}%`,
              background: color,
              borderRadius: `${trackH / 2}px 0 0 ${trackH / 2}px`,
              opacity: 0.92,
            }} />
          </>
        ) : (
          <div style={{
            width: `${pct * 100}%`,
            height: "100%",
            borderRadius: trackH / 2,
            background: color,
            opacity: 0.92,
            minWidth: pct > 0 ? 4 : 0,
          }} />
        )}
      </div>
    </div>
  );
}

// ── 7. Dot cloud — scatter / bubble ───────────────────────────────────────────
function DotCloudTeaser({
  rows, kpi, palette,
}: { rows: Record<string, unknown>[]; kpi: NavigatorKPI; palette: Palette }) {
  const color = teaserColor(kpi, palette);
  const dots = useMemo(() => {
    if (!rows.length) return null;
    const allCols = Object.keys(rows[0]);
    const numCols = allCols.filter((c) => rows.slice(0, 5).some((r) => parseNum(r[c]) !== null));
    if (numCols.length < 2) return null;
    const xCol = (kpi.chart?.x_axis ? findColumn(rows, kpi.chart.x_axis) : null) ?? numCols[0];
    const yCol = (kpi.chart?.y_axis ? findColumn(rows, kpi.chart.y_axis) : null) ?? numCols[1];
    const sample = rows.slice(0, 20);
    const xs = sample.map((r) => parseNum(r[xCol])).filter((v): v is number => v !== null);
    const ys = sample.map((r) => parseNum(r[yCol])).filter((v): v is number => v !== null);
    if (xs.length < 3 || ys.length < 3) return null;
    const minX = Math.min(...xs), rangeX = Math.max(...xs) - minX || 1;
    const minY = Math.min(...ys), rangeY = Math.max(...ys) - minY || 1;
    const len = Math.min(xs.length, ys.length);
    return Array.from({ length: len }, (_, i) => ({
      x: ((xs[i] - minX) / rangeX) * (TEASER_W - 8) + 4,
      y: TEASER_H - ((ys[i] - minY) / rangeY) * (TEASER_H - 8) - 4,
    }));
  }, [rows, kpi]);
  if (!dots || dots.length < 3) return null;

  return (
    <svg viewBox={`0 0 ${TEASER_W} ${TEASER_H}`}
      style={{ width: "100%", height: TEASER_H, display: "block" }}>
      {dots.map((d, i) => (
        <circle key={i} cx={d.x.toFixed(1)} cy={d.y.toFixed(1)} r={2.5}
          fill={color} opacity={0.55} />
      ))}
    </svg>
  );
}

// ── 8. Cell grid — heatmap / treemap ──────────────────────────────────────────
function CellGridTeaser({
  rows, kpi, palette,
}: { rows: Record<string, unknown>[]; kpi: NavigatorKPI; palette: Palette }) {
  const color = teaserColor(kpi, palette);
  const cells = useMemo(() => {
    const col = extractNumericCol(rows, kpi.chart?.y_axis);
    if (!col) return null;
    const vals = rows.map((r) => parseNum(r[col])).filter((v): v is number => v !== null).slice(0, 24);
    if (vals.length < 4) return null;
    const min = Math.min(...vals), range = Math.max(...vals) - min || 1;
    return vals.map((v) => (v - min) / range);
  }, [rows, kpi]);
  if (!cells) return null;

  const nCols = Math.min(6, Math.ceil(Math.sqrt(cells.length)));
  const nRows = Math.ceil(cells.length / nCols);
  const cW = (TEASER_W - (nCols - 1)) / nCols;
  const cH = (TEASER_H - (nRows - 1)) / nRows;

  return (
    <svg viewBox={`0 0 ${TEASER_W} ${TEASER_H}`}
      style={{ width: "100%", height: TEASER_H, display: "block" }}>
      {cells.map((intensity, i) => (
        <rect key={i}
          x={(i % nCols) * (cW + 1)} y={Math.floor(i / nCols) * (cH + 1)}
          width={cW} height={cH}
          fill={color} opacity={0.1 + intensity * 0.75} rx={1} />
      ))}
    </svg>
  );
}

// ── Fallback: stat chips ──────────────────────────────────────────────────────
function StatChipsTeaser({
  rows, kpi, palette,
}: { rows: Record<string, unknown>[]; kpi: NavigatorKPI; palette: Palette }) {
  const stats = useMemo(() => {
    const col = extractNumericCol(rows, kpi.chart?.y_axis ?? kpi.l1?.field_name);
    if (!col) return null;
    const vals = rows.map((r) => parseNum(r[col])).filter((v): v is number => v !== null);
    if (!vals.length) return null;
    const unit = kpi.l1?.unit ?? "";
    const fmt  = (v: number) => formatL1(v, unit);
    return [
      { label: "Min", val: fmt(Math.min(...vals)) },
      { label: "Avg", val: fmt(vals.reduce((a, b) => a + b, 0) / vals.length) },
      { label: "Max", val: fmt(Math.max(...vals)) },
    ];
  }, [rows, kpi]);
  if (!stats) return null;

  return (
    <div style={{ display: "flex", gap: 5, marginTop: 6, flexWrap: "wrap" }}>
      {stats.map((s) => (
        <div key={s.label} style={{ display: "flex", flexDirection: "column", gap: 1,
          background: palette.bg2, border: `1px solid ${palette.line}`,
          borderRadius: 4, padding: "3px 6px" }}>
          <span style={{ fontFamily: CHART_NUM_FONT, fontSize: 7, color: palette.ink4,
            textTransform: "uppercase", letterSpacing: "0.07em" }}>{s.label}</span>
          <span style={{ fontFamily: CHART_NUM_FONT, fontSize: 10, color: palette.ink,
            fontVariantNumeric: "tabular-nums" }}>{s.val}</span>
        </div>
      ))}
    </div>
  );
}

// ── Master teaser dispatcher ──────────────────────────────────────────────────
function KpiTeaser({
  kpi, rows, palette,
}: { kpi: NavigatorKPI; rows: Record<string, unknown>[]; palette: Palette }) {
  if (!rows.length) return null;
  const ctype = (kpi.chart?.type ?? "kpi_card").toLowerCase();
  if (ctype === "line_chart")
    return <SparklineTeaser rows={rows} kpi={kpi} palette={palette} filled={false} />;
  if (ctype === "area_chart" || ctype === "stacked_area_chart")
    return <SparklineTeaser rows={rows} kpi={kpi} palette={palette} filled />;
  if (ctype === "bar_chart")
    return <MiniBarTeaser rows={rows} kpi={kpi} palette={palette} />;
  if (ctype === "horizontal_bar_chart")
    return <HorizontalBarTeaser rows={rows} kpi={kpi} palette={palette} />;
  if (ctype === "stacked_bar_chart")
    return <SegmentedBarTeaser rows={rows} kpi={kpi} palette={palette} />;
  if (ctype === "pie_chart" || ctype === "donut_chart")
    return <MiniPieTeaser rows={rows} kpi={kpi} palette={palette} isDonut={ctype === "donut_chart"} />;
  if (ctype === "gauge_chart")
    return <ArcGaugeTeaser rows={rows} kpi={kpi} palette={palette} />;
  if (ctype === "scatter_chart" || ctype === "bubble_chart")
    return <DotCloudTeaser rows={rows} kpi={kpi} palette={palette} />;
  if (ctype === "heatmap_chart" || ctype === "treemap_chart")
    return <CellGridTeaser rows={rows} kpi={kpi} palette={palette} />;
  if (ctype === "kpi_card" || ctype === "scorecard") return null; // number is the teaser
  return <StatChipsTeaser rows={rows} kpi={kpi} palette={palette} />;
}

// ── Layer badge ───────────────────────────────────────────────────────────────

function LayerBadge({ layer, palette }: { layer: "L1" | "L2" | "L3"; palette: Palette }) {
  const L3_COLOR = palette.amber ?? "#E8A33A";
  const c =
    layer === "L1" ? palette.accent :
    layer === "L2" ? palette.green  :
    L3_COLOR;
  return (
    <span style={{
      fontFamily:   CHART_NUM_FONT,
      fontSize:     11,
      fontWeight:   700,
      borderRadius: 3,
      padding:      "1px 5px",
      letterSpacing:"0.04em",
      flexShrink:   0,
      transition:   "color 0.2s, background 0.2s, border-color 0.2s",
      color:        c,
      background:   translucent(c, 0.1),
      border:       `1px solid ${translucent(c, 0.25)}`,
    }}>
      {layer}
    </span>
  );
}

// ── KpiTile ───────────────────────────────────────────────────────────────────

interface KpiTileProps {
  kpi:        NavigatorKPI;
  workbookId: string;
  period:     Period;
  onExpand:   (kpi: NavigatorKPI) => void;
}

function KpiTile({ kpi, workbookId, period, onExpand }: KpiTileProps) {
  const { palette } = useChartTheme();
  const [hovered, setHovered] = useState(false);

  // Show config value instantly; update with live after fetch
  const [liveL1, setLiveL1] = useState<number | null>(
    typeof kpi.l1?.value === "number" ? kpi.l1.value : null
  );
  const [loading, setLoading] = useState(true);
  const [allRows, setAllRows] = useState<Record<string, unknown>[]>([]);

  const fetchAndCompute = useCallback(() => {
    const viewName = kpi.l1?.view_name;
    if (!viewName) {
      const rows = kpi.raw_data?.length ? (kpi.raw_data as Record<string, unknown>[]) : [];
      setAllRows(rows);
      setLoading(false);
      return;
    }

    // ── Cache hit: serve instantly without a network round-trip ──────────────
    const cached = _getCached(workbookId, viewName);
    if (cached) {
      const rows = cached;
      setAllRows(rows);
      // Still compute L1 from cached rows
      const configValue = typeof kpi.l1?.value === "number" ? kpi.l1.value : null;
      if (rows.length) {
        const computed = computeL1Value(kpi, rows);
        const unit = kpi.l1?.unit ?? "";
        let valid = computed !== null;
        if (valid && unit === "%" && (computed! < -200 || computed! > 200)) valid = false;
        if (valid && configValue !== null && !l1MatchesConfig(computed!, configValue)) valid = false;
        setLiveL1(valid ? computed : configValue);
      }
      setLoading(false);
      return;
    }

    api.viewData(workbookId, viewName)
      .then((res) => {
        const rows = (res.rows?.length ? res.rows : (kpi.raw_data ?? [])) as Record<string, unknown>[];
        if (res.dimension_labels && Object.keys(res.dimension_labels).length) {
          mergeWorkbookDimensionLabels(workbookId, res.dimension_labels);
        }
        _setCached(workbookId, viewName, rows);   // populate cache for next mount
        setAllRows(rows);

        // Compute L1
        const configValue = typeof kpi.l1?.value === "number" ? kpi.l1.value : null;
        if (rows.length) {
          const computed = computeL1Value(kpi, rows);
          const unit = kpi.l1?.unit ?? "";
          let valid = computed !== null;
          if (valid && unit === "%" && (computed! < -200 || computed! > 200)) valid = false;
          if (valid && configValue !== null && !l1MatchesConfig(computed!, configValue)) valid = false;
          setLiveL1(valid ? computed : configValue);
        }
      })
      .catch(() => {
        if (kpi.raw_data?.length) setAllRows(kpi.raw_data as Record<string, unknown>[]);
      })
      .finally(() => setLoading(false));
  }, [kpi, workbookId]);

  useEffect(() => { fetchAndCompute(); }, [fetchAndCompute]);

  const periodHeadline = useMemo(() => {
    const configL1 = typeof kpi.l1?.value === "number" ? kpi.l1.value : null;
    if (!allRows.length) return { value: configL1, layer: "L1" as const };
    return resolvePeriodHeadline(kpi, allRows, period, configL1);
  }, [allRows, period, kpi]);

  const displayLayer: "L1" | "L2" | "L3" = periodHeadline.layer;
  const displayValue = loading
    ? (typeof kpi.l1?.value === "number" ? kpi.l1.value : null)
    : (periodHeadline.value ?? liveL1);
  const unit = kpi.l1?.unit ?? "";

  const trendArrow =
    kpi.trend_direction === "up"   ? "▲" :
    kpi.trend_direction === "down" ? "▼" :
    kpi.trend_direction === "flat" ? "→" : null;
  const trendColor =
    kpi.trend_direction === "up"   ? palette.green :
    kpi.trend_direction === "down" ? palette.red   :
    palette.ink3;

  return (
    <button
      type="button"
      onClick={() => onExpand(kpi)}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background:    palette.bg1,
        border:        `1px solid ${hovered ? palette.line2 : palette.line}`,
        borderRadius:  6,
        padding:       "8px 10px",
        cursor:        "pointer",
        display:       "flex",
        flexDirection: "column",
        gap:           3,
        minWidth:      0,
        width:         "100%",
        textAlign:     "left",
        transitionProperty: "border-color, box-shadow",
        transitionDuration: "0.15s",
        boxShadow:     hovered
          ? "0 4px 16px rgba(0,0,0,0.16)"
          : "0 1px 3px rgba(0,0,0,0.06)",
      }}
    >
      {/* Row 1: name + badge + trend arrow */}
      <div style={{
        display:        "flex",
        alignItems:     "center",
        justifyContent: "space-between",
        gap:            6,
        minWidth:       0,
      }}>
        <span style={{
          fontFamily:    CHART_FONT,
          fontSize:      11,
          fontWeight:    600,
          letterSpacing: "0.04em",
          textTransform: "uppercase",
          color:         palette.ink3,
          overflow:      "hidden",
          textOverflow:  "ellipsis",
          whiteSpace:    "nowrap",
          flex:          1,
          minWidth:      0,
        }}>
          {kpi.name}
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 5, flexShrink: 0 }}>
          <LayerBadge layer={displayLayer} palette={palette} />
          {trendArrow && (
            <span style={{
              fontFamily: CHART_NUM_FONT,
              fontSize:   12,
              fontWeight: 700,
              color:      trendColor,
              lineHeight: 1,
            }}>
              {trendArrow}
            </span>
          )}
        </div>
      </div>

      {/* Row 2: headline value */}
      <span style={{
        fontFamily:         CHART_NUM_FONT,
        fontSize:           20,
        fontWeight:         600,
        color:              loading ? palette.ink4 : palette.ink,
        letterSpacing:      "-0.02em",
        lineHeight:         1,
        fontVariantNumeric: "tabular-nums",
        transition:         "color 0.2s",
      }}>
        {loading ? "…" : formatL1(displayValue, unit)}
      </span>

      {/* Teaser — compact visual; click for full chart in modal */}
      {!loading && (
        <KpiTeaser kpi={kpi} rows={allRows} palette={palette} />
      )}
    </button>
  );
}

// ── KpiModal ──────────────────────────────────────────────────────────────────

interface KpiModalProps {
  kpi:         NavigatorKPI;
  workbookId:  string;
  period:      Period;
  onClose:     () => void;
  actionItem?: NavigatorActionItem;   // matched from persona.action_items
  drivers?:    string[];              // matched from persona.kpi_drivers
}

function KpiModal({ kpi, workbookId, period, onClose, actionItem, drivers }: KpiModalProps) {
  const { palette } = useChartTheme();
  const effectivePeriod = kpiSupportsPeriod(kpi) ? period : "now";

  // Live data for modal — prefer cache populated by KpiTile to avoid re-fetching
  const viewName = kpi.l1?.view_name;
  const cachedRows = viewName ? _getCached(workbookId, viewName) : undefined;
  const [allRows, setAllRows]     = useState<Record<string, unknown>[]>(cachedRows ?? []);
  const [dataLoading, setDataLoading] = useState(!cachedRows?.length);

  const fetchData = useCallback(() => {
    const vName = kpi.l1?.view_name;
    if (!vName) {
      if (kpi.raw_data?.length) setAllRows(kpi.raw_data as Record<string, unknown>[]);
      setDataLoading(false);
      return;
    }
    // ── Cache hit: tile already fetched this view — use it immediately ────────
    const cached = _getCached(workbookId, vName);
    if (cached) {
      setAllRows(cached);
      setDataLoading(false);
      return;
    }
    // ── Cache miss: fetch, then cache for future mounts ───────────────────────
    setDataLoading(true);
    api.viewData(workbookId, vName)
      .then((res) => {
        const rows = (res.rows?.length ? res.rows : (kpi.raw_data ?? [])) as Record<string, unknown>[];
        if (res.dimension_labels && Object.keys(res.dimension_labels).length) {
          mergeWorkbookDimensionLabels(workbookId, res.dimension_labels);
        }
        _setCached(workbookId, vName, rows);
        setAllRows(rows);
      })
      .catch(() => {
        if (kpi.raw_data?.length) setAllRows(kpi.raw_data as Record<string, unknown>[]);
      })
      .finally(() => setDataLoading(false));
  }, [kpi, workbookId]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const liveL1 = useMemo<number | null>(() => {
    const configValue = typeof kpi.l1?.value === "number" ? kpi.l1.value : null;
    if (!allRows.length) return configValue;
    const computed = computeL1Value(kpi, allRows);
    if (computed === null) return configValue;
    const unit = kpi.l1?.unit ?? "";
    if (unit === "%" && (computed < -200 || computed > 200)) return configValue;
    if (configValue !== null && !l1MatchesConfig(computed, configValue)) return configValue;
    return computed;
  }, [allRows, kpi]);

  const modalHeadline = useMemo(() => {
    const configL1 = typeof kpi.l1?.value === "number" ? kpi.l1.value : null;
    if (!allRows.length) return { value: configL1, layer: "L1" as const };
    return resolvePeriodHeadline(kpi, allRows, effectivePeriod, configL1);
  }, [allRows, effectivePeriod, kpi]);

  const displayValue = dataLoading ? (kpi.l1?.value ?? null) : (modalHeadline.value ?? liveL1);
  const unit         = kpi.l1?.unit ?? "";
  const displayLayer: "L1" | "L2" | "L3" = modalHeadline.layer;
  const periodLabel  =
    effectivePeriod === "7d" ? (displayLayer === "L3" ? "+7D" : "7D")
    : effectivePeriod === "30d" ? (displayLayer === "L3" ? "+30D" : "30D")
    : null;
  const hasExplanation = !!(kpi.explanation?.key_insight || kpi.explanation?.risk);
  const hasInsightPanel = hasExplanation
    || (drivers && drivers.length > 0)
    || !!actionItem
    || (effectivePeriod !== "now" && !!kpi.l3_forecast?.predictions?.length);

  const [chartHeight, setChartHeight] = useState(440);
  useEffect(() => {
    const update = () => {
      setChartHeight(Math.min(540, Math.max(400, Math.round(window.innerHeight * 0.5))));
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  const trendColor =
    kpi.trend_direction === "up"   ? palette.green :
    kpi.trend_direction === "down" ? palette.red   :
    palette.ink3;
  const trendArrow =
    kpi.trend_direction === "up"   ? "▲" :
    kpi.trend_direction === "down" ? "▼" :
    kpi.trend_direction === "flat" ? "→" : null;

  const showChart = useMemo(() => {
    if (dataLoading || !allRows.length) return false;
    const val = typeof displayValue === "number" ? displayValue : null;
    return canBuildKpiChart(kpi, allRows, effectivePeriod, false, val);
  }, [dataLoading, allRows, kpi, effectivePeriod, displayValue]);

  // Close on Escape
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Lock body scroll
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, []);

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position:       "fixed",
          inset:          0,
          background:     "rgba(0,0,0,0.5)",
          backdropFilter: "blur(4px)",
          zIndex:         100,
          animation:      "backdropIn 0.18s ease",
        }}
      />

      {/* Modal box — chart is primary; insights scroll below */}
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          position:   "fixed",
          top:        "50%",
          left:       "50%",
          transform:  "translate(-50%, -50%)",
          zIndex:     101,
          width:      "min(96vw, 1040px)",
          maxHeight:  "92vh",
          overflow:   "hidden",
          background: palette.bg1,
          border:     `1px solid ${palette.line2}`,
          borderRadius: 10,
          padding:    "18px 22px 16px",
          display:    "flex",
          flexDirection: "column",
          gap:        12,
          animation:  "modalIn 0.2s ease",
        }}
      >
        {/* Close button */}
        <button
          type="button"
          onClick={onClose}
          style={{
            position:   "absolute",
            top:        14,
            right:      14,
            width:      28,
            height:     28,
            borderRadius: "50%",
            border:     `1.5px solid ${palette.line2}`,
            background: "transparent",
            color:      palette.ink3,
            fontFamily: CHART_NUM_FONT,
            fontSize:   16,
            lineHeight: 1,
            cursor:     "pointer",
            display:    "flex",
            alignItems:     "center",
            justifyContent: "center",
            padding:    0,
            transition: "border-color 0.15s, color 0.15s",
          }}
        >
          ×
        </button>

        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", paddingRight: 36 }}>
          <span style={{
            fontFamily:    CHART_FONT,
            fontSize:      14,
            fontWeight:    700,
            letterSpacing: "0.04em",
            textTransform: "uppercase",
            color:         palette.ink2,
          }}>
            {kpi.name}
          </span>

          <LayerBadge layer={displayLayer} palette={palette} />

          {periodLabel && (
            <span style={{
              fontFamily: CHART_NUM_FONT,
              fontSize:   11,
              fontWeight: 700,
              borderRadius: 3,
              padding:    "1px 5px",
              letterSpacing: "0.04em",
              color:      palette.green,
              background: translucent(palette.green, 0.1),
              border:     `1px solid ${translucent(palette.green, 0.22)}`,
            }}>
              {periodLabel}
            </span>
          )}

          {trendArrow && kpi.trend_pct !== null && (
            <span style={{
              display:    "inline-flex",
              alignItems: "center",
              gap:        3,
              fontFamily: CHART_NUM_FONT,
              fontSize:   12,
              fontWeight: 600,
              color:      trendColor,
            }}>
              {trendArrow} {Math.abs(kpi.trend_pct ?? 0).toFixed(1)}%
            </span>
          )}

          {hasExplanation && (
            <span style={{
              display:        "inline-flex",
              alignItems:     "center",
              justifyContent: "center",
              width:          18,
              height:         18,
              borderRadius:   "50%",
              border:         `1.5px solid ${palette.line2}`,
              color:          palette.ink3,
              fontFamily:     CHART_NUM_FONT,
              fontSize:       11,
              fontWeight:     700,
              lineHeight:     1,
            }}>
              i
            </span>
          )}
        </div>

        {/* Headline value */}
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexShrink: 0 }}>
          <span style={{
            fontFamily:         CHART_NUM_FONT,
            fontSize:           32,
            fontWeight:         600,
            color:              dataLoading ? palette.ink4 : palette.ink,
            letterSpacing:      "-0.02em",
            lineHeight:         1,
            fontVariantNumeric: "tabular-nums",
            transition:         "color 0.2s",
          }}>
            {formatL1(displayValue, unit)}
          </span>

          {effectivePeriod !== "now" && !dataLoading && liveL1 !== null && (
            <span style={{
              fontFamily:         CHART_NUM_FONT,
              fontSize:           13,
              color:              palette.ink4,
              fontVariantNumeric: "tabular-nums",
            }}>
              vs {formatL1(liveL1, unit)} now
            </span>
          )}
        </div>

        {/* Description */}
        {kpi.description && (
          <p style={{
            fontFamily: CHART_FONT,
            fontSize:   12,
            color:      palette.ink3,
            lineHeight: 1.5,
            margin:     0,
            flexShrink: 0,
          }}>
            {formatKpiDescription(kpi.description)}
          </p>
        )}

        {showChart && (
          <div style={{ flexShrink: 0, width: "100%" }}>
            <NavigatorKpiChart
              kpi={kpi}
              rows={allRows}
              loading={dataLoading}
              period={effectivePeriod}
              height={chartHeight}
              hideIfEmpty
              showRangeControl
              overrideValue={typeof displayValue === "number" ? displayValue : null}
              dimensionLabelMaps={workbookDimensionLabels(workbookId)}
            />
          </div>
        )}

        {hasInsightPanel && (
        <div style={{
          flex:          1,
          minHeight:     0,
          overflowY:     "auto",
          display:       "flex",
          flexDirection: "column",
          gap:           12,
          paddingRight:  2,
        }}>
        {/* Key Insight + Risk */}
        {hasExplanation && (
          <div style={{
            display:      "flex",
            flexDirection: "column",
            gap:          12,
            borderTop:    `1px solid ${palette.line}`,
            paddingTop:   16,
          }}>
            {kpi.explanation?.key_insight && (
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span style={{
                  fontFamily:    CHART_NUM_FONT,
                  fontSize:      11,
                  fontWeight:    700,
                  letterSpacing: "0.04em",
                  textTransform: "uppercase",
                  color:         palette.accent,
                }}>
                  Key Insight
                </span>
                <span style={{
                  fontFamily: CHART_FONT,
                  fontSize:   13,
                  color:      palette.ink,
                  lineHeight: 1.6,
                }}>
                  {kpi.explanation.key_insight}
                </span>
              </div>
            )}
            {kpi.explanation?.risk && (
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span style={{
                  fontFamily:    CHART_NUM_FONT,
                  fontSize:      11,
                  fontWeight:    700,
                  letterSpacing: "0.04em",
                  textTransform: "uppercase",
                  color:         palette.red,
                }}>
                  Risk
                </span>
                <span style={{
                  fontFamily: CHART_FONT,
                  fontSize:   13,
                  color:      palette.ink,
                  lineHeight: 1.6,
                }}>
                  {kpi.explanation.risk.replace(/\bnull\b/gi, "").replace(/\s{2,}/g, " ").trim()}
                </span>
              </div>
            )}
          </div>
        )}

        {/* What's Driving This */}
        {drivers && drivers.length > 0 && (
          <div style={{
            borderTop:    `1px solid ${palette.line}`,
            paddingTop:   16,
            display:      "flex",
            flexDirection: "column",
            gap:          10,
          }}>
            <span style={{
              fontFamily:    CHART_NUM_FONT,
              fontSize:      11,
              fontWeight:    700,
              letterSpacing: "0.06em",
              textTransform: "uppercase",
              color:         palette.ink3,
            }}>
              What's Driving This
            </span>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {drivers.map((d, i) => (
                <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
                  <span style={{
                    width: 5, height: 5, borderRadius: "50%",
                    background: palette.ink3, flexShrink: 0, marginTop: 6,
                  }} />
                  <span style={{
                    fontFamily: CHART_FONT,
                    fontSize:   12,
                    color:      palette.ink2,
                    lineHeight: 1.5,
                  }}>
                    {d}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* L3 Forecast band — only show when period is 7d/30d and L3 exists */}
        {effectivePeriod !== "now" && kpi.l3_forecast?.predictions?.length && (() => {
          const preds  = kpi.l3_forecast!.predictions;
          const lower  = kpi.l3_forecast!.lower_p10;
          const upper  = kpi.l3_forecast!.upper_p90;
          const idx    = effectivePeriod === "7d" ? Math.min(6, preds.length - 1) : Math.min(29, preds.length - 1);
          const lo     = lower[idx];
          const hi     = upper[idx];
          const L3_C   = palette.amber ?? "#E8A33A";
          return (
            <div style={{
              borderTop:   `1px solid ${palette.line}`,
              paddingTop:  16,
              display:     "flex",
              flexDirection: "column",
              gap:         8,
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{
                  fontFamily:    CHART_NUM_FONT,
                  fontSize:      11,
                  fontWeight:    700,
                  letterSpacing: "0.06em",
                  textTransform: "uppercase",
                  color:         L3_C,
                }}>
                  L3 · TimesFM Forecast
                </span>
                <span style={{
                  fontFamily: CHART_NUM_FONT,
                  fontSize:   10,
                  color:      palette.ink4,
                }}>
                  {kpi.l3_forecast!.model} · {effectivePeriod === "7d" ? "Day 7" : "Day 30"} prediction
                </span>
              </div>
              <div style={{
                display:      "flex",
                gap:          8,
                alignItems:   "center",
                background:   translucent(L3_C, 0.07),
                border:       `1px solid ${translucent(L3_C, 0.2)}`,
                borderRadius: 6,
                padding:      "8px 12px",
              }}>
                <span style={{
                  fontFamily:         CHART_NUM_FONT,
                  fontSize:           20,
                  fontWeight:         600,
                  color:              L3_C,
                  fontVariantNumeric: "tabular-nums",
                  letterSpacing:      "-0.02em",
                }}>
                  {formatL1(preds[idx], unit)}
                </span>
                {lo !== undefined && hi !== undefined && (
                  <span style={{
                    fontFamily:         CHART_NUM_FONT,
                    fontSize:           11,
                    color:              palette.ink4,
                    fontVariantNumeric: "tabular-nums",
                  }}>
                    p10–p90: {formatL1(lo, unit)} – {formatL1(hi, unit)}
                  </span>
                )}
              </div>
            </div>
          );
        })()}

        {/* Recommended Action */}
        {actionItem && (
          <div style={{
            borderTop:    `1px solid ${palette.line}`,
            paddingTop:   16,
            display:      "flex",
            flexDirection: "column",
            gap:          8,
          }}>
            <span style={{
              fontFamily:    CHART_NUM_FONT,
              fontSize:      11,
              fontWeight:    700,
              letterSpacing: "0.06em",
              textTransform: "uppercase",
              color:         actionItem.signal === "critical" ? palette.red :
                             actionItem.signal === "watch"    ? (palette.amber ?? "#F0C040") :
                             palette.green,
            }}>
              Recommended Action
            </span>
            <div style={{
              display:      "flex",
              alignItems:   "flex-start",
              gap:          10,
              background:   actionItem.signal === "critical" ? `${palette.red}0E` :
                            actionItem.signal === "watch"    ? `${palette.amber ?? "#F0C040"}0E` :
                            `${palette.green}0E`,
              border: `1px solid ${
                actionItem.signal === "critical" ? `${palette.red}30` :
                actionItem.signal === "watch"    ? `${palette.amber ?? "#F0C040"}30` :
                `${palette.green}30`
              }`,
              borderRadius: 6,
              padding:      "10px 12px",
            }}>
              <span style={{
                width: 6, height: 6, borderRadius: "50%", flexShrink: 0, marginTop: 5,
                background: actionItem.signal === "critical" ? palette.red :
                            actionItem.signal === "watch"    ? (palette.amber ?? "#F0C040") :
                            palette.green,
              }} />
              <span style={{
                fontFamily: CHART_FONT,
                fontSize:   13,
                color:      palette.ink,
                lineHeight: 1.6,
              }}>
                {actionItem.action}
              </span>
            </div>
          </div>
        )}
        </div>
        )}
      </div>

      <style>{`
        @keyframes backdropIn {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
        @keyframes modalIn {
          from { opacity: 0; transform: translate(-50%, -48%) scale(0.97); }
          to   { opacity: 1; transform: translate(-50%, -50%) scale(1); }
        }
      `}</style>
    </>
  );
}

// Master grid column count — sections span exactly their KPI count (capped here).
const MASTER_GRID_COLS = 4;

function useMasterGridCols(): number {
  const [cols, setCols] = useState(MASTER_GRID_COLS);
  useEffect(() => {
    const update = () => {
      const w = window.innerWidth;
      setCols(w < 640 ? 1 : w < 960 ? 2 : MASTER_GRID_COLS);
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  return cols;
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  persona:    NavigatorPersona;
  workbookId: string;
}

export function NavigatorCanvas({ persona, workbookId }: Props) {
  const { palette }  = useChartTheme();
  const masterCols   = useMasterGridCols();
  const [period, setPeriod] = useState<Period>("now");
  const [expandedKpi, setExpandedKpi] = useState<NavigatorKPI | null>(null);
  const hasSummary = (persona.summary_cards?.length ?? 0) > 0;
  const allPersonaKpis = useMemo(
    () => persona.dashboard_sections.flatMap((s) => s.kpis),
    [persona.dashboard_sections],
  );
  const showPeriodBar = personaSupportsPeriod(allPersonaKpis);

  // Helper: normalise KPI name for fuzzy matching
  const normName = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");

  // Find matching action item + drivers for a KPI
  const getModalExtras = useCallback((kpi: NavigatorKPI) => {
    const target = normName(kpi.name);
    const actionItem = (persona.action_items ?? []).find(
      (a) => normName(a.kpi_name) === target
    );
    const driversEntry = (persona.kpi_drivers ?? []).find(
      (d) => normName(d.kpi_name) === target
    );
    return { actionItem, drivers: driversEntry?.drivers };
  }, [persona.action_items, persona.kpi_drivers]);

  const handleExpand = useCallback((kpi: NavigatorKPI) => setExpandedKpi(kpi), []);
  const handleClose  = useCallback(() => setExpandedKpi(null), []);

  // Show all KPIs by default — typical persona has ≤8 tiles; avoids extra scroll.
  const DEFAULT_VISIBLE = 12;
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set());
  const toggleSection = useCallback((sectionId: string) => {
    setExpandedSections(prev => {
      const next = new Set(prev);
      next.has(sectionId) ? next.delete(sectionId) : next.add(sectionId);
      return next;
    });
  }, []);

  // Action item click — find the matching KPI and open its modal
  const handleActionClick = useCallback((kpiName: string) => {
    const normalise = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
    const target = normalise(kpiName);
    for (const section of persona.dashboard_sections) {
      const match = section.kpis.find((k) => normalise(k.name) === target);
      if (match) { setExpandedKpi(match); return; }
    }
    // Fuzzy fallback — find best partial match
    let best: NavigatorKPI | null = null;
    let bestScore = 0;
    for (const section of persona.dashboard_sections) {
      for (const k of section.kpis) {
        const kn = normalise(k.name);
        const score = [...target].filter((c) => kn.includes(c)).length;
        if (score > bestScore) { best = k; bestScore = score; }
      }
    }
    if (best) setExpandedKpi(best);
  }, [persona.dashboard_sections]);

  return (
    <div style={{
      display:       "flex",
      flexDirection: "column",
      gap:           10,
      padding:       "2px 0 0",
      flex:          1,
      minHeight:     0,
      overflow:      "hidden",
      width:         "100%",
    }}>

      {/* ── Two-column: Daily Summary + Action Items ── */}
      {hasSummary && (
        <BriefingHeader
          summaryCards={persona.summary_cards!}
          actionItems={persona.action_items ?? []}
          onActionClick={handleActionClick}
          palette={palette}
        />
      )}

      {/* ── Period bar (only when persona has temporal / forecast KPIs) ── */}
      {showPeriodBar && (
        <div style={{
          display:        "flex",
          alignItems:     "center",
          justifyContent: "space-between",
          flexShrink:     0,
        }}>
          {period === "now" && (
            <span style={{
              fontFamily:    CHART_NUM_FONT,
              fontSize:      10,
              color:         palette.ink4,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
            }}>
              Current values
            </span>
          )}
          <div style={{ marginLeft: "auto" }}>
            <PeriodBar period={period} onChange={setPeriod} palette={palette} />
          </div>
        </div>
      )}

      {/* ── KPI master grid — each domain spans only its KPI count ── */}
      <div style={{
        flex:                1,
        minHeight:           0,
        overflowY:           "auto",
        display:             "grid",
        gridTemplateColumns: `repeat(${masterCols}, minmax(0, 1fr))`,
        gap:                 10,
        alignContent:        "start",
        scrollbarWidth:      "thin",
        scrollbarColor:      `${palette.line2} transparent`,
      }}>
      {persona.dashboard_sections.filter((section) => section.kpis.length > 0).map((section) => {
        // Sort by priority desc — highest relevancy first
        const sorted = [...section.kpis].sort(
          (a, b) => (b.priority ?? 50) - (a.priority ?? 50)
        );
        const isExpanded = expandedSections.has(section.id);
        const visible    = isExpanded ? sorted : sorted.slice(0, DEFAULT_VISIBLE);
        const hiddenCount = sorted.length - DEFAULT_VISIBLE;
        // Domain width = number of visible KPIs (1 domain / 1 KPI → 1 column, not 2).
        const tileCols    = Math.min(visible.length, masterCols);
        const sectionSpan = Math.max(tileCols, 1);

        return (
          <section
            key={section.id}
            style={{
              gridColumn:          `span ${sectionSpan}`,
              display:             "grid",
              gridTemplateColumns: `repeat(${tileCols}, minmax(0, 1fr))`,
              gap:                 8,
              alignContent:        "start",
              minWidth:            0,
            }}
          >

            {/* Section header — full width of this domain block */}
            <div style={{
              gridColumn:   "1 / -1",
              display:      "flex",
              alignItems:   "center",
              gap:          8,
              marginBottom: 2,
            }}>
              <span style={{
                fontFamily:    CHART_NUM_FONT,
                fontSize:      10,
                fontWeight:    700,
                color:         palette.ink4,
                letterSpacing: "0.08em",
                textTransform: "uppercase",
              }}>
                {section.title.replace(/_/g, " ")}
              </span>
              <span style={{
                fontFamily: CHART_NUM_FONT,
                fontSize:   10,
                color:      palette.ink4,
                marginLeft: "auto",
              }}>
                {visible.length}/{sorted.length} KPI{sorted.length !== 1 ? "s" : ""}
              </span>
            </div>

            {visible.map((kpi, index) => (
              <div
                key={kpi.id ?? `${section.id}-${index}`}
                style={{
                  animation:      "kpiEnter 0.28s ease both",
                  animationDelay: `${index * 0.04}s`,
                  minWidth:       0,
                }}
              >
                <KpiTile
                  kpi={kpi}
                  workbookId={workbookId}
                  period={period}
                  onExpand={handleExpand}
                />
              </div>
            ))}

            {/* Show more / Show less button */}
            {sorted.length > DEFAULT_VISIBLE && (
              <button
                type="button"
                onClick={() => toggleSection(section.id)}
                style={{
                  gridColumn:    "1 / -1",
                  marginTop:     2,
                  width:         "100%",
                  padding:       "6px 0",
                  background:    "transparent",
                  border:        `1px dashed ${palette.line2}`,
                  borderRadius:  6,
                  cursor:        "pointer",
                  fontFamily:    CHART_NUM_FONT,
                  fontSize:      10,
                  fontWeight:    700,
                  letterSpacing: "0.06em",
                  color:         palette.ink4,
                  textTransform: "uppercase",
                  transition:    "border-color 0.15s, color 0.15s",
                }}
                onMouseEnter={e => {
                  (e.target as HTMLButtonElement).style.borderColor = palette.line2;
                  (e.target as HTMLButtonElement).style.color = palette.ink3;
                }}
                onMouseLeave={e => {
                  (e.target as HTMLButtonElement).style.borderColor = palette.line2;
                  (e.target as HTMLButtonElement).style.color = palette.ink4;
                }}
              >
                {isExpanded
                  ? `▲ Show less`
                  : `▼ Show ${hiddenCount} more KPI${hiddenCount !== 1 ? "s" : ""}`}
              </button>
            )}
          </section>
        );
      })}
      </div>

      {/* ── KPI detail modal ── */}
      {expandedKpi && (() => {
        const { actionItem, drivers } = getModalExtras(expandedKpi);
        return (
          <KpiModal
            kpi={expandedKpi}
            workbookId={workbookId}
            period={period}
            onClose={handleClose}
            actionItem={actionItem}
            drivers={drivers}
          />
        );
      })()}

      <style>{`
        @keyframes kpiEnter {
          from { opacity: 0; transform: translateY(6px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes summaryIn {
          from { opacity: 0; transform: translateY(4px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}
