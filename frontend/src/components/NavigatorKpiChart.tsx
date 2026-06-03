// ─── NavigatorKpiChart ────────────────────────────────────────────────────────
// Pure chart renderer — receives pre-fetched, period-filtered rows from
// NavigatorKpiCard and turns them into an ECharts option.
//
// No data fetching here. All fetch / filter / L2 computation lives in
// NavigatorKpiCard so both the headline number and the chart always use
// the same filtered dataset.

import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import type { NavigatorKPI, L2Projection } from "../types/navigator";
import { useChartTheme } from "../context/ChartThemeContext";
import { CHART_FONT, CHART_NUM_FONT, chartTooltip, translucent } from "./charts/chartTheme";
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

function monthYearToNum(s: string): number {
  const months: Record<string, number> = {
    jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6,
    jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12,
  };
  const m = s.match(/(\d{4})[/-](\d{1,2})/);
  if (m) return parseInt(m[1]) * 100 + parseInt(m[2]);
  const m2 = s.match(/([A-Za-z]{3})[a-z]*\s*(\d{4})/);
  if (m2) return parseInt(m2[2]) * 100 + (months[m2[1].toLowerCase()] ?? 0);
  const m3 = s.match(/Q(\d)\s*(\d{4})/i);
  if (m3) return parseInt(m3[2]) * 100 + parseInt(m3[1]) * 3;
  const m4 = s.match(/^(\d{4})$/);
  if (m4) return parseInt(m4[1]) * 100;
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
): XYPair[] {
  const groups: Record<string, number[]> = {};
  for (const row of rows) {
    const key = String(row[xCol] ?? "(null)");
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
      case "ratio":
      case "stable": {
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
): object | null {
  // Compact axis base — no labels, no ticks, no gridlines (for tile view)
  const COMPACT_AXIS = {
    axisLabel: { show: false },
    axisTick:  { show: false },
    axisLine:  { show: false },
    splitLine: { show: false },
  };
  const ctype = (kpi.chart?.type ?? "kpi_card").toLowerCase();
  if (ctype === "kpi_card" || ctype === "scorecard") return null;
  if (!rows.length) return null;

  const xHint = kpi.chart?.x_axis;
  const yHint = kpi.chart?.y_axis ?? kpi.l1?.field_name;
  const agg   = kpi.chart?.aggregation || "sum";

  // ── Gauge chart ──────────────────────────────────────────────────────────
  if (ctype === "gauge_chart") {
    const l1Val = parseNum(kpi.l1?.value);
    const unit  = (kpi.l1?.unit ?? "").trim();
    const isPercent = unit === "%" || (l1Val !== null && l1Val > 0 && l1Val <= 100 && unit === "");
    const val   = l1Val ?? 0;
    const maxVal = isPercent ? 100 : Math.max(val * 1.5, 100);
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

  let xCol = findColumn(rows, xHint ?? "") ?? autoXCol(rows);
  let yCol = findColumn(rows, yHint ?? "") ?? autoYCol(rows, xCol);

  if (xCol && xCol === yCol) xCol = autoXCol(rows, yCol);
  if (!xCol || !yCol || xCol === yCol) return null;

  // Auto-correct swapped x/y: if x is numeric and y is categorical the agent
  // put value and label on the wrong axes — swap so the chart renders correctly.
  // Skip for count aggregation — count doesn't require a numeric y column.
  if (agg !== "count") {
    const sample5 = rows.slice(0, 5);
    const xIsNumeric = sample5.some((r) => parseNum(r[xCol!]) !== null);
    const yIsNumeric = sample5.some((r) => parseNum(r[yCol!]) !== null);
    if (xIsNumeric && !yIsNumeric) {
      [xCol, yCol] = [yCol, xCol];
    }
  }

  let pairs = groupBy(rows, xCol, yCol, agg, xAxisType, sortOrder);
  if (!pairs.length) return null;

  // Apply maxPoints AFTER aggregation so counts/sums are correct
  // For temporal: keep last N (most recent); for categorical: keep as-is (already sorted)
  if (maxPoints && pairs.length > maxPoints) {
    pairs = xAxisType === "temporal"
      ? pairs.slice(-maxPoints)           // last N periods
      : pairs.slice(0, maxPoints);        // top N categories
  }

  // Build projected future points when a period is selected and projection defined
  const projPoints: ProjectedPoint[] = (period !== "now" && kpi.l2_projection)
    ? buildProjectedPoints(
        pairs,
        kpi.l2_projection,
        rows,
        xAxisType,
        period === "7d" ? 7 : 30,
      )
    : [];

  const allX = [...pairs.map((p) => p.x), ...projPoints.map((p) => p.x)];
  const xData = pairs.map((p) => p.x);
  const yData = pairs.map((p) => p.y);
  const tt    = chartTooltip(palette);

  // Compact mode: mini axes with fewer labels — resembles original but scaled down
  // No width/overflow here — applied per-axis where needed to avoid x-axis truncation
  const AXIS_BASE = compact ? {
    axisLine:  { lineStyle: { color: palette.line2 } },
    axisTick:  { show: false },
    axisLabel: {
      color:       palette.ink4,
      fontFamily:  CHART_NUM_FONT,
      fontSize:    9,
      interval:    "auto" as const,
      hideOverlap: true,
    },
    splitLine: { lineStyle: { color: palette.line, type: "dashed" as const, opacity: 0.5 } },
  } : {
    axisLine:  { lineStyle: { color: palette.line2 } },
    axisTick:  { show: false },
    axisLabel: { color: palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: 10 },
    splitLine: { lineStyle: { color: palette.line, type: "dashed" as const } },
  };
  // Compact grids — enough room so labels don't clip
  const compactGrid = compact
    ? { left: 52, right: 14, top: 8, bottom: 38 }  // bottom=38 for rotated x labels
    : null;
  // Horizontal bar: left must fit category name labels (up to ~85px for long names)
  const compactHBarGrid = compact
    ? { left: 90, right: 16, top: 6, bottom: 6 }
    : null;

  // ── Detect confidence interval columns ──────────────────────────────────
  // Automatically finds upper/lower prediction interval columns in the rows.
  // Works for any Tableau view that exports confidence bounds.
  const { lower: lowerCol, upper: upperCol } = findConfidenceCols(rows, yCol!);
  const hasCI = !!(lowerCol && upperCol);

  // Build grouped confidence data aligned to xData
  let lowerData: (number | null)[] = [];
  let upperData: (number | null)[] = [];
  if (hasCI) {
    const lowerMap = new Map<string, number[]>();
    const upperMap = new Map<string, number[]>();
    for (const row of rows) {
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

  // ── Line / Area ──────────────────────────────────────────────────────────
  if (ctype === "line_chart" || ctype === "area_chart") {
    const hasProj = projPoints.length > 0;
    const projYData = hasProj
      ? [...new Array(xData.length - 1).fill(null), yData[yData.length - 1], ...projPoints.map((p) => p.y)]
      : [];

    return {
      backgroundColor: "transparent",
      animationDuration: compact ? 200 : 600,
      tooltip: { ...tt, trigger: "axis" },
      grid: compactGrid ?? { left: 46, right: 16, top: hasCI ? 16 : 12, bottom: 36 },
      xAxis: {
        ...AXIS_BASE,
        type: "category",
        data: hasProj ? allX : xData,
        axisLabel: {
          ...AXIS_BASE.axisLabel,
          rotate: allX.length > 8 ? 30 : 0,
          interval: allX.length > 16 ? Math.floor(allX.length / 8) : 0,
        },
      },
      yAxis: { ...AXIS_BASE, type: "value" },
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
        // Projection dashed series
        ...(hasProj ? [{
          name: "Projected",
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
      grid: compactGrid ?? { left: 46, right: 16, top: 12, bottom: 36 },
      xAxis: {
        ...AXIS_BASE,
        type: "category",
        data: hasProj ? allX : xData,
        axisLabel: {
          ...AXIS_BASE.axisLabel,
          rotate:     allX.length > 8 ? 35 : 0,
          interval:   0,
          // align right so rotated labels anchor at tick bottom, not clip on left
          align:      allX.length > 8 ? "right" : "center",
        },
      },
      yAxis: { ...AXIS_BASE, type: "value" },
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
          name: "Projected",
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
      grid: compactHBarGrid ?? { left: 110, right: 16, top: 8, bottom: 8 },
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
        },
      },
      series: [{
        name: kpi.name,
        type: "bar",
        data: hPairs.map((p) => p.y),
        barMaxWidth: 20,
        itemStyle: {
          color: {
            type: "linear", x: 0, y: 0, x2: 1, y2: 0,
            colorStops: [
              { offset: 0, color: translucent(palette.accent, 0.7) },
              { offset: 1, color: palette.accent },
            ],
          },
          borderRadius: [0, 3, 3, 0],
        },
      }],
    };
  }

  // ── Pie / Donut ──────────────────────────────────────────────────────────
  if (ctype === "pie_chart" || ctype === "donut_chart") {
    // If xCol produces too many slices (e.g. Order_Date with 100+ dates),
    // try swapping to yCol if it has fewer distinct values (e.g. Ship_Status).
    // Pie charts should have ≤ 15 slices to be readable.
    const xDistinct = new Set(rows.map((r) => String(r[xCol!] ?? ""))).size;
    if (xDistinct > 15 && yCol) {
      const yDistinct = new Set(rows.map((r) => String(r[yCol!] ?? ""))).size;
      if (yDistinct < xDistinct && yDistinct <= 15) {
        [xCol, yCol] = [yCol, xCol];
      }
    }
    // Re-compute pairs with the (possibly swapped) columns
    const piePairs = groupBy(rows, xCol!, yCol!, agg, xAxisType, sortOrder);
    if (!piePairs.length) return null;

    const COLORS = [palette.accent, palette.green, palette.amber, palette.red, palette.ink2];
    const pieData = piePairs.map((p, i) => ({
      name: p.x,
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
      grid: { left: 56, right: 16, top: 12, bottom: 36 },
      xAxis: {
        ...AXIS_BASE,
        type: "category",
        data: xData,
        axisLabel: { ...AXIS_BASE.axisLabel, rotate: xData.length > 6 ? 30 : 0 },
      },
      yAxis: { ...AXIS_BASE, type: "value" },
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
    const scatterData = rows.flatMap((row) => {
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
      grid: { left: 56, right: 16, top: 12, bottom: 36 },
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

  return null;
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  kpi:        NavigatorKPI;
  rows:       Record<string, unknown>[];
  loading:    boolean;
  period?:    Period;
  height?:    number;
  maxPoints?: number;   // sample rows for compact view (avoids noisy charts)
  compact?:   boolean;  // hide all axes/labels — shape only (for tile view)
}

export function NavigatorKpiChart({ kpi, rows, loading, period = "now", height = 240, maxPoints, compact = false }: Props) {
  // Use ALL rows for groupBy aggregation — sampling happens on aggregated pairs
  // inside buildOption to preserve correct counts (not individual row values)
  const displayRows = rows;
  const { palette } = useChartTheme();

  const option = useMemo(
    () => buildOption(kpi, displayRows, palette, period, compact, maxPoints),
    [kpi, displayRows, palette, period, compact, maxPoints],
  );

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
}
