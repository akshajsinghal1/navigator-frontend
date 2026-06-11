// ─── NavigatorKpiChart ────────────────────────────────────────────────────────
// Pure chart renderer — receives pre-fetched, period-filtered rows from
// NavigatorKpiCard and turns them into an ECharts option.
//
// No data fetching here. All fetch / filter / L2 computation lives in
// NavigatorKpiCard so both the headline number and the chart always use
// the same filtered dataset.

import { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import type { NavigatorKPI, L2Projection } from "../types/navigator";
import {
  resolveChartAggregation,
  hasL3ForecastData,
  temporalGroupKey,
  filterRowsForPeriod,
  breakdownRawKey,
  resolveL3SeriesForecast,
  computeL2ProjectionValue,
  showsL2ProjectionOnChart,
  dimensionDisplayLabel,
  labelsForDimensionColumn,
  buildIdNameLabelsFromRows,
  mergeDimensionLabels,
} from "../lib/metricCompute";
import { useChartTheme } from "../context/ChartThemeContext";
import { CHART_FONT, CHART_NUM_FONT, chartTooltip, getChartPalette, translucent } from "./charts/chartTheme";
import { parseRowDate } from "./NavigatorKpiCard";
import type { Period } from "./NavigatorCanvas";

// ── Utilities ─────────────────────────────────────────────────────────────────

function norm(s: string): string {
  return String(s).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

/**
 * Find the column in `rows` whose name best matches `hint`.
 * Priority: exact → substring → word match.
 */
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

function autoXCol(rows: Record<string, unknown>[], exclude?: string | null): string | null {
  if (!rows.length) return null;
  const cols = Object.keys(rows[0]).filter((c) => c !== exclude);

  const temporal = cols.find((c) => {
    const n = norm(c);
    return (
      n.includes("date") || n.includes("month") || n.includes("year") ||
      n.includes("period") || n.includes("quarter") || n.includes("week")
    );
  });
  if (temporal) return temporal;

  const label = cols.find(
    (c) => typeof rows[0][c] === "string" && parseNum(rows[0][c]) === null
  );
  if (label) return label;

  return cols.find((c) => typeof rows[0][c] === "string") ?? null;
}

function autoYCol(rows: Record<string, unknown>[], xCol: string | null): string | null {
  if (!rows.length) return null;
  const cols = Object.keys(rows[0]).filter((c) => c !== xCol);
  return cols.find((c) => parseNum(rows[0][c]) !== null) ?? null;
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

// ── RAG (Red / Amber / Green) signal system ──────────────────────────────────
// Generic — no hardcoded domain thresholds. Priority order:
//   1. Severity-mapped categorical column (workbook already classified risk)
//   2. All-negative numeric → most negative = worst = RED
//   3. Statistical outliers (>1.5σ from mean in "bad" direction = RED)
//   4. Fallback → NEUTRAL (no override)

// RAG colors come from the theme palette — same tones used in summary cards.
// Resolved at render time so they match the light/dark theme automatically.
// Not hardcoded here; palette.green / .amber / .red are set in chartTheme.ts.
const RAG_NEUTRAL = ""; // use theme default when no signal

type RAGSignal = "critical" | "warning" | "stable" | "neutral";

/** Derive RAG signal for a single numeric value given the full dataset. */
function deriveRAGSignal(value: number, allValues: number[]): RAGSignal {
  if (!allValues.length) return "neutral";
  const allNeg = allValues.every((v) => v <= 0);
  if (allNeg) {
    // All negative → most negative = worst
    const sorted = [...allValues].sort((a, b) => a - b); // ascending, worst first
    const n = sorted.length;
    const rank = sorted.indexOf(value);
    if (rank < Math.ceil(n / 3)) return "critical";
    if (rank < Math.ceil((2 * n) / 3)) return "warning";
    return "stable";
  }
  // Statistical: compute mean + std, flag outliers in "bad" direction
  const mean = allValues.reduce((a, b) => a + b, 0) / allValues.length;
  const std  = Math.sqrt(allValues.reduce((a, b) => a + (b - mean) ** 2, 0) / allValues.length);
  if (!std) return "neutral";
  const z = (value - mean) / std;
  // For negative deviation (below average = bad in most BI metrics):
  if (z < -1.5) return "critical";
  if (z < -0.75) return "warning";
  return "stable";
}

type PaletteSubset = { green: string; amber: string; red: string };

/** Return the theme-consistent RAG color for a signal level. */
function ragColor(signal: RAGSignal, p: PaletteSubset): string {
  if (signal === "critical") return p.red;
  if (signal === "warning")  return p.amber;
  if (signal === "stable")   return p.green;
  return RAG_NEUTRAL;
}

/** Map a numeric value to a palette-consistent RAG color using the full dataset. */
function ragColorForValue(value: number, allValues: number[], p: PaletteSubset): string {
  return ragColor(deriveRAGSignal(value, allValues), p);
}

// ── Confidence interval detection ─────────────────────────────────────────────
// Finds upper/lower bound columns in rows for rendering prediction bands.
// Works automatically for any Tableau view that has interval columns.

function findConfidenceCols(
  rows: Record<string, unknown>[],
  yCol: string,
): { lower: string | null; upper: string | null } {
  if (!rows.length) return { lower: null, upper: null };
  const cols = Object.keys(rows[0]).filter((c) => c !== yCol);

  // Keywords that signal a confidence/prediction column
  const LOWER = ["lower", "low", " min", "p10", "lb", "lcl"];
  const UPPER = ["upper", "high", " max", "p90", "ub", "ucl"];
  const CONTEXT = ["interval", "bound", "confidence", "prediction", "forecast"];

  const isContext = (n: string) => CONTEXT.some((k) => n.includes(k));
  const isNumericCol = (col: string) =>
    rows.slice(0, 5).some((r) => parseNum(r[col]) !== null);

  const lower = cols.find((c) => {
    const n = norm(c);
    return LOWER.some((k) => n.includes(k)) && (isContext(n) || isNumericCol(c));
  }) ?? null;

  const upper = cols.find((c) => {
    const n = norm(c);
    return UPPER.some((k) => n.includes(k)) && (isContext(n) || isNumericCol(c));
  }) ?? null;

  return { lower, upper };
}

function aggregate(vals: number[], method: string): number {
  if (!vals.length) return 0;
  switch (method.toLowerCase()) {
    case "avg":   return vals.reduce((a, b) => a + b, 0) / vals.length;
    case "count": return vals.length;
    case "max":   return Math.max(...vals);
    case "min":   return Math.min(...vals);
    default:      return vals.reduce((a, b) => a + b, 0);
  }
}

/**
 * Bucket an x-axis value for display:
 * - ISO datetimes ("2026-04-05T02:00:00") → date only ("2026-04-05")
 * - Everything else → unchanged
 * This collapses hourly rows into daily buckets so the chart renders
 * one point per day rather than one per hour.
 */
function bucketKey(val: string): string {
  const iso = val.match(/^(\d{4}-\d{2}-\d{2})[T ]/);
  return iso ? iso[1] : val;
}

function monthYearToNum(s: string): number {
  const months: Record<string, number> = {
    jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6,
    jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12,
  };
  // YYYY-MM-DD full date — e.g. "2026-04-05" (produced by bucketKey)
  const ymd = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (ymd) return parseInt(ymd[1]) * 10000 + parseInt(ymd[2]) * 100 + parseInt(ymd[3]);
  // MM/DD/YYYY or M/D/YYYY — e.g. "11/3/2026", "10/27/2026"
  const mdy = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (mdy) return parseInt(mdy[3]) * 10000 + parseInt(mdy[1]) * 100 + parseInt(mdy[2]);
  // YYYY-MM or YYYY/MM — e.g. "2026-11"
  const ym = s.match(/(\d{4})[/-](\d{1,2})/);
  if (ym) return parseInt(ym[1]) * 100 + parseInt(ym[2]);
  // "Month D, YYYY" or "Month DD, YYYY" — e.g. "May 31, 2026", "June 2, 2026"
  // Must check BEFORE "Month YYYY" to avoid partial match
  const mdy2 = s.match(/([A-Za-z]{3})[a-z]*\s+(\d{1,2}),?\s*(\d{4})/);
  if (mdy2) {
    const m = months[mdy2[1].toLowerCase()] ?? 0;
    return parseInt(mdy2[3]) * 10000 + m * 100 + parseInt(mdy2[2]);
  }
  // "Month YYYY" — e.g. "November 2026"
  const my = s.match(/([A-Za-z]{3})[a-z]*\s*(\d{4})/);
  if (my) return parseInt(my[2]) * 100 + (months[my[1].toLowerCase()] ?? 0);
  // "Q1 2026"
  const q = s.match(/Q(\d)\s*(\d{4})/i);
  if (q) return parseInt(q[2]) * 100 + parseInt(q[1]) * 3;
  // "2026"
  const yr = s.match(/^(\d{4})$/);
  if (yr) return parseInt(yr[1]) * 100;
  // Bare integer (day number: "21", "26" etc.) — sort numerically
  const bare = parseInt(s);
  if (!isNaN(bare) && String(bare) === s.trim()) return bare;
  return 0;
}

interface XYPair { x: string; y: number; }

function groupBy(
  rows: Record<string, unknown>[],
  xCol: string,
  yCol: string,
  agg: string,
  xAxisType: string,
  sortOrder: string,
  kpi?: NavigatorKPI,
): XYPair[] {
  const groups: Record<string, number[]> = {};
  for (const row of rows) {
    const raw = String(row[xCol] ?? "(null)");
    const key = xAxisType === "temporal" && kpi
      ? temporalGroupKey(raw, kpi, rows, xCol)
      : xAxisType === "temporal"
        ? bucketKey(raw)
        : raw;
    if (agg === "count") {
      // Count: y-column doesn't need to be numeric — just tally rows per x group
      if (!groups[key]) groups[key] = [];
      groups[key].push(1);
    } else {
      const val = parseNum(row[yCol]);
      if (val === null) continue;
      if (!groups[key]) groups[key] = [];
      groups[key].push(val);
    }
  }
  let pairs: XYPair[] = Object.entries(groups).map(([x, vals]) => ({
    x,
    y: Math.round(aggregate(vals, agg) * 1000) / 1000,
  }));
  if (xAxisType === "temporal") {
    pairs.sort((a, b) => monthYearToNum(a.x) - monthYearToNum(b.x));
  } else if (sortOrder === "desc") {
    pairs.sort((a, b) => b.y - a.y);
  } else if (sortOrder === "asc") {
    pairs.sort((a, b) => a.y - b.y);
  }
  return pairs;
}

// ── Multi-series grouping (for stacked charts with a breakdown dimension) ───────
// Splits rows into one series per breakdown value, aligned on shared x categories.
// e.g. x=Month, by=Referral Status, y=Referral Count →
//   categories: [Jan, Feb, ...]
//   series: [{name:"Escalated", data:[...]}, {name:"Pending", data:[...]}, ...]
function groupByStacked(
  rows: Record<string, unknown>[],
  xCol: string,
  byCol: string,
  yCol: string,
  agg: string,
  xAxisType: string,
  breakdownLabels?: Record<string, string> | null,
  kpi?: NavigatorKPI,
): { categories: string[]; series: { name: string; data: number[] }[] } {
  const xKeys: string[] = [];
  const seenX = new Set<string>();
  const byKeys: string[] = [];
  const seenBy = new Set<string>();
  const cell: Record<string, Record<string, number[]>> = {};

  for (const row of rows) {
    const rawX = String(row[xCol] ?? "(null)");
    const xk = xAxisType === "temporal" && kpi
      ? temporalGroupKey(rawX, kpi, rows, xCol)
      : xAxisType === "temporal"
        ? bucketKey(rawX)
        : rawX;
    const bk = String(row[byCol] ?? "(null)");
    if (!seenX.has(xk))  { seenX.add(xk);   xKeys.push(xk); }
    if (!seenBy.has(bk)) { seenBy.add(bk);  byKeys.push(bk); }
    cell[xk] ??= {};
    cell[xk][bk] ??= [];
    if (agg === "count") {
      cell[xk][bk].push(1);
    } else {
      const v = parseNum(row[yCol]);
      if (v !== null) cell[xk][bk].push(v);
    }
  }

  if (xAxisType === "temporal") {
    xKeys.sort((a, b) => monthYearToNum(a) - monthYearToNum(b));
  }

  const series = byKeys.map((bk) => ({
    name: dimensionDisplayLabel(bk, breakdownLabels),
    data: xKeys.map((xk) => {
      const vals = cell[xk]?.[bk];
      return vals && vals.length ? Math.round(aggregate(vals, agg) * 1000) / 1000 : 0;
    }),
  }));
  return { categories: xKeys, series };
}

// ── Projection series builder ─────────────────────────────────────────────────
// Generates future x-labels + projected y-values to overlay as a dashed series.

interface ProjectedPoint { x: string; y: number; }

function buildProjectedPoints(
  historicalPairs: XYPair[],
  proj: L2Projection,
  rows: Record<string, unknown>[],
  xAxisType: string,
  horizonDays: number,
): ProjectedPoint[] {
  if (!historicalPairs.length || xAxisType !== "temporal") return [];

  // Detect avg period length from consecutive historical x-values
  // monthYearToNum returns 0 for unparseable labels (e.g. bare "January").
  // Fall back to using integer indices so projection points are still generated.
  const rawNums = historicalPairs.map((p) => monthYearToNum(p.x));
  const allZero = rawNums.every((n) => n === 0);
  const nums = allZero
    ? historicalPairs.map((_, i) => i + 1)  // index-based fallback
    : rawNums.filter((n) => n > 0);
  if (nums.length < 2) return [];

  // Compute average gap in "monthYearNum units" between consecutive points
  // monthYearToNum returns YYYY*100 + MM (or YYYY*100 for years)
  const gaps = nums.slice(1).map((n, i) => n - nums[i]);
  const avgGap = gaps.reduce((a, b) => a + b, 0) / gaps.length;

  // Convert avgGap to approximate days
  // gap of 1 = 1 month ≈ 30 days; gap of 100 = 1 year ≈ 365 days; gap of 3 ≈ 1 quarter
  const gapDays = avgGap >= 100 ? 365 : avgGap >= 3 ? 90 : avgGap >= 1 ? 30 : 7;

  // How many future periods to add
  const nPeriods = Math.max(1, Math.round(horizonDays / gapDays));

  // Generate future x-labels by advancing from the last historical point
  const lastNum = nums[nums.length - 1];
  const futureNums: number[] = [];
  for (let i = 1; i <= nPeriods; i++) {
    futureNums.push(lastNum + avgGap * i);
  }

  // Convert future nums back to label strings
  function numToLabel(n: number): string {
    const year  = Math.floor(n / 100);
    const month = Math.round(n % 100);
    if (month === 0) return String(year); // year-only
    const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    return `${MONTHS[(month - 1) % 12]} ${year}`;
  }

  // Compute projected value per future period
  const lastY    = historicalPairs[historicalPairs.length - 1].y;
  const valueCol = rows.length ? Object.keys(rows[0]).find(
    (c) => c.toLowerCase().includes(proj.value_field.toLowerCase())
  ) : null;

  const projectedY = (periodIndex: number): number => {
    switch (proj.method) {
      case "daily_rate": {
        // Each future period gets one period's worth of daily-rate revenue
        return (lastY / (gapDays || 30)) * gapDays;
      }
      case "ratio": {
        return lastY; // constant
      }
      case "growth_rate": {
        // Already computed CAGR in the card; here just repeat per-period growth
        if (historicalPairs.length < 2) return lastY;
        const first = historicalPairs[0].y;
        if (!first) return lastY;
        const growthPerPeriod = Math.pow(lastY / first, 1 / (historicalPairs.length - 1)) - 1;
        return lastY * Math.pow(1 + growthPerPeriod, periodIndex + 1);
      }
      default: return lastY;
    }
  };

  return futureNums.map((n, i) => ({
    x: numToLabel(n),
    y: Math.round(projectedY(i) * 1000) / 1000,
  }));
}

// ── Chart option builders ─────────────────────────────────────────────────────

type Palette = ReturnType<typeof import("./charts/chartTheme").getChartPalette>;

function buildOption(
  kpi: NavigatorKPI,
  rows: Record<string, unknown>[],
  palette: Palette,
  period: Period = "now",
  compact = false,
  maxPoints?: number,
  overrideValue?: number | null,
  dimensionLabelMaps?: Record<string, Record<string, string>>,
): object | null {
  // Compact axis base — no labels, no ticks, no gridlines (for tile view)
  const COMPACT_AXIS = {
    axisLabel: { show: false },
    axisTick:  { show: false },
    axisLine:  { show: false },
    splitLine: { show: false },
  };
  // Remap "table" (last-resort fallback) to a renderable type:
  //   2D categorical (x + breakdown) → heatmap_chart (with severity coloring)
  //   1D categorical                 → horizontal_bar_chart
  const chartRows = filterRowsForPeriod(rows, kpi, period);

  const rawType = (kpi.chart?.type ?? "kpi_card").toLowerCase();
  let ctype = rawType === "table"
    ? (kpi.chart?.breakdown_by ? "heatmap_chart" : "horizontal_bar_chart")
    : rawType;

  if (ctype === "kpi_card" || ctype === "scorecard") return null;
  if (!chartRows.length) return null;

  const xHint = kpi.chart?.x_axis;
  const yHint = kpi.chart?.y_axis ?? kpi.l1?.field_name;
  // Metric contract: same per-bucket aggregation as L1/L3 pipeline (lib/metricCompute).
  const agg = resolveChartAggregation(kpi);

  // ── Gauge chart ──────────────────────────────────────────────────────────
  if (ctype === "gauge_chart") {
    const l1Val = parseNum(kpi.l1?.value);
    const unit  = (kpi.l1?.unit ?? "").trim();
    const isPercent = unit === "%" || (l1Val !== null && l1Val > 0 && l1Val <= 100 && unit === "");
    // Use the period-resolved overrideValue (L1/L2/L3 already resolved by the
    // parent) so the gauge needle reflects the selected time horizon.
    const val   = overrideValue !== null && overrideValue !== undefined ? overrideValue : (l1Val ?? 0);
    const maxVal = isPercent ? 100 : Math.max(Math.abs(val) * 1.5, 100);
    const fmtDetail = (v: number) => isPercent ? `${v.toFixed(1)}%` : v.toLocaleString();
    return {
      backgroundColor: "transparent",
      series: [{
        type: "gauge",
        radius: "85%",
        startAngle: 200, endAngle: -20,
        min: 0, max: maxVal,
        splitNumber: 4,
        progress: { show: true, width: 12, itemStyle: { color: palette.accent } },
        axisLine: { lineStyle: { width: 12, color: [[1, palette.bg3]] } },
        axisTick:  { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        pointer:   { show: false },
        detail: {
          valueAnimation: true,
          formatter: fmtDetail,
          color: palette.ink,
          fontFamily: CHART_NUM_FONT,
          fontSize: 18,
          fontWeight: 600,
          offsetCenter: [0, "20%"],
        },
        data: [{ value: val, name: kpi.name }],
        title: { show: false },
      }],
    };
  }

  // ── Resolve x/y columns ──────────────────────────────────────────────────
  const xAxisType = kpi.chart?.x_axis_type || "categorical";
  const sortOrder = kpi.chart?.sort_order  || "none";

  let xCol = findColumn(chartRows, xHint ?? "") ?? autoXCol(chartRows);
  let yCol = findColumn(chartRows, yHint ?? "") ?? autoYCol(chartRows, xCol);

  if (xCol && xCol === yCol) xCol = autoXCol(chartRows, yCol);
  // Heatmap can work with 2 categorical dimensions + severity mapping — no numeric yCol needed.
  // Skip the early null-return for heatmap so its own barFallback logic can run.
  if (!xCol || xCol === yCol) return null;
  if (!yCol && ctype !== "heatmap_chart") return null;

  const breakdownHint = kpi.chart?.breakdown_by ?? null;
  const byColEarly = breakdownHint ? findColumn(chartRows, breakdownHint) : null;
  const dimLabels = mergeDimensionLabels(
    labelsForDimensionColumn(xCol, dimensionLabelMaps),
    labelsForDimensionColumn(byColEarly, dimensionLabelMaps),
    buildIdNameLabelsFromRows(chartRows, xCol),
    buildIdNameLabelsFromRows(chartRows, byColEarly),
    kpi.chart?.breakdown_labels,
  );
  const labelDim = (raw: string) => dimensionDisplayLabel(raw, dimLabels);

  // Auto-correct swapped x/y: if x is numeric and y is categorical the agent
  // put value and label on the wrong axes — swap so the chart renders correctly.
  // Skip for count aggregation — count doesn't require a numeric y column.
  if (agg !== "count") {
    const sample5 = chartRows.slice(0, 5);
    const xIsNumeric = sample5.some((r) => parseNum(r[xCol!]) !== null);
    const yIsNumeric = sample5.some((r) => parseNum(r[yCol!]) !== null);
    if (xIsNumeric && !yIsNumeric) {
      [xCol, yCol] = [yCol, xCol];
    }
  }

  let pairs = yCol ? groupBy(chartRows, xCol!, yCol, agg, xAxisType, sortOrder, kpi) : [];
  // Heatmaps build their own data grid directly from rows (including all-categorical views
  // like Risk Matrix where yCol is "Risk Category" — groupBy returns no numeric pairs).
  // Skip the pairs null-guard so the heatmap block is always reached.
  if (!pairs.length && ctype !== "heatmap_chart") return null;

  // Guard against degenerate "time series" — a sequential chart with a single
  // data point is meaningless (e.g. a single-value KPI view where the agent
  // wrongly picked line_chart over a non-existent date column). Fall back to
  // null so the tile just shows the headline number (kpi_card behaviour).
  const SEQUENTIAL_TYPES = new Set([
    "line_chart", "area_chart", "stacked_area_chart", "bar_chart", "stacked_bar_chart",
  ]);
  if (pairs.length <= 1 && SEQUENTIAL_TYPES.has(ctype)) return null;

  // Apply maxPoints AFTER aggregation so counts/sums are correct
  // For temporal: keep last N (most recent); for categorical: keep as-is (already sorted)
  if (maxPoints && pairs.length > maxPoints) {
    pairs = xAxisType === "temporal"
      ? pairs.slice(-maxPoints)           // last N periods
      : pairs.slice(0, maxPoints);        // top N categories
  }

  // Build projected future points when a period is selected and projection defined
  const l2Proj = kpi.l2_projection;
  const projPoints: ProjectedPoint[] =
    showsL2ProjectionOnChart(kpi, period) && l2Proj
      ? buildProjectedPoints(pairs, l2Proj, chartRows, xAxisType, period === "7d" ? 7 : 30)
      : [];

  const allX = [...pairs.map((p) => p.x), ...projPoints.map((p) => p.x)];
  const xData = pairs.map((p) => p.x);
  const yData = pairs.map((p) => p.y);
  const tt    = chartTooltip(palette);

  // Color palette for multi-series (stacked) charts
  const SERIES_COLORS = [
    palette.accent, palette.green, palette.amber, palette.red,
    palette.ink3, "#7B6CF6", "#0B7A75", "#C2554D",
  ];

  // Compact mode: y-axis labels for scale, NO x-axis labels (see modal for dates)
  // This keeps the tile clean — shape + scale is what matters in compact view
  const COMPACT_AXIS_Y = {  // for y-axis (numeric scale — keep visible)
    axisLine:  { lineStyle: { color: palette.line2 } },
    axisTick:  { show: false },
    axisLabel: {
      color:      palette.ink4,
      fontFamily: CHART_NUM_FONT,
      fontSize:   9,
    },
    splitLine: { lineStyle: { color: palette.line, type: "dashed" as const, opacity: 0.5 } },
  };
  const COMPACT_AXIS_X = {  // for x-axis — short labels, auto-skip, no rotation
    axisLine:  { lineStyle: { color: palette.line2 } },
    axisTick:  { show: false },
    axisLabel: {
      show:        true,
      color:       palette.ink4,
      fontFamily:  CHART_NUM_FONT,
      fontSize:    8,
      hideOverlap: true,
      // Short formatter: keeps day when there's a full date so daily data
      // doesn't collapse all to the same month label ("May May May...")
      formatter: (val: string) => {
        // "April 10, 2026" / "May 3, 2026" → "Apr 10" / "May 3"
        const mdy2 = val.match(/^([A-Za-z]{3})[a-z]*\s+(\d{1,2}),\s*\d{4}$/);
        if (mdy2) return `${mdy2[1]} ${mdy2[2]}`;
        // "11/3/2026" → "11/3"
        const mdy = val.match(/^(\d{1,2})\/(\d{1,2})\/\d{4}$/);
        if (mdy) return `${mdy[1]}/${mdy[2]}`;
        // "April 2026" / "November 2026" → "Apr" / "Nov"  (month+year, no day)
        const my = val.match(/^([A-Za-z]{3})[a-z]*\s+\d{4}$/);
        if (my) return my[1];
        // "April" alone → "Apr"
        const mo = val.match(/^([A-Za-z]{3})[a-z]*$/);
        if (mo) return mo[1];
        if (/^Q\d/i.test(val)) return val.slice(0, 2);
        return val.length > 7 ? val.slice(0, 7) : val;
      },
    },
    splitLine: { show: false },
  };
  const AXIS_BASE = compact ? COMPACT_AXIS_Y : {
    axisLine:  { lineStyle: { color: palette.line2 } },
    axisTick:  { show: false },
    axisLabel: { color: palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: 10 },
    splitLine: { lineStyle: { color: palette.line, type: "dashed" as const } },
  };
  // Compact grids — containLabel auto-fits labels; explicit sides prevent over-shrinking
  const compactGrid = compact
    ? { containLabel: true, left: "8%", right: "4%", top: 6, bottom: 4 }
    : null;
  // Horizontal bar compact: containLabel for y-axis (category names still shown)
  const compactHBarGrid = compact
    ? { containLabel: true, top: 6, bottom: 6 }
    : null;

  // ── Detect confidence interval columns ──────────────────────────────────
  // Automatically finds upper/lower prediction interval columns in the rows.
  // Works for any Tableau view that exports confidence bounds.
  const { lower: lowerCol, upper: upperCol } = findConfidenceCols(chartRows, yCol!);
  const hasCI = !!(lowerCol && upperCol);

  // Build grouped confidence data aligned to xData
  let lowerData: (number | null)[] = [];
  let upperData: (number | null)[] = [];
  if (hasCI) {
    const lowerMap = new Map<string, number[]>();
    const upperMap = new Map<string, number[]>();
    for (const row of chartRows) {
      const key = String(row[xCol!] ?? "(null)");
      const lo  = parseNum(row[lowerCol!]);
      const hi  = parseNum(row[upperCol!]);
      if (lo !== null) { if (!lowerMap.has(key)) lowerMap.set(key, []); lowerMap.get(key)!.push(lo); }
      if (hi !== null) { if (!upperMap.has(key)) upperMap.set(key, []); upperMap.get(key)!.push(hi); }
    }
    lowerData = xData.map((x) => {
      const vals = lowerMap.get(x);
      return vals?.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
    });
    upperData = xData.map((x) => {
      const vals = upperMap.get(x);
      return vals?.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
    });
  }

  // ── Multi-series charts (split by a breakdown dimension) ─────────────────
  // MUST come before the single-series line/area block — otherwise line_chart
  // with breakdown_by hits the single-series block first and returns early.

  const effectiveBreakdownBy: string | null = breakdownHint;

  const MULTI_SERIES_TYPES = new Set([
    "stacked_bar_chart", "stacked_area_chart", "line_chart", "area_chart",
  ]);
  if (MULTI_SERIES_TYPES.has(ctype) && effectiveBreakdownBy) {
    const byCol = findColumn(chartRows, effectiveBreakdownBy);
    if (byCol && byCol !== xCol && byCol !== yCol) {
      const { categories, series } = groupByStacked(
        chartRows, xCol!, byCol, yCol ?? byCol, agg, xAxisType, dimLabels, kpi,
      );
      if (series.length >= 1) {
        const isStackedArea = ctype === "stacked_area_chart";
        const isStackedBar  = ctype === "stacked_bar_chart";
        const isLine        = ctype === "line_chart";
        const isArea        = ctype === "area_chart";
        const stacked       = isStackedArea || isStackedBar;

        const L3_C          = palette.amber ?? "#E8A33A";
        const l3HorizonDays = period === "7d" ? 7 : 30;
        const showForecast  = !compact && period !== "now" && xAxisType === "temporal";
        const bySeriesL3    = kpi.l3_forecast_by_series ?? {};
        const aggregatePreds = kpi.l3_forecast?.predictions ?? [];
        // Breakdown charts: per-series L3 only — aggregate anchor was summing all
        // series last values and spiking the y-axis (e.g. 5 × 68% → 340).
        const hasL3BySeries = showForecast
          && !!effectiveBreakdownBy
          && Object.keys(bySeriesL3).length > 0;
        const hasL3Aggregate = showForecast && aggregatePreds.length > 0 && !hasL3BySeries;
        // Never fake a flat L2 extension — it reads as a broken forecast (esp. ratio/stable).
        const futureSlots = (hasL3BySeries || hasL3Aggregate) ? l3HorizonDays : 0;
        const futureLabels = futureSlots > 0
          ? Array.from({ length: futureSlots }, (_, i) => `+${i + 1}d`)
          : [];
        const extendedCats = futureSlots > 0 ? [...categories, ...futureLabels] : categories;

        const paddedSeries = futureSlots > 0
          ? series.map((s) => ({ ...s, data: [...s.data, ...new Array(futureSlots).fill(null)] }))
          : series;

        const histNulls = new Array(categories.length - 1).fill(null);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const overlaySeries: any[] = [];

        for (let i = 0; i < series.length; i++) {
          const s = series[i];
          const rawKey = breakdownRawKey(s.name, dimLabels);
          const lastVal = s.data[s.data.length - 1];
          const seriesColor = SERIES_COLORS[i % SERIES_COLORS.length];
          const l3Fc = resolveL3SeriesForecast(s.name, bySeriesL3, dimLabels);

          if (hasL3BySeries && l3Fc?.predictions?.length) {
            const preds = l3Fc.predictions.slice(0, l3HorizonDays);
            const lower = (l3Fc.lower_p10 ?? []).slice(0, l3HorizonDays);
            const upper = (l3Fc.upper_p90 ?? []).slice(0, l3HorizonDays);
            const bandStack = `l3band-${i}`;
            overlaySeries.push({
              name: s.name,
              type: "line",
              data: [...histNulls, lastVal, ...preds],
              smooth: true,
              symbol: "none",
              lineStyle: { color: seriesColor, width: 2, type: "dashed" },
              itemStyle: { color: seriesColor },
              z: 4,
            });
            if (lower.length) {
              overlaySeries.push({
                name: `_l3lo_${i}`,
                type: "line",
                data: [...histNulls, lastVal, ...lower],
                symbol: "none",
                lineStyle: { opacity: 0 },
                areaStyle: { color: "transparent" },
                stack: bandStack,
                tooltip: { show: false },
                z: 1,
              });
              overlaySeries.push({
                name: `_l3hi_${i}`,
                type: "line",
                data: [...histNulls, lastVal, ...upper].map((u, idx) => {
                  const lo = [...histNulls, lastVal, ...lower][idx];
                  return (u !== null && lo !== null) ? (u as number) - (lo as number) : null;
                }),
                symbol: "none",
                lineStyle: { opacity: 0 },
                areaStyle: { color: translucent(seriesColor, 0.12) },
                stack: bandStack,
                tooltip: { show: false },
                z: 1,
              });
            }
          }
        }

        const l3SlicePreds = hasL3Aggregate
          ? (kpi.l3_forecast!.predictions).slice(0, l3HorizonDays) : [];
        const l3SliceLower = hasL3Aggregate
          ? (kpi.l3_forecast!.lower_p10 ?? []).slice(0, l3HorizonDays) : [];
        const l3SliceUpper = hasL3Aggregate
          ? (kpi.l3_forecast!.upper_p90 ?? []).slice(0, l3HorizonDays) : [];
        const lastVals = series
          .map((s) => s.data[s.data.length - 1])
          .filter((v): v is number => typeof v === "number");
        const lastAnchor = lastVals.length === 1
          ? lastVals[0]
          : typeof overrideValue === "number"
            ? overrideValue
            : lastVals.length
              ? lastVals.reduce((a, b) => a + b, 0) / lastVals.length
              : null;
        const l3LineData   = hasL3Aggregate ? [...histNulls, lastAnchor, ...l3SlicePreds] : [];
        const l3LowerData  = hasL3Aggregate ? [...histNulls, lastAnchor, ...l3SliceLower] : [];
        const l3UpperData  = hasL3Aggregate ? [...histNulls, lastAnchor, ...l3SliceUpper] : [];

        return {
          backgroundColor: "transparent",
          animationDuration: compact ? 200 : 600,
          color: SERIES_COLORS,
          tooltip: { ...tt, trigger: "axis", axisPointer: { type: isStackedBar ? "shadow" : "line" } },
          legend: compact
            // "plain" wraps to multiple rows → 10 items don't crowd into 1 scroll line
            ? { type: "plain", top: 2, orient: "horizontal",
                icon: "circle", itemWidth: 6, itemHeight: 6, itemGap: 8,
                textStyle: { color: palette.ink4, fontFamily: CHART_NUM_FONT, fontSize: 8 },
                data: paddedSeries.map((s) => s.name) }
            : {
                type: "scroll", bottom: 0, icon: "roundRect", itemWidth: 10, itemHeight: 10,
                textStyle: { color: palette.ink3, fontFamily: CHART_FONT, fontSize: 11 },
                data: [
                  ...paddedSeries.map((s) => s.name),
                  ...(hasL3Aggregate ? ["L3 Forecast"] : []),
                ],
              },
          grid: compactGrid
            ? { ...compactGrid, top: compact ? Math.max(16, Math.ceil(series.length / 5) * 14) : 12 }
            : { containLabel: true, left: "8%", right: "4%", top: 12, bottom: 32 },
          xAxis: {
            ...AXIS_BASE, type: "category", data: extendedCats,
            axisLabel: compact ? COMPACT_AXIS_X.axisLabel
              : {
                  ...AXIS_BASE.axisLabel,
                  rotate: extendedCats.length > 8 ? 35 : 0,
                  hideOverlap: true,
                  ...(xAxisType !== "temporal" && Object.keys(dimLabels).length
                    ? { formatter: (val: string) => labelDim(val) }
                    : {}),
                },
          },
          yAxis: { ...AXIS_BASE, type: "value", scale: (isLine || isArea) && !stacked },
          series: [
            // Historical breakdown series (padded with nulls for future slots)
            ...paddedSeries.map((s, i) => {
              const c = SERIES_COLORS[i % SERIES_COLORS.length];
              const base = { name: s.name, data: s.data, ...(stacked ? { stack: "total" } : {}) };
              if (isStackedBar) return { ...base, type: "bar", barMaxWidth: 40,
                itemStyle: { color: c, borderRadius: i === series.length - 1 ? [3,3,0,0] : [0,0,0,0] } };
              return { ...base, type: "line", smooth: true, symbol: "none",
                lineStyle: { width: compact ? 1.5 : 2, color: c },
                ...(isStackedArea || isArea
                  ? { areaStyle: { color: translucent(c, stacked ? 0.45 : 0.15) } }
                  : {}),
              };
            }),
            ...overlaySeries,
            ...(hasL3Aggregate && l3SliceLower.length ? [{
              name: "L3 Lower",
              type: "line",
              data: l3LowerData,
              symbol: "none",
              lineStyle: { opacity: 0 },
              areaStyle: { color: "transparent" },
              stack: "l3band-agg",
              tooltip: { show: false },
              z: 1,
            }] : []),
            ...(hasL3Aggregate && l3SliceUpper.length ? [{
              name: "L3 Band",
              type: "line",
              data: l3UpperData.map((u, i) => {
                const lo = l3LowerData[i];
                return (u !== null && lo !== null) ? (u as number) - (lo as number) : null;
              }),
              symbol: "none",
              lineStyle: { opacity: 0 },
              areaStyle: { color: translucent(L3_C, 0.12) },
              stack: "l3band-agg",
              tooltip: { show: false },
              z: 1,
            }] : []),
            ...(hasL3Aggregate && l3SlicePreds.length ? [{
              name: "L3 Forecast",
              type: "line",
              data: l3LineData,
              smooth: true,
              symbol: "none",
              lineStyle: { color: L3_C, width: 2.5, type: "dashed" },
              itemStyle: { color: L3_C },
              z: 4,
            }] : []),
          ],
        };
      }
    }
  }

  // ── Line / Area (single series — no breakdown) ───────────────────────────
  if (ctype === "line_chart" || ctype === "area_chart") {
    const hasProj  = projPoints.length > 0;
    const hasL3    = !compact && hasL3ForecastData(kpi) && !!kpi.l3_forecast?.predictions?.length;
    const l3Preds  = kpi.l3_forecast?.predictions ?? [];
    const l3Lower  = kpi.l3_forecast?.lower_p10 ?? [];
    const l3Upper  = kpi.l3_forecast?.upper_p90 ?? [];

    // Extend x-axis with future day labels for L3 (Day+1 … Day+N)
    const l3XLabels = hasL3
      ? l3Preds.map((_, i) => `+${i + 1}d`)
      : [];
    const extendedX = hasL3
      ? [...(hasProj ? allX : xData), ...l3XLabels]
      : (hasProj ? allX : xData);

    const projYData = hasProj
      ? [...new Array(xData.length - 1).fill(null), yData[yData.length - 1], ...projPoints.map((p) => p.y)]
      : [];

    // L3 prediction series — starts at the last historical point
    const l3YData = hasL3
      ? [
          ...new Array((hasProj ? allX.length : xData.length) - 1).fill(null),
          yData[yData.length - 1],    // connect to last actual point
          ...l3Preds,
        ]
      : [];
    const l3LowerData = hasL3
      ? [...new Array((hasProj ? allX.length : xData.length) - 1).fill(null), yData[yData.length - 1], ...l3Lower]
      : [];
    const l3UpperData = hasL3
      ? [...new Array((hasProj ? allX.length : xData.length) - 1).fill(null), yData[yData.length - 1], ...l3Upper]
      : [];

    return {
      backgroundColor: "transparent",
      animationDuration: compact ? 200 : 600,
      tooltip: { ...tt, trigger: "axis" },
      grid: compactGrid ?? { containLabel: true, left: "8%", right: "4%", top: hasCI ? 16 : 12, bottom: 8 },
      xAxis: {
        ...AXIS_BASE,
        type: "category",
        data: extendedX,
        axisLabel: compact
          ? COMPACT_AXIS_X.axisLabel
          : { ...AXIS_BASE.axisLabel, rotate: extendedX.length > 8 ? 30 : 0, hideOverlap: true },
      },
      // Always auto-scale: compact tile and expanded modal should both zoom to data range.
      yAxis: { ...AXIS_BASE, type: "value", scale: true },
      series: [
        // Confidence band — lower bound (invisible fill base)
        ...(hasCI ? [{
          name: "Lower Bound",
          type: "line",
          data: lowerData,
          symbol: "none",
          lineStyle: { opacity: 0 },
          areaStyle: { color: "transparent" },
          stack: "confidence",
          tooltip: { show: false },
        }] : []),
        // Confidence band — upper fill (stacks on lower → fills the gap)
        ...(hasCI ? [{
          name: "Confidence Band",
          type: "line",
          data: upperData.map((u, i) => {
            const lo = lowerData[i];
            return (u !== null && lo !== null) ? u - lo : null;
          }),
          symbol: "none",
          lineStyle: { opacity: 0 },
          areaStyle: { color: translucent(palette.accent, 0.12) },
          stack: "confidence",
          tooltip: { show: false },
        }] : []),
        // Main series
        {
          name: kpi.name,
          type: "line",
          data: hasProj ? [...yData, ...new Array(projPoints.length).fill(null)] : yData,
          smooth: false,
          symbol: "none",
          lineStyle: { color: palette.accent, width: 2 },
          areaStyle: hasCI ? undefined : { color: translucent(palette.accent, 0.08) },
          z: 3,
        },
        // L2 Projection dashed series
        ...(hasProj ? [{
          name: "L2 Projection",
          type: "line",
          data: projYData,
          smooth: false,
          symbol: "circle",
          symbolSize: 5,
          lineStyle: { color: palette.accent, width: 2, type: "dashed", opacity: 0.6 },
          itemStyle: { color: palette.accent, opacity: 0.6 },
          areaStyle: { color: translucent(palette.accent, 0.03) },
          z: 3,
        }] : []),
        // L3 TimesFM confidence band — lower (invisible base)
        ...(hasL3 ? [{
          name: "L3 Lower",
          type: "line",
          data: l3LowerData,
          symbol: "none",
          lineStyle: { opacity: 0 },
          areaStyle: { color: "transparent" },
          stack: "l3band",
          tooltip: { show: false },
          z: 1,
        }] : []),
        // L3 TimesFM confidence band — upper fill
        ...(hasL3 ? [{
          name: "L3 Band",
          type: "line",
          data: l3UpperData.map((u, i) => {
            const lo = l3LowerData[i];
            return (u !== null && lo !== null) ? u - lo : null;
          }),
          symbol: "none",
          lineStyle: { opacity: 0 },
          areaStyle: { color: translucent(palette.green, 0.12) },
          stack: "l3band",
          tooltip: { show: false },
          z: 1,
        }] : []),
        // L3 TimesFM prediction line
        ...(hasL3 ? [{
          name: "L3 Forecast",
          type: "line",
          data: l3YData,
          smooth: true,
          symbol: "none",
          lineStyle: { color: palette.green, width: 2, type: "dashed" },
          itemStyle: { color: palette.green },
          z: 4,
        }] : []),
      ],
    };
  }

  // ── Bar ──────────────────────────────────────────────────────────────────

  if (ctype === "bar_chart" || ctype === "stacked_bar_chart") {
    const hasProj = projPoints.length > 0;
    const projBarData = hasProj
      ? [...new Array(xData.length).fill(null), ...projPoints.map((p) => p.y)]
      : [];

    // Whisker marks for confidence intervals
    const markLineData = hasCI
      ? xData.flatMap((x, i) => {
          const lo = lowerData[i];
          const hi = upperData[i];
          if (lo === null || hi === null) return [];
          return [
            [{ coord: [x, lo], symbol: "none" }, { coord: [x, hi], symbol: "none" }],
          ];
        })
      : [];

    return {
      backgroundColor: "transparent",
      animationDuration: compact ? 200 : 600,
      tooltip: { ...tt, trigger: "axis", axisPointer: { type: "shadow" } },
      grid: compactGrid ?? { containLabel: true, left: "8%", right: "4%", top: 12, bottom: 8 },
      xAxis: {
        ...AXIS_BASE,
        type: "category",
        data: hasProj ? allX : xData,
        axisLabel: compact
          ? COMPACT_AXIS_X.axisLabel
          : {
              ...AXIS_BASE.axisLabel,
              rotate: allX.length > 8 ? 35 : 0,
              hideOverlap: true,
              ...(xAxisType !== "temporal" && Object.keys(dimLabels).length
                ? { formatter: (val: string) => labelDim(val) }
                : {}),
            },
      },
      yAxis: { ...AXIS_BASE, type: "value" },  // bar: always start from 0
      series: [
        {
          name: kpi.name,
          type: "bar",
          data: hasProj ? [...yData, ...new Array(projPoints.length).fill(null)] : yData,
          barMaxWidth: 40,
          itemStyle: {
            color: {
              type: "linear", x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: palette.accent },
                { offset: 1, color: translucent(palette.accent, 0.4) },
              ],
            },
            borderRadius: [3, 3, 0, 0],
          },
          // Whisker lines for confidence intervals
          ...(hasCI && markLineData.length ? {
            markLine: {
              silent: true,
              symbol: ["none", "none"],
              lineStyle: { color: palette.ink3, width: 1.5, type: "solid" },
              data: markLineData,
            },
          } : {}),
        },
        ...(hasProj ? [{
          name: "L2 Projection",
          type: "bar",
          data: projBarData,
          barMaxWidth: 40,
          itemStyle: {
            color: translucent(palette.accent, 0.35),
            borderColor: palette.accent,
            borderWidth: 1.5,
            borderType: "dashed",
            borderRadius: [3, 3, 0, 0],
          },
        }] : []),
      ],
    };
  }

  // ── Horizontal bar / Map ─────────────────────────────────────────────────
  if (ctype === "horizontal_bar_chart" || ctype === "map_chart") {
    const hPairs =
      ctype === "map_chart"
        ? pairs.toSorted((a, b) => b.y - a.y).slice(0, 20)
        : pairs;
    return {
      backgroundColor: "transparent",
      animationDuration: compact ? 200 : 600,
      tooltip: { ...tt, trigger: "axis", axisPointer: { type: "shadow" } },
      grid: compactHBarGrid ?? { containLabel: true, left: "2%", right: "4%", top: 8, bottom: 8 },
      xAxis: { ...AXIS_BASE, type: "value" },
      yAxis: {
        ...AXIS_BASE,
        type: "category",
        data: hPairs.map((p) => p.x),
        // compact: width=82 fits ~10-12 char names within the 90px left grid
        // full: width=100 as before
        axisLabel: {
          ...AXIS_BASE.axisLabel,
          width:    compact ? 82 : 100,
          overflow: "truncate",
          formatter: (val: string) => labelDim(val),
        },
      },
      series: [{
        name: kpi.name,
        type: "bar",
        // RAG: color each bar by its signal relative to the full dataset
        data: hPairs.map((p) => {
          const allY = hPairs.map((h) => h.y);
          const c = ragColorForValue(p.y, allY, palette);
          return {
            value: p.y,
            itemStyle: c
              ? { color: c, borderRadius: [0, 3, 3, 0] }
              : {
                  color: { type: "linear", x: 0, y: 0, x2: 1, y2: 0,
                    colorStops: [{ offset: 0, color: translucent(palette.accent, 0.7) }, { offset: 1, color: palette.accent }] },
                  borderRadius: [0, 3, 3, 0],
                },
          };
        }),
        barMaxWidth: 20,
      }],
    };
  }

  // ── Pie / Donut ──────────────────────────────────────────────────────────
  if (ctype === "pie_chart" || ctype === "donut_chart") {
    // If xCol produces too many slices (e.g. Order_Date with 100+ dates),
    // try swapping to yCol if it has fewer distinct values (e.g. Ship_Status).
    // Pie charts should have ≤ 15 slices to be readable.
    const xDistinct = new Set(chartRows.map((r) => String(r[xCol!] ?? ""))).size;
    if (xDistinct > 15 && yCol) {
      const yDistinct = new Set(chartRows.map((r) => String(r[yCol!] ?? ""))).size;
      if (yDistinct < xDistinct && yDistinct <= 15) {
        [xCol, yCol] = [yCol, xCol];
      }
    }
    // Re-compute pairs with the (possibly swapped) columns
    const piePairs = groupBy(chartRows, xCol!, yCol!, agg, xAxisType, sortOrder, kpi);
    if (!piePairs.length) return null;

    const COLORS = [palette.accent, palette.green, palette.amber, palette.red, palette.ink2];
    const pieData = piePairs.map((p, i) => ({
      name: labelDim(p.x),
      value: p.y,
      itemStyle: { color: COLORS[i % COLORS.length] },
    }));
    const isDonut = ctype === "donut_chart";
    return {
      backgroundColor: "transparent",
      tooltip: {
        ...tt,
        trigger: "item",
        formatter: (p: { name: string; value: number; percent: number }) =>
          `${p.name}: ${p.value.toLocaleString()} (${p.percent}%)`,
      },
      legend: {
        bottom: 0,
        type: "scroll",
        textStyle: { color: palette.ink3, fontFamily: CHART_FONT, fontSize: 10 },
        icon: "circle", itemWidth: 8,
      },
      series: [{
        name: kpi.name,
        type: "pie",
        radius: isDonut ? ["35%", "60%"] : "60%",
        center: ["50%", "40%"],
        data: pieData,
        label: { show: false },
        emphasis: {
          itemStyle: { shadowBlur: 10, shadowColor: "rgba(0,0,0,0.3)" },
          label: { show: true, fontSize: 12, fontWeight: 600, color: palette.ink },
        },
      }],
    };
  }

  // ── Waterfall chart ─────────────────────────────────────────────────────
  // Rendered as a plain bar chart where each bar shows the raw contribution.
  // Positive values go above the x-axis (green), negative below (red).
  // ECharts handles negative bars natively — no stacking tricks needed.
  if (ctype === "waterfall_chart") {
    return {
      backgroundColor: "transparent",
      animationDuration: 600,
      tooltip: { ...tt, trigger: "axis", axisPointer: { type: "shadow" } },
      grid: compactGrid ?? { containLabel: true, left: "8%", right: "4%", top: 12, bottom: 8 },
      xAxis: {
        ...AXIS_BASE,
        type: "category",
        data: xData,
        axisLabel: { ...AXIS_BASE.axisLabel, rotate: xData.length > 6 ? 30 : 0 },
      },
      yAxis: { ...AXIS_BASE, type: "value" },  // waterfall: always start from 0
      series: [{
        name: kpi.name,
        type: "bar",
        barMaxWidth: 50,
        data: yData.map((v) => ({
          value: v,
          itemStyle: {
            color: v >= 0 ? palette.green : palette.red,
            borderRadius: v >= 0 ? [3, 3, 0, 0] : [0, 0, 3, 3],
          },
        })),
      }],
    };
  }

  // ── Scatter chart ────────────────────────────────────────────────────────
  if (ctype === "scatter_chart") {
    // Use x and y columns as the two axes for correlation
    const scatterData = chartRows.flatMap((row) => {
      const xv = parseNum(row[xCol!]);
      const yv = parseNum(row[yCol!]);
      return xv !== null && yv !== null ? [[xv, yv]] : [];
    });

    if (!scatterData.length) return null;

    return {
      backgroundColor: "transparent",
      animationDuration: 600,
      tooltip: {
        ...tt,
        trigger: "item",
        formatter: (p: { value: [number, number] }) =>
          `${xHint ?? xCol}: ${p.value[0].toLocaleString()}<br/>${yHint ?? yCol}: ${p.value[1].toLocaleString()}`,
      },
      grid: compactGrid ?? { containLabel: true, left: "8%", right: "4%", top: 12, bottom: 8 },
      xAxis: { ...AXIS_BASE, type: "value", name: xHint ?? xCol ?? "", nameLocation: "end", nameTextStyle: { color: palette.ink4, fontSize: 10 } },
      yAxis: { ...AXIS_BASE, type: "value", name: yHint ?? yCol ?? "", nameLocation: "end", nameTextStyle: { color: palette.ink4, fontSize: 10 } },
      series: [{
        name: kpi.name,
        type: "scatter",
        data: scatterData,
        symbolSize: 7,
        itemStyle: { color: palette.accent, opacity: 0.7 },
      }],
    };
  }

  // ── Donut chart ──────────────────────────────────────────────────────────
  if (ctype === "donut_chart") {
    const COLORS = [palette.accent, palette.green, palette.amber, palette.red, palette.ink2];
    const pieData = pairs.map((p, i) => ({
      name: p.x, value: p.y,
      itemStyle: { color: COLORS[i % COLORS.length] },
    }));
    return {
      backgroundColor: "transparent",
      tooltip: { ...tt, trigger: "item",
        formatter: (p: { name: string; value: number; percent: number }) =>
          `${p.name}: ${p.value.toLocaleString()} (${p.percent}%)` },
      legend: { bottom: 0, type: "scroll", textStyle: { color: palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: 10 }, icon: "circle", itemWidth: 8 },
      series: [{ name: kpi.name, type: "pie", radius: ["40%", "65%"], center: ["50%", "42%"],
        data: pieData, label: { show: false },
        emphasis: { itemStyle: { shadowBlur: 10, shadowColor: "rgba(0,0,0,0.3)" } } }],
    };
  }

  // ── Stacked area chart ───────────────────────────────────────────────────
  if (ctype === "stacked_area_chart") {
    return {
      backgroundColor: "transparent", animationDuration: compact ? 200 : 600,
      tooltip: { ...tt, trigger: "axis" },
      grid: compactGrid ?? { containLabel: true, left: "8%", right: "4%", top: hasCI ? 16 : 12, bottom: 8 },
      xAxis: { ...AXIS_BASE, type: "category", data: xData,
        axisLabel: compact ? COMPACT_AXIS_X.axisLabel : { ...AXIS_BASE.axisLabel, rotate: xData.length > 8 ? 30 : 0, hideOverlap: true } },
      // Line/area charts always auto-scale — compact and expanded should match.
      // scale:false (start-from-0) is only correct for bar charts.
      yAxis: { ...AXIS_BASE, type: "value", scale: true },
      series: [{ name: kpi.name, type: "line", data: yData, smooth: true, symbol: "none",
        lineStyle: { color: palette.accent, width: 2 },
        areaStyle: { color: translucent(palette.accent, 0.25) } }],
    };
  }

  // ── Funnel chart ─────────────────────────────────────────────────────────
  if (ctype === "funnel_chart") {
    const funnelData = pairs.map((p, i) => ({
      name: p.x, value: p.y,
      itemStyle: { color: `rgba(232,163,58,${1 - i * 0.15})` },
    }));
    return {
      backgroundColor: "transparent",
      tooltip: { ...tt, trigger: "item",
        formatter: (p: { name: string; value: number; percent: number }) =>
          `${p.name}: ${p.value.toLocaleString()} (${p.percent}%)` },
      series: [{ name: kpi.name, type: "funnel",
        left: "10%", width: "80%", top: 8, bottom: 32,
        sort: "descending", gap: 2,
        data: funnelData,
        label: { show: !compact, position: "inside", fontSize: 10, color: palette.bg },
        emphasis: { label: { fontSize: 12 } } }],
    };
  }

  // ── Heatmap chart ────────────────────────────────────────────────────────
  if (ctype === "heatmap_chart") {
    const cols = chartRows.length ? Object.keys(chartRows[0]) : [];

    // Severity mappings for categorical columns (HIGH→3, RED→3, etc.)
    const SEVERITY_MAP: Record<string, number> = {
      high: 3, critical: 3, red: 3, danger: 3, alert: 3,
      medium: 2, moderate: 2, amber: 2, warning: 2, caution: 2,
      low: 1, normal: 1, green: 1, safe: 1, ok: 1, good: 1,
    };
    const severityScore = (v: unknown): number | null => {
      const s = String(v ?? "").toLowerCase().trim();
      return SEVERITY_MAP[s] ?? null;
    };

    // breakdown_by sets the y-axis; fall back to yCol when breakdown_by not set
    const byCol  = kpi.chart?.breakdown_by
      ? findColumn(chartRows, kpi.chart.breakdown_by)
      : (yCol !== xCol ? yCol : null);

    // Find intensity: prefer numeric column, then severity-mapped categorical
    const intensityCol = cols.find(c =>
      c !== xCol && c !== byCol &&
      chartRows.slice(0, 5).some(r => parseNum(r[c]) !== null)
    );
    const severityCol = !intensityCol ? cols.find(c =>
      c !== xCol && c !== byCol &&
      chartRows.slice(0, 5).some(r => severityScore(r[c]) !== null)
    ) : null;

    const getVal = (row: Record<string, unknown>): number | null => {
      if (intensityCol) return parseNum(row[intensityCol]);
      if (severityCol)  return severityScore(row[severityCol]);
      return 1; // count-per-cell fallback
    };

    // Severity → RAG color for per-bar coloring
    const severityToColor = (score: number): string => {
      if (score >= 3) return palette.red;
      if (score >= 2) return palette.amber;
      return palette.green;
    };

    // Detect if an array of values is a pure RAG column (GREEN/AMBER/RED etc.)
    const isRagColumn = (vals: string[]) =>
      vals.length >= 1 && vals.length <= 4 &&
      vals.every(v => SEVERITY_MAP[v.toLowerCase().trim()] !== undefined);

    // ── Colour-coded risk bar chart ───────────────────────────────────────────
    // When the data has a categorical risk/status column (GREEN/AMBER/RED) but no
    // numeric intensity, render a horizontal bar coloured per RAG level instead of
    // a confusing count-heatmap.  The "worst" risk per label drives both bar length
    // and colour so the chart reads at a glance.
    const colorRiskBar = (overrideSevCol?: string): Record<string, unknown> | null => {
      // Use override (e.g. byCol when it IS the RAG column) or fall back to severityCol
      const effSevCol = overrideSevCol ?? severityCol;
      if (!effSevCol || !xCol) return null;
      const groups = new Map<string, { maxSev: number; label: string }>();
      for (const r of chartRows) {
        const k = String(r[xCol!] ?? "");
        if (!k) continue;
        const sev = severityScore(r[effSevCol]);
        if (sev === null) continue;
        const prev = groups.get(k);
        if (!prev || sev > prev.maxSev) {
          groups.set(k, { maxSev: sev, label: String(r[effSevCol] ?? "").toUpperCase() });
        }
      }
      const bars = [...groups.entries()]
        .map(([k, v]) => ({ name: k, value: v.maxSev, label: v.label }))
        .sort((a, b) => b.value - a.value);
      if (!bars.length) return null;
      return {
        backgroundColor: "transparent",
        animationDuration: compact ? 200 : 600,
        tooltip: compact ? { show: false } : {
          ...tt, trigger: "axis", axisPointer: { type: "shadow" },
          // Use actual label from data (e.g. "AMBER", "MEDIUM", "HIGH") — no hardcoded strings
          formatter: (p: { name: string; value: number }[]) => {
            const bar = bars.find(b => b.name === p[0].name);
            return `${p[0].name}: <b>${bar?.label ?? ""}</b>`;
          },
        },
        grid: { containLabel: true, left: "2%", right: "8%", top: 4, bottom: 4 },
        xAxis: { ...(compact ? COMPACT_AXIS_Y : AXIS_BASE), type: "value", max: 3, show: false },
        yAxis: {
          ...(compact ? COMPACT_AXIS_Y : AXIS_BASE), type: "category",
          data: bars.map(b => b.name), inverse: true,
          axisLabel: {
            color: compact ? palette.ink4 : palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: compact ? 8 : 11,
            formatter: (val: string) => labelDim(val),
          },
        },
        series: [{
          type: "bar",
          data: bars.map(b => ({
            value: b.value,
            itemStyle: { color: severityToColor(b.value), borderRadius: [0, 3, 3, 0] },
            label: compact ? undefined : {
              show: true, position: "right",
              color: palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: 10,
              formatter: () => bars.find(x => x.value === b.value)?.label ?? "",
            },
          })),
          barMaxWidth: 24,
        }],
      };
    };

    // ── Fallback: when a valid 2-D grid can't form, render a horizontal bar
    // of xCol vs mean intensity — so the tile ALWAYS shows something.
    const barFallback = () => {
      // If byCol is a RAG column, use colour-coded risk bar with it as severity source
      const ragOverride = byCol && isRagColumn([...new Set(chartRows.map(r => String(r[byCol!] ?? "")))].filter(Boolean))
        ? byCol : undefined;
      const rcb = colorRiskBar(ragOverride);
      if (rcb) return rcb;

      if (!xCol) return null;
      const groups = new Map<string, number[]>();
      for (const r of chartRows) {
        const k = String(r[xCol!] ?? "");
        if (!k) continue;
        const v = getVal(r);
        if (v === null) continue;
        if (!groups.has(k)) groups.set(k, []);
        groups.get(k)!.push(v);
      }
      const bars = [...groups.entries()]
        .map(([k, vs]) => ({ x: k, y: vs.reduce((a, b) => a + b, 0) / vs.length }))
        .sort((a, b) => b.y - a.y);
      if (!bars.length) return null;
      return {
        backgroundColor: "transparent",
        animationDuration: compact ? 200 : 600,
        tooltip: compact ? { show: false } : { ...tt, trigger: "axis", axisPointer: { type: "shadow" } },
        grid: { containLabel: true, left: "2%", right: "4%", top: 8, bottom: 8 },
        xAxis: { ...(compact ? COMPACT_AXIS_Y : AXIS_BASE), type: "value" },
        yAxis: {
          ...(compact ? COMPACT_AXIS_Y : AXIS_BASE), type: "category",
          data: bars.map(b => b.x), inverse: true,
          axisLabel: {
            color: compact ? palette.ink4 : palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: compact ? 8 : 10,
            formatter: (val: string) => labelDim(val),
          },
        },
        series: [{
          type: "bar", data: bars.map(b => b.y), barMaxWidth: 22,
          itemStyle: { color: palette.accent, borderRadius: [0, 3, 3, 0] },
        }],
      };
    };

    if (!byCol || !xCol) return compact ? barFallback() : null;
    let xVals  = [...new Set(chartRows.map(r => String(r[xCol!] ?? "")))].filter(Boolean);
    let yVals  = [...new Set(chartRows.map(r => String(r[byCol!] ?? "")))].filter(Boolean);

    // If x has only 1 distinct value but y has multiple, swap them so we get
    // a real bar/heatmap (e.g. Risk Matrix with 1 dept but 5 facilities → use facilities on x)
    if (xVals.length < 2 && yVals.length >= 2) {
      [xCol, yCol] = [byCol, xCol!];
      xVals = yVals;
      yVals = [...new Set(chartRows.map(r => String(r[yCol!] ?? "")))].filter(Boolean);
    }

    // If the 2nd dimension is still degenerate, fall back to a bar of the primary dimension.
    if (yVals.length < 2 || xVals.length < 2) return compact ? barFallback() : null;

    // If one of the two dimensions IS the risk-color column itself (values = GREEN/AMBER/RED),
    // don't render a confusing "color vs department" grid — use colorRiskBar with
    // the non-color dimension as the label axis so each bar is clearly coloured.
    if (isRagColumn(xVals) || isRagColumn(yVals)) {
      // yVals is the RAG column (byCol) — xCol is the label dimension. Pass byCol
      // as the explicit severity column since severityCol was calculated excluding byCol.
      if (isRagColumn(yVals)) {
        const rcb = colorRiskBar(byCol ?? undefined);
        if (rcb) return rcb;
      }
      // xVals is the RAG column — swap so xCol becomes the label dimension
      if (isRagColumn(xVals)) {
        [xCol, yCol] = [byCol!, xCol!];
        const rcb = colorRiskBar(yCol ?? undefined);
        if (rcb) return rcb;
      }
    }

    // Build cell map
    const cellMap = new Map<string, number[]>();
    for (const row of chartRows) {
      const xi = xVals.indexOf(String(row[xCol!] ?? ""));
      const yi = yVals.indexOf(String(row[byCol!] ?? ""));
      if (xi < 0 || yi < 0) continue;
      const v = getVal(row);
      if (v !== null) {
        const key = `${xi},${yi}`;
        if (!cellMap.has(key)) cellMap.set(key, []);
        cellMap.get(key)!.push(v);
      }
    }
    const rawHeatData = [...cellMap.entries()].map(([key, vals]) => {
      const [xi, yi] = key.split(",").map(Number);
      return [xi, yi, vals.reduce((a, b) => a + b, 0) / vals.length];
    });
    if (!rawHeatData.length) return compact ? barFallback() : null;

    // Smart axis assignment: put the SHORTER dimension on x-axis so cells are wider.
    // If facilities (xVals) outnumber departments (yVals), swap them.
    const swapAxes = xVals.length > yVals.length;
    const hmXVals  = swapAxes ? yVals : xVals;
    const hmYVals  = swapAxes ? xVals : yVals;
    const heatData = rawHeatData.map(([xi, yi, v]) =>
      swapAxes ? [yi, xi, v] : [xi, yi, v]
    );

    const hmXLabel = {
      color:       compact ? palette.ink4 : palette.ink3,
      fontFamily:  CHART_NUM_FONT,
      fontSize:    compact ? 8 : 9,
      rotate:      hmXVals.length > 5 ? 35 : 0,
      hideOverlap: true,
      formatter:   (val: string) => labelDim(val),
    };
    // containLabel:true handles the space — no fixed width needed, no truncation
    const hmYLabel = {
      color:      compact ? palette.ink4 : palette.ink3,
      fontFamily: CHART_NUM_FONT,
      fontSize:   compact ? 8 : 9,
      formatter:  (val: string) => labelDim(val),
    };

    return {
      backgroundColor: "transparent",
      animationDuration: compact ? 200 : 600,
      tooltip: compact ? { show: false } : { ...tt, formatter: (p: { value: [number, number, number] }) =>
        `${labelDim(hmXVals[p.value[0]])} / ${labelDim(hmYVals[p.value[1]])}: ${p.value[2].toLocaleString()}` },
      // containLabel ensures y-axis labels never clip; works for both compact and full
      grid: { containLabel: true, left: "2%", right: "2%", top: compact ? 4 : 8, bottom: compact ? 4 : 8 },
      xAxis: {
        type: "category", data: hmXVals,
        axisLabel: hmXLabel,
        axisTick: { show: false },
        axisLine: { lineStyle: { color: palette.line2 } },
      },
      yAxis: {
        type: "category", data: hmYVals,
        axisLabel: hmYLabel,
        axisTick: { show: false },
        axisLine: { lineStyle: { color: palette.line2 } },
      },
      visualMap: (() => {
        const vals = heatData.map(d => d[2]);
        const minV = Math.min(...vals);
        const maxV = Math.max(...vals);
        // RAG for ALL heatmaps — direction depends on data:
        //   severity-mapped (1/2/3 from categorical risk): green=1 → red=3
        //   all-negative numeric: green=least-bad → red=worst (most negative)
        //   positive numeric: green=lowest → amber=middle → red=highest
        //   (relative within dataset — shows which cells stand out)
        // Single-color only if there's truly no variation (all same value).
        const hasVariation = maxV > minV;
        const isSeverityMapped = severityCol !== null;
        const allNeg = vals.every(v => v <= 0);
        // For all-negative: reverse the scale so most-negative = red (worst)
        const colors: string[] = (isSeverityMapped || !allNeg)
          ? [palette.green, palette.amber, palette.red]   // low=green, high=red
          : [palette.red,   palette.amber, palette.green]; // most-negative=red, least=green
        return {
          show: false,
          min: minV,
          max: maxV,
          inRange: hasVariation
            ? { color: colors }
            : { color: [translucent(palette.accent, 0.4), palette.accent] },
        };
      })(),
      series: [{ type: "heatmap", data: heatData, emphasis: { itemStyle: { shadowBlur: 10 } } }],
    };
  }

  // ── Treemap chart ────────────────────────────────────────────────────────
  if (ctype === "treemap_chart") {
    const COLORS = [palette.accent, palette.green, palette.amber, palette.red, palette.ink2];
    return {
      backgroundColor: "transparent",
      tooltip: { ...tt, formatter: (p: { name: string; value: number }) =>
        `${p.name}: ${p.value.toLocaleString()}` },
      series: [{ type: "treemap", width: "100%", height: "100%",
        roam: false, nodeClick: false,
        data: pairs.map((p, i) => ({ name: p.x, value: p.y, itemStyle: { color: COLORS[i % COLORS.length], gapWidth: 2 } })),
        label: { show: true, fontSize: 10, color: "#fff", formatter: "{b}" },
        breadcrumb: { show: false } }],
    };
  }

  // ── Radar chart ──────────────────────────────────────────────────────────
  if (ctype === "radar_chart") {
    const maxVal = Math.max(...pairs.map(p => p.y)) * 1.2;
    return {
      backgroundColor: "transparent",
      tooltip: tt,
      radar: {
        indicator: pairs.map(p => ({ name: p.x, max: maxVal })),
        axisName: { color: palette.ink3, fontSize: 9 },
        axisLine: { lineStyle: { color: palette.line } },
        splitLine: { lineStyle: { color: palette.line } },
        splitArea: { areaStyle: { color: ["transparent"] } },
      },
      series: [{ type: "radar",
        data: [{ name: kpi.name, value: pairs.map(p => p.y),
          areaStyle: { color: translucent(palette.accent, 0.2) },
          lineStyle: { color: palette.accent, width: 2 },
          itemStyle: { color: palette.accent } }] }],
    };
  }

  // ── Bubble chart ─────────────────────────────────────────────────────────
  if (ctype === "bubble_chart") {
    // Uses x_axis, y_axis, and a third dimension for bubble size
    const scatterData = chartRows.flatMap((row) => {
      const xv = parseNum(row[xCol!]);
      const yv = parseNum(row[yCol!]);
      return xv !== null && yv !== null ? [[xv, yv, Math.abs(yv) / 10]] : [];
    });
    if (!scatterData.length) return null;
    return {
      backgroundColor: "transparent", animationDuration: compact ? 200 : 600,
      tooltip: { ...tt, trigger: "item",
        formatter: (p: { value: [number, number, number] }) =>
          `${xHint ?? xCol}: ${p.value[0].toLocaleString()}<br/>${yHint ?? yCol}: ${p.value[1].toLocaleString()}` },
      grid: compactGrid ?? { containLabel: true, left: "8%", right: "4%", top: 12, bottom: 8 },
      xAxis: { ...AXIS_BASE, type: "value", name: xHint ?? xCol ?? "", nameLocation: "end", nameTextStyle: { color: palette.ink4, fontSize: 10 } },
      yAxis: { ...AXIS_BASE, type: "value", scale: true, name: yHint ?? yCol ?? "", nameLocation: "end", nameTextStyle: { color: palette.ink4, fontSize: 10 } },
      series: [{ type: "scatter", data: scatterData, symbolSize: (d: number[]) => Math.sqrt(d[2]) * 5 + 8,
        itemStyle: { color: translucent(palette.accent, 0.7), borderColor: palette.accent, borderWidth: 1 } }],
    };
  }

  return null;
}

// ── Chart range (recent window vs full history) ───────────────────────────────

export type ChartRange = "recent" | "all";

const RECENT_MAX_TEMPORAL    = 24;
const RECENT_MAX_CATEGORICAL = 12;

function recentMaxForKpi(kpi: NavigatorKPI): number {
  return (kpi.chart?.x_axis_type ?? "categorical") === "temporal"
    ? RECENT_MAX_TEMPORAL
    : RECENT_MAX_CATEGORICAL;
}

function extractCategoryCount(option: object): number {
  const o = option as {
    xAxis?: { type?: string; data?: unknown[] } | { type?: string; data?: unknown[] }[];
    yAxis?: { type?: string; data?: unknown[] } | { type?: string; data?: unknown[] }[];
  };
  const xa = Array.isArray(o.xAxis) ? o.xAxis[0] : o.xAxis;
  if (xa?.type === "category" && xa.data?.length) return xa.data.length;
  const ya = Array.isArray(o.yAxis) ? o.yAxis[0] : o.yAxis;
  if (ya?.type === "category" && ya.data?.length) return ya.data.length;
  return 0;
}

function withDataZoom(option: object, xLen: number, palette: Palette): object {
  const windowPct = Math.min(100, Math.max(15, Math.round((RECENT_MAX_TEMPORAL / xLen) * 100)));
  const start = 100 - windowPct;
  const grid = (option as { grid?: Record<string, unknown> }).grid ?? {};
  const bottom = typeof grid.bottom === "number" ? grid.bottom : 8;
  const isHorizontal = (() => {
    const o = option as { yAxis?: { type?: string } | { type?: string }[] };
    const ya = Array.isArray(o.yAxis) ? o.yAxis[0] : o.yAxis;
    return ya?.type === "category";
  })();

  const slider = {
    type:        "slider" as const,
    start,
    end:         100,
    height:      14,
    width:       isHorizontal ? 14 : undefined,
    orient:      isHorizontal ? ("vertical" as const) : ("horizontal" as const),
    bottom:      isHorizontal ? undefined : 4,
    right:       isHorizontal ? 4 : undefined,
    top:         isHorizontal ? 12 : undefined,
    yAxisIndex:  isHorizontal ? 0 : undefined,
    borderColor: palette.line2,
    fillerColor: translucent(palette.accent, 0.15),
    handleStyle: { color: palette.accent },
    textStyle:   { color: palette.ink4, fontFamily: CHART_NUM_FONT, fontSize: 9 },
  };

  return {
    ...option,
    grid: {
      ...grid,
      bottom: isHorizontal ? bottom : bottom + 30,
      right:  isHorizontal ? 28 : grid.right,
    },
    dataZoom: [
      { type: "inside", start, end: 100, yAxisIndex: isHorizontal ? 0 : undefined },
      slider,
    ],
  };
}

function ChartRangeControl({
  range,
  onChange,
  palette,
  recentMax,
  total,
}: {
  range:     ChartRange;
  onChange:  (r: ChartRange) => void;
  palette:   Palette;
  recentMax: number;
  total:     number;
}) {
  const opts: { key: ChartRange; label: string }[] = [
    { key: "recent", label: `Recent ${recentMax}` },
    { key: "all",    label: `All ${total}` },
  ];
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 8 }}>
      <span style={{
        fontFamily:    CHART_NUM_FONT,
        fontSize:      10,
        color:         palette.ink4,
        letterSpacing: "0.04em",
        textTransform: "uppercase",
      }}>
        Range
      </span>
      <div style={{
        display:      "inline-flex",
        background:   palette.bg2,
        border:       `1px solid ${palette.line}`,
        borderRadius: 6,
        padding:      2,
        gap:          2,
      }}>
        {opts.map(({ key, label }) => {
          const active = range === key;
          return (
            <button
              key={key}
              type="button"
              onClick={() => onChange(key)}
              style={{
                fontFamily:     CHART_NUM_FONT,
                fontSize:       11,
                fontWeight:     active ? 700 : 500,
                padding:        "3px 10px",
                borderRadius:   4,
                border:         "none",
                cursor:         "pointer",
                background:     active ? palette.bg1 : "transparent",
                color:          active ? palette.ink : palette.ink4,
                boxShadow:      active ? "0 1px 3px rgba(0,0,0,0.08)" : "none",
                letterSpacing:  "0.02em",
                whiteSpace:     "nowrap",
              }}
            >
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** True when buildOption would produce a renderable chart (avoids empty reserved space). */
export function canBuildKpiChart(
  kpi: NavigatorKPI,
  rows: Record<string, unknown>[],
  period: Period = "now",
  compact = false,
  overrideValue?: number | null,
): boolean {
  if (!rows.length) return false;
  const palette = getChartPalette();
  return buildOption(kpi, rows, palette, period, compact, undefined, overrideValue) !== null;
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  kpi:            NavigatorKPI;
  rows:           Record<string, unknown>[];
  loading:        boolean;
  period?:        Period;
  height?:        number;
  maxPoints?:     number;              // sample rows for compact view (avoids noisy charts)
  compact?:       boolean;             // hide all axes/labels — shape only (for tile view)
  overrideValue?: number | null;       // period-resolved value (L1/L2/L3) for gauge needle
  hideIfEmpty?: boolean;               // when true, render nothing instead of "No chart" placeholder
  showRangeControl?: boolean;          // modal: Recent vs All toggle when data is dense
  dimensionLabelMaps?: Record<string, Record<string, string>>;
}

export function NavigatorKpiChart({
  kpi,
  rows,
  loading,
  period = "now",
  height = 240,
  maxPoints: maxPointsProp,
  compact = false,
  overrideValue,
  hideIfEmpty = false,
  showRangeControl = false,
  dimensionLabelMaps,
}: Props) {
  const displayRows = rows;
  const { palette } = useChartTheme();
  const [range, setRange] = useState<ChartRange>("recent");
  const recentMax = recentMaxForKpi(kpi);

  useEffect(() => { setRange("recent"); }, [kpi.id, kpi.name]);

  const effectiveMaxPoints = maxPointsProp !== undefined
    ? maxPointsProp
    : showRangeControl && range === "recent"
      ? recentMax
      : undefined;

  const fullPointCount = useMemo(() => {
    if (!showRangeControl || !displayRows.length) return 0;
    const full = buildOption(kpi, displayRows, palette, period, compact, undefined, overrideValue, dimensionLabelMaps);
    return full ? extractCategoryCount(full) : 0;
  }, [showRangeControl, displayRows, kpi, palette, period, compact, overrideValue, dimensionLabelMaps]);

  const baseOption = useMemo(
    () => buildOption(kpi, displayRows, palette, period, compact, effectiveMaxPoints, overrideValue, dimensionLabelMaps),
    [kpi, displayRows, palette, period, compact, effectiveMaxPoints, overrideValue, dimensionLabelMaps],
  );

  const option = useMemo(() => {
    if (!baseOption) return null;
    if (showRangeControl && range === "all" && !compact && fullPointCount > recentMax) {
      return withDataZoom(baseOption, fullPointCount, palette);
    }
    return baseOption;
  }, [baseOption, showRangeControl, range, compact, fullPointCount, recentMax, palette]);

  const needsRangeControl = showRangeControl && !compact && fullPointCount > recentMax;

  const chartBody = (() => {
    if (loading) {
      return (
        <div style={{
          height,
          background: palette.bg2,
          borderRadius: 4,
          position: "relative",
          overflow: "hidden",
        }}>
          <div style={{
            position: "absolute", inset: 0,
            background: `linear-gradient(90deg, transparent 0%, ${palette.bg3} 50%, transparent 100%)`,
            animation: "shimmer 0.9s infinite",
          }} />
          <style>{`
            @keyframes shimmer {
              0%   { transform: translateX(-100%); }
              100% { transform: translateX(100%); }
            }
          `}</style>
        </div>
      );
    }

    if (!rows.length || !option) {
      if (hideIfEmpty) return null;
      return (
        <div style={{
          height,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          border: `1px dashed ${palette.line2}`,
          borderRadius: 4,
          color: palette.ink4,
          fontFamily: CHART_FONT,
          fontSize: 12,
        }}>
          {!rows.length ? "No data for this period" : "No chart"}
        </div>
      );
    }

    return (
      <ReactECharts
        option={option}
        style={{ height, width: "100%" }}
        notMerge
        opts={{ renderer: "canvas" }}
      />
    );
  })();

  if (chartBody === null) return null;

  if (!needsRangeControl) return chartBody;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, width: "100%" }}>
      <ChartRangeControl
        range={range}
        onChange={setRange}
        palette={palette}
        recentMax={recentMax}
        total={fullPointCount}
      />
      {chartBody}
    </div>
  );
}
