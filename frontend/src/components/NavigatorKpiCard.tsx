// ─── NavigatorKpiCard ────────────────────────────────────────────────────────
// Renders one KPI from the Intelligence Config.
//
// Data flow:
//   1. Fetch live rows from /api/viewdata once on mount (cached across cards)
//   2. Compute headline from rows using the agent-defined projection method
//   3. Pass rows to NavigatorKpiChart for rendering
//
// Period behaviour:
//   "now" → L1 value (pipeline-computed snapshot)
//   "7d"  → FORWARD projection: where will this metric be in the next 7 days?
//   "30d" → FORWARD projection: where will this metric be in the next 30 days?
//
// The projection uses l2_projection.method (ratio/growth_rate/daily_rate); snapshots omit it
// applied to current Tableau rows to extrapolate the trend forward.
//
// Layout:
//   ┌──────────────────────────────────────────────┐
//   │  KPI Name  [L1]         7D  TREND ▲12%  [i] │
//   │  74.4 %                                      │
//   │  Description text                            │
//   ├──────────────────────────────────────────────┤
//   │  [  ECharts chart — live data  ]             │
//   └──────────────────────────────────────────────┘

import { useState, useRef, useEffect, useMemo, useCallback, type CSSProperties } from "react";
import { useChartTheme } from "../context/ChartThemeContext";
import { NavigatorKpiChart } from "./NavigatorKpiChart";
import { CHART_FONT, CHART_NUM_FONT } from "./charts/chartTheme";
import { api } from "../api/client";
import type { NavigatorKPI } from "../types/navigator";
import type { Period } from "./NavigatorCanvas";
import {
  computeL1Value,
  resolvePeriodHeadline,
  l1MatchesConfig,
} from "../lib/metricCompute";
import { formatKpiDescription } from "../lib/kpiDisplay";

// ── Module-level viewdata cache ───────────────────────────────────────────────
// Shared across all KPI cards on the same page. Key = "workbookId::viewName".
// Prevents fetching the same Tableau view 6-10x when multiple KPIs share it.
// Cleared automatically when the workbook changes (different workbookId).
const _viewCache = new Map<string, Record<string, unknown>[]>();
const _inFlight  = new Map<string, Promise<Record<string, unknown>[]>>();
/** Workbook-level id→name maps merged from Hyper /viewdata responses. */
const _workbookDimLabels = new Map<string, Record<string, Record<string, string>>>();

export function workbookDimensionLabels(workbookId: string): Record<string, Record<string, string>> {
  return _workbookDimLabels.get(workbookId) ?? {};
}

export function mergeWorkbookDimensionLabels(
  workbookId: string,
  maps: Record<string, Record<string, string>>,
): void {
  if (!maps || !Object.keys(maps).length) return;
  const prev = _workbookDimLabels.get(workbookId) ?? {};
  const merged: Record<string, Record<string, string>> = { ...prev };
  for (const [col, labels] of Object.entries(maps)) {
    merged[col] = { ...(prev[col] ?? {}), ...labels };
  }
  _workbookDimLabels.set(workbookId, merged);
}

function cachedViewData(workbookId: string, viewName: string): Promise<Record<string, unknown>[]> {
  const key = `${workbookId}::${viewName}`;
  // Return cached rows immediately if available
  const cached = _viewCache.get(key);
  if (cached) return Promise.resolve(cached);
  // Deduplicate in-flight requests — return the same promise if already fetching
  const existing = _inFlight.get(key);
  if (existing) return existing;
  // Start a new fetch
  const promise = api.viewData(workbookId, viewName)
    .then((res) => {
      const rows = (res.rows ?? []) as Record<string, unknown>[];
      _viewCache.set(key, rows);
      if (res.dimension_labels && Object.keys(res.dimension_labels).length) {
        mergeWorkbookDimensionLabels(workbookId, res.dimension_labels);
      }
      _inFlight.delete(key);
      return rows;
    })
    .catch(() => {
      _inFlight.delete(key);
      return [] as Record<string, unknown>[];
    });
  _inFlight.set(key, promise);
  return promise;
}

// ── Date parsing ──────────────────────────────────────────────────────────────

