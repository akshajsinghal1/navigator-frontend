// ─── Navigator Intelligence Config Types ─────────────────────────────────────
// Mirrors the Python IntelligenceConfig schema produced by the pipeline.
// These are the ONLY types the universal frontend needs — no domain-specific shapes.

export interface NavigatorWorkbook {
  name:               string;
  project?:           string | null;
  tableau_updated_at?: string | null;
  data_sources?:      string[];
  // legacy fields kept for backward compat
  content_url?:       string;
  luid?:              string;
  project_name?:      string | null;
  updated_at?:        string | null;
}

export interface NavigatorL1 {
  value:      number | null;
  unit:       string;         // "USD" | "%" | "days" | "hours" | "score" | ""
  format:     string;         // "currency" | "percent" | "number"
  view_name:  string;         // Tableau view to fetch live data from
  field_name: string;         // column hint for the y-axis value
}

export interface NavigatorL2 {
  formula:          string | null;
  parameters_used?: string[];
  forecast_value?:  number | null;
  method?:          string | null;
  error?:           string | null;
  // legacy fields
  parameters?:      unknown[];
  output_unit?:     string;
}

// ── L2 Projection — agent-defined formula for 7D/30D forward projections ─────
// Evaluated by the frontend on fresh Tableau rows at display time.
export type L2Method = "daily_rate" | "ratio" | "growth_rate" | "stable";

export interface L2Projection {
  method:      L2Method;
  value_field: string;          // exact column name for the metric value
  aggregation: "sum" | "avg" | "count";
  date_field:  string | null;   // date/time column (required for daily_rate, growth_rate)
}

export interface NavigatorChart {
  type:          string;           // "line_chart" | "bar_chart" | "horizontal_bar_chart" | "gauge_chart" | "pie_chart" | "donut_chart" | "kpi_card"
  x_axis:        string | null;   // column hint for x-axis (null for gauge/scorecard)
  y_axis:        string | null;   // column hint for y-axis
  x_axis_type:   string | null;   // "temporal" | "categorical" | "numeric"
  aggregation:   string;           // "sum" | "avg" | "count" | "min" | "max"
  sort_order:    string | null;   // "asc" | "desc" | "none"
  breakdown_by?: string | null;
  color_by?:     string | null;
  sort_by?:      string | null;
  filters?:      unknown[];
  notes?:        string | null;
}

export interface NavigatorKPI {
  id:               string;
  name:             string;
  description:      string;
  layer?:           "L1" | "L2" | "L3";
  priority?:        number;   // 0-100 relevancy score assigned by domain agent
  l1:               NavigatorL1;
  trend_direction:  "up" | "down" | "flat" | null;
  trend_pct:        number | null;
  chart:            NavigatorChart;
  l2:               NavigatorL2 | null;
  l2_projection?:   L2Projection | null;   // agent-defined projection method
  explanation?: {
    what?:           string;
    why_it_matters?: string;
    trend?:          string | null;
    risk?:           string | null;
    key_insight?:    string | null;
  };
  raw_data?:        Record<string, unknown>[];  // optional — used when data is embedded
}

export interface NavigatorSummaryCard {
  title:  string;
  body:   string;
  signal: "positive" | "warning" | "neutral";
}

export interface NavigatorActionItem {
  kpi_name: string;
  action:   string;
  signal:   "critical" | "watch" | "stable";
}

export interface NavigatorKpiDrivers {
  kpi_name: string;
  drivers:  string[];
}

export type PersonaLevel = "executive" | "manager" | "analyst";

export interface NavigatorPersonaInfo {
  role:             string;
  focus_areas:      string[];
  rationale?:       string;
  persona_level?:   PersonaLevel;   // executive / manager / analyst
  // legacy fields
  slug?:            string;
  decision_context?: string;
}

export interface NavigatorSection {
  id:          string;
  title:       string;
  description: string;
  kpis:        NavigatorKPI[];
  kpi_ids?:    string[];  // optional — legacy
}

export interface NavigatorPersona {
  persona:            NavigatorPersonaInfo;
  summary_cards?:     NavigatorSummaryCard[];
  action_items?:      NavigatorActionItem[];
  kpi_drivers?:       NavigatorKpiDrivers[];
  dashboard_sections: NavigatorSection[];
}

export interface NavigatorConfig {
  workbook:     NavigatorWorkbook;
  objective:    string;
  personas:     NavigatorPersona[];
  version:      string;
  generated_at: string;
  refreshed_at?: string;
}

// ── API response shape ────────────────────────────────────────────────────────

export interface ViewDataResponse {
  workbook:  string;
  view:      string;
  rows:      Record<string, unknown>[];
  row_count: number;
}
