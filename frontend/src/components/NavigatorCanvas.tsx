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
import { NavigatorKpiChart } from "./NavigatorKpiChart";
import { CHART_FONT, CHART_NUM_FONT, translucent } from "./charts/chartTheme";
import { api } from "../api/client";
import type { NavigatorPersona, NavigatorSummaryCard, NavigatorKPI, L2Projection } from "../types/navigator";

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

function evaluateL2Projection(
  rows: Record<string, unknown>[],
  proj: L2Projection,
  horizonDays: number,
): number | null {
  if (!rows.length) return null;

  const valueCol = findColumn(rows, proj.value_field);
  if (!valueCol) return null;

  const dateColHint = proj.date_field || null;
  const dateCol = dateColHint ? findColumn(rows, dateColHint) : null;

  switch (proj.method) {
    case "daily_rate": {
      const total = aggregateField(rows, valueCol, "sum");
      if (total === null) return null;

      let spanDays = dateCol ? computeDateSpanDays(rows, dateCol) : null;

      if (!spanDays && dateCol) {
        const distinct = new Set(rows.map((r) => String(r[dateCol!] ?? "")).filter(Boolean));
        if (distinct.size > 0) {
          const daysPerPeriod = distinct.size > 4 ? 30 : 90;
          spanDays = distinct.size * daysPerPeriod;
        }
      }

      if (!spanDays) return total;
      return (total / spanDays) * horizonDays;
    }

    case "ratio": {
      return aggregateField(rows, valueCol, proj.aggregation);
    }

    case "growth_rate": {
      if (!dateCol) return aggregateField(rows, valueCol, proj.aggregation);

      const byDate = new Map<number, number[]>();
      for (const row of rows) {
        const d = parseRowDate(row[dateCol]);
        const v = parseNum(row[valueCol]);
        if (d && v !== null) {
          const t = d.getTime();
          if (!byDate.has(t)) byDate.set(t, []);
          byDate.get(t)!.push(v);
        }
      }
      const sorted = [...byDate.entries()]
        .sort(([a], [b]) => a - b)
        .map(([t, vals]) => ({ t, v: vals.reduce((a, b) => a + b, 0) }));

      if (sorted.length < 2) {
        return aggregateField(rows, valueCol, proj.aggregation);
      }

      const first = sorted[0];
      const last  = sorted[sorted.length - 1];
      if (!first.v) return last.v;

      const periodMs   = (last.t - first.t) / (sorted.length - 1);
      const periodDays = periodMs / 86_400_000;
      if (!periodDays) return last.v;

      const growthPerPeriod = Math.pow(last.v / first.v, 1 / (sorted.length - 1)) - 1;
      const periodsAhead    = horizonDays / periodDays;
      return last.v * Math.pow(1 + growthPerPeriod, periodsAhead);
    }

    case "stable":
    default: {
      return aggregateField(rows, valueCol, proj.aggregation);
    }
  }
}

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

// ── Summary card ──────────────────────────────────────────────────────────────

const summaryCardBase = {
  flex: 1,
  minWidth: 0,
  borderRadius: 6,
  padding: "10px 14px",
  display: "flex",
  flexDirection: "column" as const,
  gap: 4,
  animation: "summaryIn 0.32s ease both",
} as const;