export function parseRowDate(val: unknown): Date | null {
  if (val === null || val === undefined) return null;
  const s = String(val).trim();
  if (!s) return null;

  // ISO date: "2024-01-15" or "2024-01-15T..."
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) {
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }

  // Year/Month slug: "2024/01" or "2024-01"
  const ym = s.match(/^(\d{4})[/-](\d{1,2})$/);
  if (ym) return new Date(+ym[1], +ym[2] - 1, 1);

  // "Month Year": "January 2024" or "Jan 2024"
  const MONTHS: Record<string, number> = {
    jan:1, feb:2, mar:3, apr:4, may:5, jun:6,
    jul:7, aug:8, sep:9, oct:10, nov:11, dec:12,
  };
  const my = s.match(/([A-Za-z]{3})[a-z]*[\s-]+(\d{4})/);
  if (my) {
    const m = MONTHS[my[1].toLowerCase()];
    if (m) return new Date(+my[2], m - 1, 1);
  }

  // "Q1 2024"
  const q = s.match(/Q(\d)\s+(\d{4})/i);
  if (q) return new Date(+q[2], (+q[1] - 1) * 3, 1);

  // Plain year: "2024"
  const yr = s.match(/^(\d{4})$/);
  if (yr) return new Date(+yr[1], 0, 1);

  // Fallback
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

// ── L1 value formatter ────────────────────────────────────────────────────────

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

// ── Trend badge ───────────────────────────────────────────────────────────────

interface TrendBadgeProps {
  direction: "up" | "down" | "flat" | null;
  pct:       number | null;
}

function TrendBadge({ direction, pct }: TrendBadgeProps) {
  const { palette } = useChartTheme();
  if (!direction || pct === null) return null;

  const color = direction === "up" ? palette.green : direction === "down" ? palette.red : palette.ink3;
  const arrow = direction === "up" ? "▲" : direction === "down" ? "▼" : "→";

  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 3,
      fontFamily: CHART_NUM_FONT, fontSize: 12, fontWeight: 600,
      letterSpacing: "0.02em", fontVariantNumeric: "tabular-nums",
      color,
    }}>
      {arrow} {Math.abs(pct).toFixed(1)}%
    </span>
  );
}

// ── Explanation popover ───────────────────────────────────────────────────────

interface ExplanationPopoverProps {
  kpi:          NavigatorKPI;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
}

function ExplanationPopover({ kpi, onMouseEnter, onMouseLeave }: ExplanationPopoverProps) {
  const { palette } = useChartTheme();
  const ex = kpi.explanation ?? {};
  if (!ex.key_insight && !ex.risk) return null;

  return (
    <div
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      style={{
        position: "absolute",
        bottom: "calc(100% + 8px)",
        right: 0,
        width: 280,
        zIndex: 100,
        background: palette.bg,
        border: `1px solid ${palette.line2}`,
        borderRadius: 8,
        boxShadow: "0 8px 24px rgba(0,0,0,0.14)",
        padding: "12px 14px",
        display: "flex",
        flexDirection: "column",
        gap: 10,
        animation: "popoverIn 0.15s ease",
      }}
    >
      {/* Arrow */}
      <div style={{
        position: "absolute",
        bottom: -6, right: 14,
        width: 10, height: 10,
        background: palette.bg,
        border: `1px solid ${palette.line2}`,
        borderTop: "none", borderLeft: "none",
        transform: "rotate(45deg)",
      }} />

      {ex.key_insight && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{
            fontFamily: CHART_NUM_FONT, fontSize: 12, fontWeight: 700,
            letterSpacing: "0.04em", textTransform: "uppercase", color: palette.accent,
          }}>
            Key Insight
          </span>
          <span style={{ fontFamily: CHART_FONT, fontSize: 12, color: palette.ink, lineHeight: 1.55 }}>
            {ex.key_insight}
          </span>
        </div>
      )}

      {ex.risk && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{
            fontFamily: CHART_NUM_FONT, fontSize: 12, fontWeight: 700,
            letterSpacing: "0.04em", textTransform: "uppercase", color: palette.red,
          }}>
            Risk
          </span>
          <span style={{ fontFamily: CHART_FONT, fontSize: 12, color: palette.ink, lineHeight: 1.55 }}>
            {ex.risk}
          </span>
        </div>
      )}
    </div>
  );
}

// ── Card component ────────────────────────────────────────────────────────────

interface Props {
  kpi:          NavigatorKPI;
  workbookId:   string;
  chartHeight?: number;
  period:       Period;
}

