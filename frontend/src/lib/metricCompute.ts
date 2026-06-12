// ─── Metric contract (frontend) ───────────────────────────────────────────────
// Mirrors pipeline/metric_contract.py — single aggregation rules for L1/L2/chart.

import type { NavigatorKPI } from "../types/navigator";

export type MetricKind = "snapshot" | "rate" | "accumulator";

const DATE_KWS = ["date", "time", "timestamp", "day", "hour", "month", "created", "period"];

function norm(s: string): string {
  return String(s).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

export function findColumn(rows: Record<string, unknown>[], hint: string | null | undefined): string | null {
  if (!rows.length || !hint) return null;
  const cols = Object.keys(rows[0]);
  const h = norm(hint);
  const exact = cols.find((c) => norm(c) === h);
  if (exact) return exact;
  const sub = cols.find((c) => norm(c).includes(h) || h.includes(norm(c)));
  return sub ?? null;
}

export function findDateColumn(
  rows: Record<string, unknown>[],
  ...hints: (string | null | undefined)[]
): string | null {
  if (!rows.length) return null;
  for (const hint of hints) {
    if (hint && hint !== "null" && hint !== "None") {
      const col = findColumn(rows, hint);
      if (col) return col;
    }
  }
  const cols = Object.keys(rows[0]);
  return cols.find((c) => DATE_KWS.some((kw) => c.toLowerCase().includes(kw))) ?? null;
}

function parseNum(v: unknown): number | null {
  if (v === null || v === undefined) return null;
  if (typeof v === "number") return v;
  let s = String(v).trim().replace(/,/g, "");
  if (!s) return null;
  if (s.startsWith("(") && s.endsWith(")")) {
    const n = parseFloat(s.slice(1, -1));
    return isNaN(n) ? null : -n;
  }
  if (s.endsWith("%")) s = s.slice(0, -1).trim();
  const n = parseFloat(s);
  return isNaN(n) ? null : n;
}

function computeConversionRate(rows: Record<string, unknown>[]): number | null {
  const conv = findColumn(rows, "converted_count") ?? findColumn(rows, "converted");
  const total = findColumn(rows, "referral_count") ?? findColumn(rows, "referrals");
  if (!conv || !total) return null;
  let c = 0, t = 0;
  for (const r of rows) {
    c += parseNum(r[conv]) ?? 0;
    t += parseNum(r[total]) ?? 0;
  }
  return t > 0 ? (100 * c) / t : null;
}

function computeIsolationUtilization(rows: Record<string, unknown>[]): number | null {
  const iso = findColumn(rows, "isolation_beds_used");
  const staffed = findColumn(rows, "staffed_beds");
  if (!iso || !staffed) return null;
  const ratios: number[] = [];
  for (const r of rows) {
    const i = parseNum(r[iso]);
    const s = parseNum(r[staffed]);
    if (i !== null && s && s > 0) ratios.push((100 * i) / s);
  }
  return ratios.length ? ratios.reduce((a, b) => a + b, 0) / ratios.length : null;
}

function bucketKey(datetimeVal: unknown, hourly: boolean): string {
  const s = String(datetimeVal);
  if (hourly && s.length > 10 && ["T", " ", "t"].includes(s[10])) return s.slice(0, 13);
  return s.slice(0, 10);
}

export function isHourlyDates(rows: Record<string, unknown>[], dateCol: string): boolean {
  const sample = rows.find((r) => r[dateCol])?.[dateCol];
  const s = String(sample ?? "");
  return s.length > 10 && ["T", " ", "t"].includes(s[10]);
}

function isCumulativeSeries(vals: number[]): boolean {
  if (vals.length < 3) return false;
  let increases = 0;
  for (let i = 0; i < vals.length - 1; i++) {
    if (vals[i] <= vals[i + 1]) increases++;
  }
  return increases >= (vals.length - 1) * 0.85;
}

function incrementsFromCumulative(series: { x: string; y: number }[]): { x: string; y: number }[] {
  if (series.length < 2) return series;
  return series.map((p, i) => ({
    x: p.x,
    y: i === 0 ? p.y : p.y - series[i - 1].y,
  }));
}

function aggregateValues(vals: number[], agg: string): number {
  if (!vals.length) return 0;
  switch (agg.toLowerCase()) {
    case "avg":   return vals.reduce((a, b) => a + b, 0) / vals.length;
    case "count": return vals.length;
    case "max":   return Math.max(...vals);
    case "min":   return Math.min(...vals);
    default:      return vals.reduce((a, b) => a + b, 0);
  }
}

export function bucketSeries(
  rows: Record<string, unknown>[],
  valueCol: string,
  dateCol: string,
  perBucketAgg: string,
  hourly?: boolean,
): { x: string; y: number }[] {
  const useHourly = hourly ?? isHourlyDates(rows, dateCol);
  const buckets: Record<string, number[]> = {};
  for (const row of rows) {
    const dv = row[dateCol];
    if (dv == null) continue;
    const bk = bucketKey(dv, useHourly);
    if (!buckets[bk]) buckets[bk] = [];
    if (perBucketAgg === "count") {
      buckets[bk].push(1);
    } else {
      const v = parseNum(row[valueCol]);
      if (v !== null) buckets[bk].push(v);
    }
  }
  return Object.keys(buckets)
    .sort()
    .map((k) => ({
      x: k,
      y: Math.round(aggregateValues(buckets[k], perBucketAgg) * 1000) / 1000,
    }));
}

export function metricKind(method: string | null | undefined): MetricKind {
  if (method === "ratio") return "rate";
  if (method === "daily_rate" || method === "growth_rate") return "accumulator";
  return "accumulator";
}

const TEMPORAL_CHART_TYPES = new Set([
  "line_chart", "area_chart", "stacked_area_chart", "stacked_bar_chart",
]);

function isTemporalKpi(kpi: NavigatorKPI): boolean {
  const ctype = (kpi.chart?.type ?? "").toLowerCase();
  return kpi.chart?.x_axis_type === "temporal" || TEMPORAL_CHART_TYPES.has(ctype);
}

const SNAPSHOT_KWS = ["current", "snapshot", "on hand", "beds available", "queue depth", "backlog", "headcount", "census", "capacity"];
const RATE_KWS = ["rate", "ratio", "percent", "pct", "utilization", "occupancy", "margin", "productivity"];
const FLOW_KWS = ["volume", "referral", "admission", "discharge", "throughput", "hours", "wait time"];

function nameHasAny(nameN: string, kws: string[]): boolean {
  return kws.some((kw) => nameN.includes(kw));
}

/** Classify KPI metric kind (mirrors pipeline/metric_classifier.classify_metric_kind). */
export function resolveMetricKind(kpi: NavigatorKPI): MetricKind {
  const nameN = norm(kpi.name);
  const l2 = kpi.l2_projection;
  const method = l2?.method;
  const chartAgg = (kpi.chart?.aggregation ?? "").toLowerCase();
  const unit = (kpi.l1?.unit ?? "").trim();

  if (chartAgg === "count") return "snapshot";
  if (unit === "%" || nameHasAny(nameN, RATE_KWS)) return "rate";
  if (method === "ratio") return "rate";
  if (nameHasAny(nameN, SNAPSHOT_KWS)) return "snapshot";
  if (nameHasAny(nameN, ["hold", "pending transfer", "queue depth", "backlog"])) return "snapshot";

  const ctype = (kpi.chart?.type ?? "").toLowerCase();
  if (
    (ctype === "gauge_chart" || ctype === "horizontal_bar_chart" || ctype === "kpi_card" || ctype === "heatmap_chart" || ctype === "map_chart")
    && kpi.chart?.x_axis_type !== "temporal"
  ) {
    return "snapshot";
  }

  if (method === "daily_rate" || method === "growth_rate") return "accumulator";
  if (nameHasAny(nameN, FLOW_KWS)) return "accumulator";
  if (isTemporalKpi(kpi) && TEMPORAL_CHART_TYPES.has(ctype) && !nameHasAny(nameN, FLOW_KWS)) {
    return "snapshot";
  }
  return metricKind(method);
}

/** Aggregation within each time bucket (charts + L3 alignment). */
export function resolveChartAggregation(kpi: NavigatorKPI): string {
  const l2 = kpi.l2_projection;
  const kind = resolveMetricKind(kpi);
  if (kind === "snapshot") {
    const ca = (kpi.chart?.aggregation ?? "").toLowerCase();
    if (ca === "sum" || ca === "avg" || ca === "count" || ca === "min" || ca === "max") return ca;
    return "avg";
  }
  if (kind === "rate") return "avg";
  if (l2?.aggregation) return l2.aggregation;
  return (kpi.chart?.aggregation ?? "sum").toLowerCase();
}

export function parseMetricDate(val: unknown): Date | null {
  if (val === null || val === undefined) return null;
  if (val instanceof Date) return val;
  const s = String(val).trim();
  if (!s) return null;
  const d = new Date(s);
  if (!isNaN(d.getTime())) return d;
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return null;
}

/** Pipeline eligibility for L3 (mirrors pipeline/metric_contract.resolve_l3_eligible). */
export function resolveL3Eligible(kpi: NavigatorKPI): boolean {
  const vn = kpi.l1?.view_name ?? "";
  if (vn.toUpperCase().startsWith("[TABLE] FORECAST")) return false;

  const nameN = norm(kpi.name);
  if (nameN.includes("snapshot") || nameN.includes("heatmap")) return false;

  const ctype = (kpi.chart?.type ?? "").toLowerCase();
  const xType = kpi.chart?.x_axis_type;
  if (ctype === "horizontal_bar_chart" || ctype === "gauge_chart" || ctype === "kpi_card" || ctype === "heatmap_chart") {
    if (xType !== "temporal") return false;
  }

  if (resolveMetricKind(kpi) === "snapshot" && !nameN.includes("trend") && !nameN.includes("over time")) {
    return false;
  }

  const fieldN = norm(kpi.l1?.field_name ?? "");
  const yN = norm(kpi.chart?.y_axis ?? "");
  if (fieldN.includes("overtime hours") || fieldN.includes("agency hours")) return false;
  if (yN.includes("overtime hours") || yN.includes("agency hours")) return false;

  return xType === "temporal" || TEMPORAL_CHART_TYPES.has(ctype);
}

/** True when 7D/30D uses forward daily_rate / growth_rate (not windowed history). */
export function usesL2ForwardProjection(kpi: NavigatorKPI): boolean {
  const m = kpi.l2_projection?.method;
  return m === "daily_rate" || m === "growth_rate";
}

/** Config declares L2 projection for this period (all methods including ratio). */
export function hasL2ProjectionLayer(kpi: NavigatorKPI): boolean {
  if (!kpi.l2_projection) return false;
  const layers = kpi.forecast_layers;
  if (layers?.length) return layers.includes("l2_projection");
  return true;
}

/** Config declares L3 forecast for this KPI. */
export function hasL3ProjectionLayer(kpi: NavigatorKPI): boolean {
  if (!hasL3ForecastData(kpi)) return false;
  const layers = kpi.forecast_layers;
  if (layers?.length) return layers.includes("l3");
  return true;
}

/** True when the chart should draw the L2 projection line on 7D/30D (alongside L3). */
export function showsL2ProjectionOnChart(
  kpi: NavigatorKPI,
  period: "now" | "7d" | "30d",
): boolean {
  if (period === "now") return false;
  return hasL2ProjectionLayer(kpi) && isTemporalKpi(kpi);
}

const NON_TEMPORAL_CHART_TYPES = new Set([
  "gauge_chart", "heatmap_chart", "horizontal_bar_chart", "kpi_card", "scorecard",
]);

/** Whether 7D/30D changes headline or chart for this KPI (mirrors audit non-temporal list). */
export function kpiSupportsPeriod(kpi: NavigatorKPI): boolean {
  const ctype = (kpi.chart?.type ?? "").toLowerCase();
  if (NON_TEMPORAL_CHART_TYPES.has(ctype) && kpi.chart?.x_axis_type !== "temporal") {
    return false;
  }
  const nameN = norm(kpi.name);
  if (nameN.includes("snapshot") || nameN.includes("heatmap")) return false;
  if (isTemporalKpi(kpi)) return true;
  if (hasL3ForecastData(kpi)) return true;
  if (hasL2ProjectionLayer(kpi)) return true;
  return false;
}

/** True when any KPI on the persona benefits from the period toggle. */
export function personaSupportsPeriod(kpis: NavigatorKPI[]): boolean {
  return kpis.some(kpiSupportsPeriod);
}

/** How to combine per-series L3 values into one headline. */
export function resolveL3BreakdownAggregate(kpi: NavigatorKPI): "sum" | "avg" | "max" {
  const unit = (kpi.l1?.unit ?? "").trim();
  const isPercent = unit === "%" || (kpi.l2_projection?.method === "ratio" && unit !== "hours");
  const kind = resolveMetricKind(kpi);
  if (isPercent || kind === "rate") return "avg";
  if (kind === "snapshot") {
    const agg = resolveChartAggregation(kpi).toLowerCase();
    if (agg === "avg") return "avg";
    if (agg === "max") return "max";
    // Level metrics (beds, occupancy): average across breakdown series unless flow KPI.
    const nameN = norm(kpi.name);
    if (nameN.includes("hold") || nameN.includes("transfer") || nameN.includes("referral") || nameN.includes("volume")) {
      return "sum";
    }
    return "avg";
  }
  return "sum";
}

/** Unified headline for Now / 7D / 30D — matches chart window or L3 forecast. */
export function resolvePeriodHeadline(
  kpi: NavigatorKPI,
  rows: Record<string, unknown>[],
  period: "now" | "7d" | "30d",
  configL1?: number | null,
): { value: number | null; layer: "L1" | "L2" | "L3" } {
  const effectivePeriod = kpiSupportsPeriod(kpi) ? period : "now";

  if (!rows.length) {
    return { value: configL1 ?? null, layer: "L1" };
  }

  if (effectivePeriod === "now") {
    if (kpi.value_source === "agent_derived" && kpi.l2_derived?.value != null) {
      return { value: kpi.l2_derived.value, layer: "L2" };
    }
    const v = computeL1Value(kpi, rows);
    const layer = kpi.value_source === "agent_derived" ? "L2" : "L1";
    return { value: v ?? configL1 ?? null, layer };
  }

  const horizon = effectivePeriod === "7d" ? 7 : 30;

  // L3 headline — pipeline-saved forecast; do not gate on l1MatchesConfig (different horizon/scale).
  if (hasL3ProjectionLayer(kpi)) {
    const l3 = resolveL3Value(kpi, effectivePeriod);
    if (l3 !== null && Number.isFinite(l3)) {
      return { value: l3, layer: "L3" };
    }
  }

  // L2 headline — any l2_projection method on temporal KPIs (ratio, daily_rate, growth_rate).
  if (showsL2ProjectionOnChart(kpi, effectivePeriod) && kpi.l2_projection) {
    const projected = computeL2ProjectionValue(kpi, rows, horizon);
    if (projected !== null && Number.isFinite(projected)) {
      return { value: projected, layer: "L2" };
    }
  }

  const temporal = isTemporalKpi(kpi);
  const windowed = temporal ? filterRowsForPeriod(rows, kpi, effectivePeriod) : rows;
  const windowL1 = computeL1Value(kpi, windowed);
  return { value: windowL1 ?? configL1 ?? null, layer: "L1" };
}

/** Whether saved L3 forecast data exists for 7D/30D display and chart overlays. */
export function hasL3ForecastData(kpi: NavigatorKPI): boolean {
  if (kpi.chart?.breakdown_by) {
    const bySeries = kpi.l3_forecast_by_series;
    return !!(bySeries && Object.keys(bySeries).length) || !!kpi.l3_forecast?.predictions?.length;
  }
  return !!kpi.l3_forecast?.predictions?.length;
}

/** @deprecated Prefer hasL3ForecastData (display) or resolveL3Eligible (pipeline). */
export function l3Eligible(kpi: NavigatorKPI): boolean {
  return hasL3ForecastData(kpi);
}

export function resolveL3HorizonIndex(period: "7d" | "30d"): number {
  return period === "7d" ? 6 : 29;
}

/** L3 headline value for 7D / 30D tile — matches aggregate forecast on chart. */
export function resolveL3Value(kpi: NavigatorKPI, period: "7d" | "30d"): number | null {
  if (!hasL3ForecastData(kpi)) return null;
  const idx = resolveL3HorizonIndex(period);
  const preds = kpi.l3_forecast?.predictions;
  if (preds?.length) {
    return preds[Math.min(idx, preds.length - 1)];
  }
  const bySeries = kpi.l3_forecast_by_series;
  if (!bySeries) return null;
  const vals: number[] = [];
  for (const fc of Object.values(bySeries)) {
    if (fc.predictions?.length) {
      vals.push(fc.predictions[Math.min(idx, fc.predictions.length - 1)]);
    }
  }
  if (!vals.length) return null;

  const agg = resolveL3BreakdownAggregate(kpi);
  if (agg === "avg") return vals.reduce((a, b) => a + b, 0) / vals.length;
  if (agg === "max") return Math.max(...vals);
  return vals.reduce((a, b) => a + b, 0);
}

/** Human-readable label for a raw dimension key (facility_id, department_id, …). */
export function dimensionDisplayLabel(
  raw: string,
  labels?: Record<string, string> | null,
): string {
  if (!labels) return raw;
  if (labels[raw]) return labels[raw];
  const s = String(raw);
  if (labels[s]) return labels[s];
  const n = Number(raw);
  if (!Number.isNaN(n) && Number.isFinite(n)) {
    const rounded = String(Math.round(n));
    if (labels[rounded]) return labels[rounded];
    if (labels[`Facility_${rounded}`]) return labels[`Facility_${rounded}`];
  }
  return raw;
}

export function labelsForDimensionColumn(
  col: string | null | undefined,
  maps?: Record<string, Record<string, string>> | null,
): Record<string, string> {
  if (!col || !maps) return {};
  const h = norm(col).replace(/\s+/g, "_");
  if (maps[col]) return maps[col];
  if (maps[h]) return maps[h];
  for (const [k, v] of Object.entries(maps)) {
    if (norm(k).replace(/\s+/g, "_") === h) return v;
  }
  return {};
}

/** Build id→name labels when the same table has parallel id + name columns. */
export function buildIdNameLabelsFromRows(
  rows: Record<string, unknown>[],
  idCol: string | null | undefined,
): Record<string, string> {
  if (!rows.length || !idCol) return {};
  const idNorm = norm(idCol);
  const base = idNorm.replace(/ id$/, "").replace(/_id$/, "").trim();
  if (!base) return {};
  const cols = Object.keys(rows[0]);
  let nameCol: string | null = null;
  for (const c of cols) {
    const cn = norm(c);
    if (cn === idNorm || cn.endsWith("_id")) continue;
    if (cn.includes("name") && cn.includes(base)) {
      nameCol = c;
      break;
    }
  }
  if (!nameCol) return {};
  const out: Record<string, string> = {};
  for (const r of rows) {
    const ik = String(r[idCol] ?? "").trim();
    const nk = String(r[nameCol] ?? "").trim();
    if (ik && nk && ik !== "(null)" && nk !== "(null)") out[ik] = nk;
  }
  return Object.keys(out).length >= 2 ? out : {};
}

export function mergeDimensionLabels(
  ...sources: (Record<string, string> | null | undefined)[]
): Record<string, string> {
  const merged: Record<string, string> = {};
  for (const src of sources) {
    if (src) Object.assign(merged, src);
  }
  return merged;
}

/** Map display label back to raw breakdown key for l3_forecast_by_series lookup. */
export function breakdownRawKey(
  displayName: string,
  labels?: Record<string, string> | null,
): string {
  if (!labels) return displayName;
  for (const [raw, disp] of Object.entries(labels)) {
    if (disp === displayName) return raw;
  }
  const facility = /^Facility_(\d+)$/i.exec(displayName);
  if (facility) return facility[1];
  return displayName;
}

/** Resolve per-series L3 forecast by display name / raw breakdown key. */
export function resolveL3SeriesForecast(
  seriesName: string,
  bySeries: Record<string, { predictions?: number[] }>,
  labels?: Record<string, string> | null,
): { predictions?: number[]; lower_p10?: number[]; upper_p90?: number[] } | undefined {
  const raw = breakdownRawKey(seriesName, labels);
  if (bySeries[raw]) return bySeries[raw];
  if (bySeries[seriesName]) return bySeries[seriesName];
  const facility = /^Facility_(\d+)$/i.exec(seriesName);
  if (facility && bySeries[facility[1]]) return bySeries[facility[1]];
  return undefined;
}

/** Restrict rows to the last N days relative to the newest date in the dataset. */
export function filterRowsForPeriod(
  rows: Record<string, unknown>[],
  kpi: NavigatorKPI,
  period: "now" | "7d" | "30d",
): Record<string, unknown>[] {
  if (period === "now" || !rows.length) return rows;
  const days = period === "7d" ? 7 : 30;
  const l2 = kpi.l2_projection;
  const dateCol = findDateColumn(rows, l2?.date_field, kpi.chart?.x_axis);
  if (!dateCol) return rows;

  const dates = rows
    .map((r) => parseMetricDate(r[dateCol]))
    .filter((d): d is Date => d !== null);
  if (!dates.length) return rows;

  const maxMs = Math.max(...dates.map((d) => d.getTime()));
  const cutoff = new Date(maxMs);
  cutoff.setDate(cutoff.getDate() - days);

  return rows.filter((r) => {
    const d = parseMetricDate(r[dateCol]);
    return d !== null && d.getTime() >= cutoff.getTime();
  });
}

function columnIsNumeric(rows: Record<string, unknown>[], col: string): boolean {
  for (const r of rows.slice(0, 20)) {
    if (parseNum(r[col]) !== null) return true;
  }
  return false;
}

/** Count KPIs: entities in latest period above p75 of numeric y-axis (high-risk style). */
export function computeCountBreakdownHeadline(
  kpi: NavigatorKPI,
  rows: Record<string, unknown>[],
): number | null {
  const chartAgg = (kpi.chart?.aggregation ?? "").toLowerCase();
  const l2Agg = (kpi.l2_projection?.aggregation ?? "").toLowerCase();
  const isCount = chartAgg === "count" || l2Agg === "count";
  if (!isCount || !kpi.chart?.breakdown_by) return null;
  const yCol = findColumn(rows, kpi.chart.y_axis);
  const byCol = findColumn(rows, kpi.chart.breakdown_by);
  if (!yCol || !byCol || !columnIsNumeric(rows, yCol)) return null;

  const ys = rows.map((r) => parseNum(r[yCol])).filter((v): v is number => v !== null);
  if (!ys.length) return null;
  const sorted = [...ys].sort((a, b) => a - b);
  const threshold = sorted[Math.max(Math.floor(sorted.length * 0.75) - 1, 0)];

  const dateCol = findDateColumn(rows, kpi.l2_projection?.date_field, kpi.chart?.x_axis);
  let sub = rows;
  if (dateCol) {
    const hourly = isHourlyDates(rows, dateCol);
    const keys = [
      ...new Set(
        rows.filter((r) => r[dateCol] != null).map((r) => bucketKey(r[dateCol], hourly)),
      ),
    ].sort();
    if (!keys.length) return null;
    const latest = keys[keys.length - 1];
    sub = rows.filter(
      (r) => r[dateCol] != null && bucketKey(r[dateCol], hourly) === latest,
    );
  }

  const byEntity: Record<string, number> = {};
  for (const r of sub) {
    const bk = String(r[byCol] ?? "");
    if (!bk || bk === "(null)") continue;
    const y = parseNum(r[yCol]);
    if (y === null) continue;
    byEntity[bk] = byEntity[bk] === undefined ? y : Math.max(byEntity[bk], y);
  }
  return Object.values(byEntity).filter((v) => v >= threshold).length;
}

/** L1 headline from live rows (metric contract). */
export function computeL1Value(kpi: NavigatorKPI, rows: Record<string, unknown>[]): number | null {
  if (kpi.value_source === "agent_derived" && kpi.l2_derived?.value != null) {
    return kpi.l2_derived.value;
  }
  if (!rows.length || !kpi.l1) return null;
  const l2 = kpi.l2_projection;
  let valueCol = findColumn(rows, l2?.value_field ?? kpi.l1.field_name);
  if (!valueCol) valueCol = findColumn(rows, kpi.l1.field_name);
  if (!valueCol && kpi.chart?.y_axis) valueCol = findColumn(rows, kpi.chart.y_axis);
  if (!valueCol) return null;

  const countHeadline = computeCountBreakdownHeadline(kpi, rows);
  if (countHeadline !== null) return countHeadline;

  const nameN = norm(kpi.name);
  if (nameN.includes("conversion") && nameN.includes("rate")) {
    const conv = computeConversionRate(rows);
    if (conv !== null) return conv;
  }
  if (nameN.includes("isolation") && nameN.includes("utilization")) {
    const iso = computeIsolationUtilization(rows);
    if (iso !== null) return iso;
  }

  if (!columnIsNumeric(rows, valueCol)) return null;

  const kind = resolveMetricKind(kpi);
  const dateCol = findDateColumn(rows, l2?.date_field, kpi.chart?.x_axis);

  if (kind === "snapshot" && dateCol) {
    if (nameN.includes("available beds")) {
      const bucketAgg = resolveChartAggregation(kpi);
      const series = bucketSeries(rows, valueCol, dateCol, bucketAgg);
      return series.length ? series[series.length - 1].y : null;
    }
    if (isHourlyDates(rows, dateCol)) {
      const keys = [
        ...new Set(
          rows.filter((r) => r[dateCol] != null).map((r) => bucketKey(r[dateCol], true)),
        ),
      ].sort();
      if (!keys.length) return null;
      const latest = keys[keys.length - 1];
      const sub = rows.filter(
        (r) => r[dateCol] != null && bucketKey(r[dateCol], true) === latest,
      );
      const vals = sub.map((r) => parseNum(r[valueCol])).filter((v): v is number => v !== null);
      return vals.length ? vals.reduce((a, b) => a + b, 0) : null;
    }
    const bucketAgg = resolveChartAggregation(kpi);
    const series = bucketSeries(rows, valueCol, dateCol, bucketAgg);
    return series.length ? series[series.length - 1].y : null;
  }

  if (kind === "rate") {
    if (dateCol) {
      const series = bucketSeries(rows, valueCol, dateCol, "avg");
      if (series.length) return series[series.length - 1].y;
    }
    const vals = rows.map((r) => parseNum(r[valueCol])).filter((v): v is number => v !== null);
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  }

  if (l2?.method === "daily_rate" && dateCol) {
    const series = bucketSeries(rows, valueCol, dateCol, "sum");
    if (series.length) {
      const vals = series.map((p) => p.y);
      if (isCumulativeSeries(vals) && series.length >= 2) {
        return series[series.length - 1].y - series[series.length - 2].y;
      }
      return series[series.length - 1].y;
    }
  }

  const agg = (l2?.aggregation ?? kpi.chart?.aggregation ?? "sum").toLowerCase();
  if (dateCol && agg === "sum" && kind === "accumulator") {
    const series = bucketSeries(rows, valueCol, dateCol, "sum");
    if (series.length) {
      const vals = series.map((p) => p.y);
      if (isCumulativeSeries(vals) && series.length >= 2) {
        return series[series.length - 1].y - series[series.length - 2].y;
      }
      return series[series.length - 1].y;
    }
  }

  const vals = rows.map((r) => parseNum(r[valueCol])).filter((v): v is number => v !== null);
  return vals.length ? aggregateValues(vals, agg) : null;
}

/** L2 projection for 7D / 30D horizons. */
export function computeL2ProjectionValue(
  kpi: NavigatorKPI,
  rows: Record<string, unknown>[],
  horizonDays: number,
): number | null {
  if (!rows.length || !kpi.l2_projection) return null;
  const l2 = kpi.l2_projection;
  let valueCol = findColumn(rows, l2.value_field);
  if (!valueCol) valueCol = findColumn(rows, kpi.l1.field_name);
  if (!valueCol && kpi.chart?.y_axis) valueCol = findColumn(rows, kpi.chart.y_axis);
  if (!valueCol) return null;
  const dateCol = findDateColumn(rows, l2.date_field, kpi.chart?.x_axis);

  if (l2.method === "ratio") {
    return computeL1Value(kpi, rows);
  }

  if (l2.method === "daily_rate") {
    if (!dateCol) return computeL1Value(kpi, rows);
    const series = bucketSeries(rows, valueCol, dateCol, "sum");
    if (series.length < 2) return computeL1Value(kpi, rows);
    const vals = series.map((p) => p.y);
    if (isCumulativeSeries(vals)) {
      const inc = incrementsFromCumulative(series);
      const avgDaily = inc.reduce((a, p) => a + p.y, 0) / inc.length;
      return avgDaily * horizonDays;
    }
    const total = series.reduce((a, p) => a + p.y, 0);
    const keys = new Set(
      rows.filter((r) => r[dateCol] != null).map((r) => bucketKey(r[dateCol], isHourlyDates(rows, dateCol))),
    );
    const spanDays = Math.max(keys.size, 1);
    return (total / spanDays) * horizonDays;
  }

  if (l2.method === "growth_rate" && dateCol) {
    const series = bucketSeries(rows, valueCol, dateCol, l2.aggregation);
    if (series.length < 2) return computeL1Value(kpi, rows);
    const first = series[0].y;
    const last = series[series.length - 1].y;
    if (!first) return last;
    const growth = Math.pow(last / first, 1 / (series.length - 1)) - 1;
    const periodsAhead = horizonDays / Math.max(series.length, 1);
    return last * Math.pow(1 + growth, periodsAhead);
  }

  return computeL1Value(kpi, rows);
}

/** Soft sanity check vs pipeline-computed config L1. */
/** For temporal charts: snapshot KPIs on hourly data use hour buckets, not day. */
export function temporalGroupKey(
  raw: string,
  kpi: NavigatorKPI,
  rows: Record<string, unknown>[],
  xCol: string,
): string {
  const isoDay = raw.match(/^(\d{4}-\d{2}-\d{2})[T ]/);
  if (!isoDay) return raw;
  if (resolveMetricKind(kpi) === "snapshot" && isHourlyDates(rows, xCol)) {
    return raw.slice(0, 13);
  }
  return isoDay[1];
}

export function l1MatchesConfig(live: number, configL1: number): boolean {
  if (!Math.abs(configL1)) return true;
  const ratio = Math.abs(live) / Math.abs(configL1);
  return ratio <= 10 && ratio >= 0.1;
}