function SummaryCardItem({ card }: { card: NavigatorSummaryCard }) {
  const { palette } = useChartTheme();

  const accentColor =
    card.signal === "positive" ? palette.green :
    card.signal === "warning"  ? palette.red   :
    palette.accent;

  const signalLabel =
    card.signal === "positive" ? "● Positive" :
    card.signal === "warning"  ? "▲ Watch"    :
    "◆ Insight";

  return (
    <div style={{
      ...summaryCardBase,
      background: `${accentColor}12`,
      border: `1px solid ${accentColor}33`,
      borderLeft: `3px solid ${accentColor}`,
    }}>
      {/* Signal + title on one row */}
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{
          fontFamily: CHART_NUM_FONT, fontSize: 10, fontWeight: 700,
          color: accentColor, letterSpacing: "0.04em", textTransform: "uppercase",
          flexShrink: 0,
        }}>
          {signalLabel}
        </span>
        <span style={{
          fontFamily: CHART_FONT, fontSize: 12, fontWeight: 700,
          color: palette.ink, lineHeight: 1.2,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          {card.title}
        </span>
      </div>
      {/* Body — full text */}
      <p style={{
        fontFamily: CHART_FONT, fontSize: 11,
        color: palette.ink3, lineHeight: 1.5, margin: 0,
      }}>
        {card.body}
      </p>
    </div>
  );
}

// ── Mini sparkline SVG ────────────────────────────────────────────────────────

function MiniSparkline({
  rawData,
  color,
}: {
  rawData: Record<string, unknown>[];
  color: string;
}) {
  const yValues = useMemo(() => {
    if (!rawData?.length) return null;
    const cols = Object.keys(rawData[0]);
    const numCol = cols.find((c) => {
      const sample = rawData.slice(0, 5).map((r) => parseNum(r[c]));
      return sample.some((v) => v !== null);
    });
    if (!numCol) return null;
    return rawData.map((r) => parseNum(r[numCol])).filter((v): v is number => v !== null);
  }, [rawData]);

  if (!yValues || yValues.length < 2) return null;

  const H = 40;
  const W = 100; // viewBox units — SVG scales to fill container
  const min = Math.min(...yValues);
  const max = Math.max(...yValues);
  const range = max - min || 1;

  const points = yValues
    .map((v, i) => {
      const x = (i / (yValues.length - 1)) * W;
      const y = H - ((v - min) / range) * (H - 4) - 2;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      style={{ width: "100%", height: H, display: "block" }}
    >
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

// ── Layer badge ───────────────────────────────────────────────────────────────

function LayerBadge({ layer, palette }: { layer: "L1" | "L2"; palette: Palette }) {
  return (
    <span style={{
      fontFamily: CHART_NUM_FONT,
      fontSize: 11,
      fontWeight: 700,
      borderRadius: 3,
      padding: "1px 5px",
      letterSpacing: "0.04em",
      flexShrink: 0,
      transition: "color 0.2s, background 0.2s, border-color 0.2s",
      color:       layer === "L1" ? palette.accent : palette.green,
      background:  layer === "L1" ? translucent(palette.accent, 0.1) : translucent(palette.green, 0.1),
      border: `1px solid ${layer === "L1" ? translucent(palette.accent, 0.25) : translucent(palette.green, 0.25)}`,
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

    api.viewData(workbookId, viewName)
      .then((res) => {
        const rows = (res.rows?.length ? res.rows : (kpi.raw_data ?? [])) as Record<string, unknown>[];
        setAllRows(rows);

        // Compute L1
        const fieldHint = kpi.l1?.field_name;
        const configValue = typeof kpi.l1?.value === "number" ? kpi.l1.value : null;
        if (fieldHint && rows.length) {
          const col = findColumn(rows, fieldHint);
          if (col) {
            const agg = (kpi.chart?.aggregation ?? "sum").toLowerCase();
            const computed = aggregateField(rows, col, agg);
            const unit = kpi.l1?.unit ?? "";
            let valid = computed !== null;
            if (valid && unit === "%" && (computed! < 0 || computed! > 100)) valid = false;
            if (valid && agg === "count" && configValue !== null) {
              const ratio = computed! / configValue;
              if (ratio > 3 || ratio < 0.33) valid = false;
            }
            setLiveL1(valid ? computed : configValue);
          }
        }
      })
      .catch(() => {
        if (kpi.raw_data?.length) setAllRows(kpi.raw_data as Record<string, unknown>[]);
      })
      .finally(() => setLoading(false));
  }, [kpi, workbookId]);

  useEffect(() => { fetchAndCompute(); }, [fetchAndCompute]);

  // L2 is derived directly — no extra state or effect needed
  const liveL2 = useMemo(() => {
    if (period === "now" || !allRows.length || !kpi.l2_projection) return null;
    return evaluateL2Projection(allRows, kpi.l2_projection, period === "7d" ? 7 : 30);
  }, [allRows, period, kpi.l2_projection]);

  const displayLayer: "L1" | "L2" = period === "now" ? "L1" : "L2";
  const displayValue = loading
    ? (typeof kpi.l1?.value === "number" ? kpi.l1.value : null)
    : (period === "now" ? liveL1 : liveL2);
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
        padding:       "10px 12px",
        cursor:        "pointer",
        display:       "flex",
        flexDirection: "column",
        gap:           4,
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
        fontSize:           22,
        fontWeight:         600,
        color:              loading ? palette.ink4 : palette.ink,
        letterSpacing:      "-0.02em",
        lineHeight:         1,
        fontVariantNumeric: "tabular-nums",
        transition:         "color 0.2s",
      }}>
        {loading ? "…" : formatL1(displayValue, unit)}
      </span>

      {/* Chart — correct type at compact height; full detail on click */}
      {(kpi.chart?.type ?? "kpi_card") !== "kpi_card" && (kpi.chart?.type ?? "") !== "scorecard" && (
        <div style={{ marginTop: 4 }}>
          <NavigatorKpiChart
            kpi={kpi}
            rows={allRows.length ? allRows : (kpi.raw_data as Record<string, unknown>[] ?? [])}
            loading={loading}
            height={110}
            maxPoints={20}
            compact
          />
        </div>
      )}
    </button>
  );
}

// ── KpiModal ──────────────────────────────────────────────────────────────────

interface KpiModalProps {
  kpi:        NavigatorKPI;
  workbookId: string;
  period:     Period;
  onClose:    () => void;
}

function KpiModal({ kpi, workbookId, period, onClose }: KpiModalProps) {
  const { palette } = useChartTheme();

  // Live data for modal headline
  const [allRows, setAllRows]     = useState<Record<string, unknown>[]>([]);
  const [dataLoading, setDataLoading] = useState(true);

  const fetchData = useCallback(() => {
    const viewName = kpi.l1?.view_name;
    if (!viewName) {
      if (kpi.raw_data?.length) setAllRows(kpi.raw_data as Record<string, unknown>[]);
      setDataLoading(false);
      return;
    }
    setDataLoading(true);
    api.viewData(workbookId, viewName)
      .then((res) => {
        if (res.rows?.length) {
          setAllRows(res.rows as Record<string, unknown>[]);
        } else if (kpi.raw_data?.length) {
          setAllRows(kpi.raw_data as Record<string, unknown>[]);
        }
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
    const fieldHint = kpi.l1?.field_name;
    if (!fieldHint) return configValue;
    const col = findColumn(allRows, fieldHint);
    if (!col) return configValue;
    const agg = (kpi.chart?.aggregation ?? "sum").toLowerCase();
    const computed = aggregateField(allRows, col, agg);
    if (computed === null) return configValue;
    const unit = kpi.l1?.unit ?? "";
    if (unit === "%" && (computed < 0 || computed > 100)) return configValue;
    if (agg === "count" && configValue !== null) {
      const ratio = computed / configValue;
      if (ratio > 3 || ratio < 0.33) return configValue;
    }
    return computed;
  }, [allRows, kpi]);

  const liveL2 = useMemo<number | null>(() => {
    if (period === "now" || !allRows.length || !kpi.l2_projection) return null;
    return evaluateL2Projection(allRows, kpi.l2_projection, period === "7d" ? 7 : 30);
  }, [allRows, period, kpi.l2_projection]);

  const liveValue   = period === "now" ? liveL1 : liveL2;
  const displayValue = dataLoading ? (kpi.l1?.value ?? null) : liveValue;
  const unit         = kpi.l1?.unit ?? "";
  const displayLayer: "L1" | "L2" = period === "now" ? "L1" : "L2";
  const periodLabel  = period === "7d" ? "7D" : period === "30d" ? "30D" : null;
  const hasExplanation = !!(kpi.explanation?.key_insight || kpi.explanation?.risk);

  const trendColor =
    kpi.trend_direction === "up"   ? palette.green :
    kpi.trend_direction === "down" ? palette.red   :
    palette.ink3;
  const trendArrow =
    kpi.trend_direction === "up"   ? "▲" :
    kpi.trend_direction === "down" ? "▼" :
    kpi.trend_direction === "flat" ? "→" : null;

  const ctype = (kpi.chart?.type ?? "kpi_card").toLowerCase();
  const isCardOnly = ctype === "kpi_card" || ctype === "scorecard";

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

      {/* Modal box */}
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          position:   "fixed",
          top:        "50%",
          left:       "50%",
          transform:  "translate(-50%, -50%)",
          zIndex:     101,
          width:      "90vw",
          maxWidth:   720,
          maxHeight:  "85vh",
          overflowY:  "auto",
          background: palette.bg1,
          border:     `1px solid ${palette.line2}`,
          borderRadius: 10,
          padding:    24,
          display:    "flex",
          flexDirection: "column",
          gap:        16,
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
        <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
          <span style={{
            fontFamily:         CHART_NUM_FONT,
            fontSize:           36,
            fontWeight:         600,
            color:              dataLoading ? palette.ink4 : palette.ink,
            letterSpacing:      "-0.02em",
            lineHeight:         1,
            fontVariantNumeric: "tabular-nums",
            transition:         "color 0.2s",
          }}>
            {formatL1(displayValue, unit)}
          </span>

          {period !== "now" && !dataLoading && liveL1 !== null && (
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
            fontSize:   13,
            color:      palette.ink3,
            lineHeight: 1.6,
            margin:     0,
          }}>
            {kpi.description}
          </p>
        )}

        {/* Full chart */}
        {!isCardOnly && (
          <div style={{ minHeight: 280 }}>
            <NavigatorKpiChart
              kpi={kpi}
              rows={allRows}
              loading={dataLoading}
              period={period}
              height={280}
            />
          </div>
        )}

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
                  {kpi.explanation.risk}
                </span>
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

// ── KPI tile column span ──────────────────────────────────────────────────────
// 2-column grid. Span rules:
//   heatmap / radar / treemap / funnel → always span 2 (full width — need space)
//   last tile in a section that would be alone → span 2 (fills gap cleanly)
//   everything else → span 1 (half width, 2 per row)

const FULL_WIDTH_TYPES = new Set([
  "heatmap_chart", "radar_chart", "treemap_chart", "funnel_chart",
]);

function kpiColSpan(kpi: NavigatorKPI, index: number, total: number): number {
  const ctype = kpi.chart?.type ?? "kpi_card";
  // Complex chart types always get full width
  if (FULL_WIDTH_TYPES.has(ctype)) return 2;
  // Last tile alone on a row + has a chart → expand to fill gap
  const isLast       = index === total - 1;
  const wouldBeLone  = total % 2 !== 0 && isLast;
  const hasChart     = ctype !== "kpi_card" && ctype !== "scorecard";
  if (wouldBeLone && hasChart) return 2;
  return 1;
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  persona:    NavigatorPersona;
  workbookId: string;
}

export function NavigatorCanvas({ persona, workbookId }: Props) {
  const { palette }  = useChartTheme();
  const [period, setPeriod]         = useState<Period>("now");
  const [expandedKpi, setExpandedKpi] = useState<NavigatorKPI | null>(null);
  const hasSummary = (persona.summary_cards?.length ?? 0) > 0;

  const handleExpand   = useCallback((kpi: NavigatorKPI) => setExpandedKpi(kpi), []);
  const handleClose    = useCallback(() => setExpandedKpi(null), []);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, padding: "4px 0" }}>

      {/* ── AI Summary strip ── */}
      {hasSummary && (
        <div style={{ display: "flex", gap: 10 }}>
          {persona.summary_cards!.map((card, i) => (
            <SummaryCardItem key={i + card.title} card={card} />
          ))}
        </div>
      )}

      {/* ── Period bar ── */}
      <div style={{
        display:        "flex",
        alignItems:     "center",
        justifyContent: "space-between",
      }}>
        <span style={{
          fontFamily:    CHART_NUM_FONT,
          fontSize:      11,
          color:         palette.ink4,
          letterSpacing: "0.04em",
          textTransform: "uppercase",
        }}>
          {period === "now" ? "Current values" : `Last ${period.toUpperCase()} · live from Tableau`}
        </span>
        <PeriodBar period={period} onChange={setPeriod} palette={palette} />
      </div>

      {/* ── KPI sections ── */}
      {persona.dashboard_sections.map((section) => (
        <section key={section.id}>

          {/* Section header — compact, no description to save vertical space */}
          <div style={{
            display:      "flex",
            alignItems:   "center",
            gap:          8,
            marginBottom: 8,
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
              {section.kpis.length} KPI{section.kpis.length !== 1 ? "s" : ""}
            </span>
          </div>

          {/* Compact KPI tile grid — 2 columns, smart spans */}
          <div style={{
            display:             "grid",
            gridTemplateColumns: "repeat(2, 1fr)",
            gap:                 10,
          }}>
            {section.kpis.map((kpi, index) => (
              <div
                key={kpi.id ?? `${section.id}-${index}`}
                style={{
                  gridColumn:     `span ${kpiColSpan(kpi, index, section.kpis.length)}`,
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
          </div>
        </section>
      ))}

      {/* ── KPI detail modal ── */}
      {expandedKpi && (
        <KpiModal
          kpi={expandedKpi}
          workbookId={workbookId}
          period={period}
          onClose={handleClose}
        />
      )}

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