export function NavigatorKpiCard({ kpi, workbookId, chartHeight = 200, period }: Props) {
  const { palette } = useChartTheme();
  const isCardOnly = (kpi.chart?.type ?? "kpi_card").toLowerCase() === "kpi_card";
  const [showExplanation, setShowExplanation] = useState(false);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Data fetch ─────────────────────────────────────────────────────────────
  const [allRows, setAllRows]   = useState<Record<string, unknown>[]>([]);
  const [dataLoading, setDataLoading] = useState(true);

  const fetchData = useCallback(() => {
    const viewName = kpi.l1?.view_name;
    if (!viewName) {
      if (kpi.raw_data?.length) setAllRows(kpi.raw_data as Record<string, unknown>[]);
      setDataLoading(false);
      return;
    }
    setDataLoading(true);
    // Use the shared cache — deduplicates concurrent fetches for the same view
    cachedViewData(workbookId, viewName)
      .then((rows) => {
        if (rows.length > 0) {
          setAllRows(rows);
        } else if (kpi.raw_data?.length) {
          setAllRows(kpi.raw_data as Record<string, unknown>[]);
        }
      })
      .catch(() => {
        if (kpi.raw_data?.length) setAllRows(kpi.raw_data as Record<string, unknown>[]);
      })
      .finally(() => setDataLoading(false));
  }, [kpi.l1?.view_name, workbookId, kpi.raw_data]);

  // eslint-disable-next-line react-doctor/no-derived-state
  useEffect(() => { fetchData(); }, [fetchData]);

  // ── Live value computation ─────────────────────────────────────────────────
  // All values — L1 (Now) and L2 (7D/30D) — are computed from fresh Tableau rows.
  // The pipeline config stores only the definition (field name, formula method).

  // L1: aggregate from live rows.
  // Falls back to pipeline-stored value when live computation is impossible or
  // clearly wrong (e.g. conditional counts, ratios, string-field aggregations).
  const liveL1 = useMemo<number | null>(() => {
    const configValue = typeof kpi.l1?.value === "number" ? kpi.l1.value : null;
    if (!allRows.length) return configValue;
    const computed = computeL1Value(kpi, allRows);
    if (computed === null) return configValue;
    if (configValue !== null && !l1MatchesConfig(computed, configValue)) return configValue;
    const unit = kpi.l1?.unit ?? "";
    if (unit === "%" && (computed < -100 || computed > 100)) return configValue;
    return computed;
  }, [allRows, kpi]);

  const periodHeadline = useMemo(() => {
    const configL1 = typeof kpi.l1?.value === "number" ? kpi.l1.value : null;
    if (!allRows.length) return { value: configL1, layer: "L1" as const };
    return resolvePeriodHeadline(kpi, allRows, period, configL1);
  }, [allRows, period, kpi]);

  const displayLayer: "L1" | "L2" | "L3" = periodHeadline.layer;
  const displayValue = dataLoading ? (kpi.l1?.value ?? null) : (periodHeadline.value ?? liveL1);
  const unit         = kpi.l1?.unit ?? "";

  // ── RAG signal for this KPI ────────────────────────────────────────────────
  // Derived from existing data — no domain thresholds hardcoded.
  // risk text present → warning; large negative trend → warning/critical;
  // negative value with negative trend → critical
  const kpiSignal = (() => {
    const hasRisk   = !!kpi.explanation?.risk;
    const trendDown = kpi.trend_direction === "down";
    const trendPct  = kpi.trend_pct ?? 0;
    const val       = typeof displayValue === "number" ? displayValue : null;
    if (hasRisk && val !== null && val < 0 && trendDown && trendPct < -15) return "critical";
    if (hasRisk || (trendDown && trendPct < -10))                           return "warning";
    if (kpi.trend_direction === "up" && trendPct > 5)                       return "stable";
    return "neutral";
  })();
  // Use theme palette — same tones as summary cards, not jarring primary colors
  const signalColor = kpiSignal === "critical" ? palette.red
                    : kpiSignal === "warning"  ? palette.amber
                    : kpiSignal === "stable"   ? palette.green
                    : "";

  // ── Hover handlers ─────────────────────────────────────────────────────────
  function handleMouseEnter() {
    if (hideTimer.current) clearTimeout(hideTimer.current);
    setShowExplanation(true);
  }
  function handleMouseLeave() {
    hideTimer.current = setTimeout(() => setShowExplanation(false), 120);
  }

  const hasExplanation = !!(kpi.explanation?.key_insight || kpi.explanation?.risk);

  // ── Period badge (shown when not "now") ───────────────────────────────────
  const periodLabel = period === "7d" ? "+7D" : period === "30d" ? "+30D" : null;

  return (
    <div style={{
      position: "relative",
      background: palette.bg1,
      border: `1px solid ${showExplanation ? palette.line2 : palette.line}`,
      borderRadius: 6,
      padding: "14px 16px",
      display: "flex",
      flexDirection: "column",
      gap: 10,
      minWidth: 0,
      transition: "border-color 0.15s, box-shadow 0.15s",
      boxShadow: showExplanation ? "0 4px 12px rgba(0,0,0,0.12)" : "0 1px 3px rgba(0,0,0,0.06)",
    }}>

      {/* Floating popover */}
      {showExplanation && hasExplanation && (
        <>
          <ExplanationPopover
            kpi={kpi}
            onMouseEnter={handleMouseEnter}
            onMouseLeave={handleMouseLeave}
          />
          <style>{`
            @keyframes popoverIn {
              from { opacity: 0; transform: translateY(4px); }
              to   { opacity: 1; transform: translateY(0); }
            }
          `}</style>
        </>
      )}

      {/* Header — name + badges + trend + info icon */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8 }}>
        {/* Name + RAG signal dot + layer badge */}
        <div style={{ display: "flex", alignItems: "center", gap: 6, flex: 1, minWidth: 0 }}>
          {/* RAG signal dot — only shown when signal is non-neutral */}
          {signalColor && (
            <span style={{
              width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
              background: signalColor,
              boxShadow: `0 0 4px ${signalColor}80`,
            }} />
          )}
          <span style={{
            fontFamily: CHART_FONT,
            fontSize: 12,
            fontWeight: 600,
            letterSpacing: "0.04em",
            textTransform: "uppercase",
            lineHeight: 1.4,
            color: palette.ink2,
            textWrap: "balance" as CSSProperties["textWrap"],
          }}>
            {kpi.name}
          </span>

          {/* Layer badge — L1 when Now, L2 when viewing 7D/30D projection */}
          <span style={{
            fontFamily: CHART_NUM_FONT,
            fontSize: 11,
            fontWeight: 700,
            borderRadius: 3,
            padding: "1px 5px",
            letterSpacing: "0.04em",
            flexShrink: 0,
            transition: "color 0.2s, background 0.2s, border-color 0.2s",
            color: displayLayer === "L1" ? palette.accent
                 : displayLayer === "L3" ? palette.amber ?? palette.red
                 : palette.green,
            background: displayLayer === "L1" ? `${palette.accent}18`
                      : displayLayer === "L3" ? `${(palette.amber ?? palette.red)}18`
                      : `${palette.green}18`,
            border: `1px solid ${
              displayLayer === "L1" ? `${palette.accent}40`
            : displayLayer === "L3" ? `${(palette.amber ?? palette.red)}40`
            : `${palette.green}40`}`,
          }}>
            {displayLayer}
          </span>

          {/* Period badge — shown when L2 is active */}
          {periodLabel && (
            <span style={{
              fontFamily: CHART_NUM_FONT,
              fontSize: 11,
              fontWeight: 700,
              borderRadius: 3,
              padding: "1px 5px",
              letterSpacing: "0.04em",
              flexShrink: 0,
              color: palette.green,
              background: `${palette.green}15`,
              border: `1px solid ${palette.green}35`,
            }}>
              {periodLabel}
            </span>
          )}
        </div>

        {/* Trend + info icon */}
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
          <TrendBadge direction={kpi.trend_direction} pct={kpi.trend_pct} />
          {hasExplanation && (
            <button
              type="button"
              onMouseEnter={handleMouseEnter}
              onMouseLeave={handleMouseLeave}
              style={{
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                width: 18, height: 18, borderRadius: "50%",
                border: `1.5px solid ${showExplanation ? palette.accent : palette.line2}`,
                background: showExplanation ? palette.accent : "transparent",
                color: showExplanation ? palette.bg : palette.ink3,
                fontFamily: CHART_NUM_FONT, fontSize: 12, fontWeight: 700,
                cursor: "default", lineHeight: 1, padding: 0,
                transition: "border-color 0.15s, background 0.15s, color 0.15s",
                transform: showExplanation ? "scale(1.1)" : "scale(1)",
              }}
            >
              i
            </button>
          )}
        </div>
      </div>

      {/* Headline value — color driven by RAG signal */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span style={{
          fontFamily: CHART_NUM_FONT,
          fontSize: isCardOnly ? 32 : 26,
          fontWeight: 600,
          color: dataLoading ? palette.ink4 : (signalColor || palette.ink),
          letterSpacing: "-0.02em",
          lineHeight: 1,
          fontVariantNumeric: "tabular-nums",
          transition: "color 0.2s",
        }}>
          {formatL1(displayValue, unit)}
        </span>

        {/* Show live L1 as context when viewing 7D or 30D projection */}
        {period !== "now" && !dataLoading && liveL1 !== null && (
          <span style={{
            fontFamily: CHART_NUM_FONT,
            fontSize: 12,
            color: palette.ink4,
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
          fontSize: 12,
          color: palette.ink3,
          lineHeight: 1.5,
          margin: 0,
        }}>
          {formatKpiDescription(kpi.description)}
        </p>
      )}

      {/* Chart — receives pre-fetched & filtered rows */}
      {!isCardOnly && (
        <div style={{ flex: 1, minHeight: 0 }}>
          <NavigatorKpiChart
            kpi={kpi}
            rows={allRows}
            overrideValue={displayValue}
            loading={dataLoading}
            period={period}
            height={chartHeight}
            dimensionLabelMaps={workbookDimensionLabels(workbookId)}
          />
        </div>
      )}
    </div>
  );
}
