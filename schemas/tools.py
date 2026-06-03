"""
schemas/tools.py
────────────────
Claude API tool definitions for every agent in the pipeline.

These are the FIXED boundaries of the system.
• The tool *signatures* (name, parameters, description) are fixed code.
• What the agent DOES with them — which tools it calls, what values it passes —
  is 100% agent-decided at runtime.

Tool groups
───────────
  ORCHESTRATOR_TOOLS   — used by the orchestrator agent
  DOMAIN_TOOLS         — used by domain analysis sub-agents
  CHART_TOOLS          — used by chart/explanation sub-agents
"""

from __future__ import annotations

from schemas.config import CHART_TYPES

# ── shared type aliases ──────────────────────────────────────────────────────
_STRING   = {"type": "string"}
_NUMBER   = {"type": "number"}
_BOOLEAN  = {"type": "boolean"}
_ARRAY    = lambda items: {"type": "array", "items": items}
_OBJECT   = lambda props, required=None: {
    "type": "object",
    "properties": props,
    **({"required": required} if required else {}),
}


# ════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR TOOLS
# Called by the orchestrator to spin up sub-agents and assemble the config.
# ════════════════════════════════════════════════════════════════════════════

ORCHESTRATOR_TOOLS: list[dict] = [

    {
        "name": "analyze_domain",
        "description": (
            "Spin up a domain analysis sub-agent for a specific business domain. "
            "Pass the KPIs Navigator has designed for this domain — the sub-agent "
            "finds the data in Tableau to compute them, detects trends and anomalies, "
            "and returns structured results. "
            "Call multiple times in parallel for different domains."
        ),
        "input_schema": _OBJECT(
            {
                "domain_name": {
                    **_STRING,
                    "description": "The business domain, e.g. 'Sales Performance'",
                },
                "relevant_views": {
                    **_ARRAY(_STRING),
                    "description": "Tableau view names likely to contain data for this domain (from available_api_views only)",
                },
                "relevant_fields": {
                    **_ARRAY(_STRING),
                    "description": (
                        "Exact field names from the 'reachable_fields' list that are relevant "
                        "to this domain. Use the 'name' values ONLY — these are the real CSV "
                        "column names (e.g. 'Sales', 'Profit_Ratio', 'Tourism_Inbound'). "
                        "Do NOT use metadata display names with spaces."
                    ),
                },
                "kpi_designs": {
                    **_ARRAY(
                        _OBJECT({
                            "name":             {**_STRING, "description": "KPI name, e.g. 'Revenue per Order'"},
                            "description":      {**_STRING, "description": "What this KPI measures and why it matters"},
                            "computation_hint": {**_STRING, "description": "How to compute it from available data"},
                        })
                    ),
                    "description": (
                        "KPIs Navigator has designed for this domain. "
                        "The domain agent finds the data and computes each one."
                    ),
                },
            },
            required=["domain_name", "relevant_views", "kpi_designs"],
        ),
    },

    {
        "name": "generate_chart_spec",
        "description": (
            "Spin up a chart and explanation sub-agent for a single KPI. "
            "The sub-agent selects the best chart type, defines the axes/breakdowns, "
            "and writes the plain-language explanation. "
            "You may call this tool multiple times in parallel for different KPIs."
        ),
        "input_schema": _OBJECT(
            {
                "kpi_id":   {**_STRING, "description": "Unique snake_case identifier for this KPI"},
                "kpi_name": {**_STRING, "description": "Display name, e.g. 'Total Sales'"},
                "kpi_description": {**_STRING, "description": "One-sentence description of what this KPI measures"},
                "domain": {**_STRING, "description": "Which domain this KPI belongs to"},
                "l1_value": {
                    "description": "Current value fetched from Tableau (null if unavailable)",
                    "oneOf": [{"type": "number"}, {"type": "string"}, {"type": "null"}],
                },
                "l1_unit":   {**_STRING, "description": "Unit of the value, e.g. 'USD', '%', 'days'"},
                "l1_format": {
                    **_STRING,
                    "description": "Display format: 'currency' | 'percentage' | 'number' | 'text'",
                },
                "l1_view_name":  {**_STRING, "description": "Tableau view where the value was fetched"},
                "l1_field_name": {**_STRING, "description": "Field/measure name in that view"},
                "has_formula": {
                    **_BOOLEAN,
                    "description": "True if this KPI has a calculated formula enabling L2 forecast",
                },
                "formula":    {
                    "description": "The Tableau calculated field formula, if any",
                    "oneOf": [_STRING, {"type": "null"}],
                },
                "formula_parameters": {
                    **_ARRAY(_STRING),
                    "description": "Parameter names used in the formula",
                },
                "available_dimensions": {
                    **_ARRAY(_STRING),
                    "description": "Dimension field names available for breakdown/axis in this datasource",
                },
                "objective": {**_STRING, "description": "The overall business objective"},
                "persona_role": {**_STRING, "description": "The persona role, e.g. 'Sales Operations Director'"},
            },
            required=["kpi_id", "kpi_name", "kpi_description", "domain",
                      "l1_view_name", "l1_field_name", "objective", "persona_role"],
        ),
    },

    {
        "name": "emit_intelligence_config",
        "description": (
            "Emit the final assembled Intelligence Config JSON. "
            "Call this ONCE when all domain analyses and chart specs are complete. "
            "Provide as many personas as the workbook genuinely warrants — each derived "
            "from the workbook data, each with their own curated set of dashboard sections "
            "and KPIs. Do not cap personas or KPIs artificially. "
            "This is the final output of the entire pipeline."
        ),
        "input_schema": _OBJECT(
            {
                "objective": {
                    **_STRING,
                    "description": "The single business objective shared across all personas",
                },
                "personas": {
                    **_ARRAY(
                        _OBJECT(
                            {
                                "role": {
                                    **_STRING,
                                    "description": "Persona role title, e.g. 'Sales Operations Director'",
                                },
                                "focus_areas": {
                                    **_ARRAY(_STRING),
                                    "description": "Keywords describing what this persona cares about",
                                },
                                "rationale": {
                                    **_STRING,
                                    "description": "Why this persona was derived from the workbook data",
                                },
                                "persona_level": {
                                    "type": "string",
                                    "enum": ["executive", "manager", "analyst"],
                                    "description": (
                                        "Audience complexity level — determines how much detail the dashboard shows:\n"
                                        "  executive — C-suite, VP, Director. They need: ONE screen, 4-6 KPIs max, "
                                        "big headline numbers, minimal chart labels, no jargon. Examples: CEO, CFO, COO, "
                                        "'VP of Sales', 'Chief Revenue Officer', 'Medical Director'.\n"
                                        "  manager   — Dept head, operations lead, team manager. They need: comprehensive "
                                        "view, all KPIs, full charts, breakdowns. Examples: 'Sales Operations Manager', "
                                        "'Supply Chain Manager', 'Admissions Manager'.\n"
                                        "  analyst   — BI analyst, data scientist, power user. They need: everything — "
                                        "all metadata, field names, raw values, technical detail."
                                    ),
                                },
                                "summary_cards": {
                                    **_ARRAY(
                                        _OBJECT({
                                            "title":  _STRING,
                                            "body":   _STRING,
                                            "signal": {
                                                "type": "string",
                                                "enum": ["positive", "warning", "neutral"],
                                            },
                                        })
                                    ),
                                    "description": (
                                        "Exactly 3 AI-written summary cards for this persona's dashboard. "
                                        "Each card has a short title, a 2-3 sentence body written in plain business language, "
                                        "and a signal: 'positive' (things going well), 'warning' (needs attention / risk), "
                                        "or 'neutral' (informational / context). "
                                        "Cover different angles — e.g. overall performance, a specific risk, and an opportunity."
                                    ),
                                },
                                "dashboard_sections": {
                                    **_ARRAY(
                                        _OBJECT({
                                            "id":          _STRING,
                                            "title":       _STRING,
                                            "description": _STRING,
                                            "kpi_ids":     _ARRAY(_STRING),
                                        })
                                    ),
                                    "description": "Sections curated for this persona (ordered most to least important)",
                                },
                            },
                            required=["role", "focus_areas", "rationale", "summary_cards", "dashboard_sections"],
                        )
                    ),
                    "description": "Personas derived from the workbook — as many as the data warrants, each with their own dashboard",
                },
            },
            required=["objective", "personas"],
        ),
    },
]


# ════════════════════════════════════════════════════════════════════════════
# DOMAIN TOOLS
# Called by domain analysis sub-agents.
# ════════════════════════════════════════════════════════════════════════════

DOMAIN_TOOLS: list[dict] = [

    {
        "name": "fetch_view_data",
        "description": (
            "Fetch the data underlying a Tableau view/sheet as a list of rows. "
            "Use this to get actual KPI values, time-series data, and breakdowns. "
            "Returns up to 200 rows by default. "
            "After fetching, use run_analysis to explore the data with pandas before computing KPIs."
        ),
        "input_schema": _OBJECT(
            {
                "view_name": {
                    **_STRING,
                    "description": "Exact name of the Tableau view/sheet to fetch data from",
                },
                "max_rows": {
                    **_NUMBER,
                    "description": "Maximum rows to return (default 200, max 2000)",
                },
                "filters": {
                    **_ARRAY(
                        _OBJECT({
                            "field": _STRING,
                            "values": _ARRAY(_STRING),
                        })
                    ),
                    "description": "Optional filters to apply (field name + allowed values)",
                },
            },
            required=["view_name"],
        ),
    },

    {
        "name": "run_analysis",
        "description": (
            "Run a pandas expression on data previously fetched with fetch_view_data. "
            "Use this to explore the data, verify values, and compute derived metrics "
            "BEFORE deciding on KPI values. This eliminates assumptions — you verify "
            "everything against real data.\n\n"
            "Call fetch_view_data first, then use run_analysis to explore:\n"
            "  - Distribution of values: df['Status'].value_counts()\n"
            "  - Filtered counts: df[df['Flag']==True].shape[0]\n"
            "  - Group aggregations: df.groupby('Department')['Gap'].mean()\n"
            "  - Conversion rates: df['Converted'].sum() / df['Total'].sum()\n"
            "  - Correlations: df[['Field1','Field2']].corr()\n"
            "  - Time ranges: df['Date'].min(), df['Date'].max()\n\n"
            "ALWAYS use run_analysis before computing a KPI value — never assume "
            "what the data contains. The result tells you exactly what to compute."
        ),
        "input_schema": _OBJECT(
            {
                "view_name": {
                    **_STRING,
                    "description": "View name that was already fetched with fetch_view_data",
                },
                "expression": {
                    **_STRING,
                    "description": (
                        "A single pandas expression (not a statement, no assignments). "
                        "The DataFrame is available as 'df'. Examples:\n"
                        "  df['Referral Status'].value_counts()\n"
                        "  df[df['Escalation Flag']==True]['Referral Count'].sum()\n"
                        "  df.groupby('Facility Name')['Staffing Gap'].mean().sort_values()\n"
                        "  df['Converted Count'].sum() / df['Referral Count'].sum()\n"
                        "  df.groupby('Insurance Type')['Rejected Count'].sum()"
                    ),
                },
            },
            required=["view_name", "expression"],
        ),
    },

    {
        "name": "emit_domain_result",
        "description": (
            "Emit the analysis result for this domain. "
            "Call this once when you have finished analyzing the domain. "
            "Include one entry per KPI you identified and analyzed."
        ),
        "input_schema": _OBJECT(
            {
                "domain_name": {**_STRING, "description": "The business domain analyzed"},
                "kpis": {
                    **_ARRAY(
                        _OBJECT(
                            {
                                "id":    {**_STRING, "description": "snake_case unique id, e.g. 'total_sales'"},
                                "name":  {**_STRING, "description": "Display name"},
                                "description": {**_STRING, "description": "What this KPI measures"},
                                "layer": {
                                    "type": "string",
                                    "enum": ["L1", "L2", "L3"],
                                    "description": (
                                        "KPI layer type — determines how the value is obtained:\n"
                                        "  L1 = Direct: value fetched straight from Tableau (a raw sum/count/avg of a field).\n"
                                        "       Use for: total sales, headcount, average rating, any single-field aggregation.\n"
                                        "  L2 = Deterministic: value is a ratio, formula, or multi-field calculation.\n"
                                        "       Use for: profit margin (profit/sales), CAGR, cost-per-unit, any derived metric.\n"
                                        "  L3 = Predictive: ML-model forecast (reserved for future use — do not use now)."
                                    ),
                                },
                                "l1_value": {
                                    "description": "Fetched current value (number or string)",
                                    "oneOf": [{"type": "number"}, {"type": "string"}, {"type": "null"}],
                                },
                                "l1_unit":       _STRING,
                                "l1_format":     _STRING,
                                "l1_view_name":  _STRING,
                                "l1_field_name": _STRING,
                                "trend_description": {
                                    "description": "Plain-language trend description (e.g. 'Up 12% vs prior period')",
                                    "oneOf": [_STRING, {"type": "null"}],
                                },
                                "anomaly":  {
                                    "description": "Anomaly or risk flag, if detected",
                                    "oneOf": [_STRING, {"type": "null"}],
                                },
                                "trend_direction": {
                                    "description": "'up', 'down', or 'flat' — direction of the trend vs prior period",
                                    "oneOf": [{"type": "string", "enum": ["up", "down", "flat"]}, {"type": "null"}],
                                },
                                "trend_pct": {
                                    "description": "Percentage change vs prior period (e.g. 12.3 for +12.3%)",
                                    "oneOf": [_NUMBER, {"type": "null"}],
                                },
                                "raw_data": {
                                    **_ARRAY({"type": "object"}),
                                    "description": "Raw rows from Tableau for this KPI (for chart rendering). LIMIT TO 20 ROWS MAXIMUM — use the most recent/representative rows.",
                                },
                                "key_drivers": {
                                    "description": (
                                        "For KPIs that are declining, negative, or in warning territory: "
                                        "list the 2-3 primary factors driving the issue, with specific sub-segments "
                                        "and numbers. Example: ['Cardiology occupancy at 94% (vs 85% target)', "
                                        "'ICU staffing gap of -12%', 'Q4 bookings down 23% vs Q3']. "
                                        "For healthy KPIs: set to null."
                                    ),
                                    "oneOf": [_ARRAY(_STRING), {"type": "null"}],
                                },
                                "critical_segments": {
                                    "description": (
                                        "For KPIs with a breakdown dimension (facility, department, region, category, "
                                        "sales rep, product): identify which specific sub-segments are performing worst "
                                        "and need attention. Example: ['Cardiology', 'West region', 'Furniture category']. "
                                        "Use the actual values from the data — look at the categorical_breakdown in the "
                                        "fetch_view_data response. Set to null if no breakdown dimension exists."
                                    ),
                                    "oneOf": [_ARRAY(_STRING), {"type": "null"}],
                                },
                                "l2_projection": {
                                    "description": (
                                        "How to project this KPI forward for 7D/30D forecasts. "
                                        "The frontend evaluates this on fresh Tableau rows at display time.\n"
                                        "Methods:\n"
                                        "  daily_rate  — cumulative totals that accrue over time (revenue, order count, cost, units sold).\n"
                                        "               formula: sum(value_field) / date_span_days * horizon_days\n"
                                        "  ratio       — percentages/rates that remain roughly constant (profit margin, on-time rate, conversion %).\n"
                                        "               projection = same ratio, computed from value_field.\n"
                                        "  growth_rate — metrics with steady trend (customer count, market share, NPS).\n"
                                        "               projects using recent compound growth rate.\n"
                                        "  stable      — snapshot metrics that don't project forward (avg days to ship, current rating).\n"
                                        "               projection = current value unchanged.\n"
                                        "Set to null if no meaningful projection is possible."
                                    ),
                                    "oneOf": [
                                        _OBJECT(
                                            {
                                                "method": {
                                                    "type": "string",
                                                    "enum": ["daily_rate", "ratio", "growth_rate", "stable"],
                                                },
                                                "value_field": {
                                                    **_STRING,
                                                    "description": "Exact column name from the view that contains the metric value",
                                                },
                                                "aggregation": {
                                                    "type": "string",
                                                    "enum": ["sum", "avg", "count"],
                                                    "description": "How to aggregate value_field across rows",
                                                },
                                                "date_field": {
                                                    "oneOf": [_STRING, {"type": "null"}],
                                                    "description": "Date/time column name — required for daily_rate and growth_rate, null otherwise",
                                                },
                                            },
                                            required=["method", "value_field", "aggregation"],
                                        ),
                                        {"type": "null"},
                                    ],
                                },
                            },
                            required=["id", "name", "description", "layer",
                                      "l1_view_name", "l1_field_name"],
                        )
                    ),
                    "description": "List of KPIs analyzed in this domain",
                },
            },
            required=["domain_name", "kpis"],
        ),
    },
]


# ════════════════════════════════════════════════════════════════════════════
# CHART TOOLS
# Called by chart/explanation sub-agents.
# ════════════════════════════════════════════════════════════════════════════

# Build the enum list for chart types (from the Literal in config.py)
_CHART_TYPE_VALUES = [
    "kpi_card", "line_chart", "bar_chart", "stacked_bar_chart",
    "horizontal_bar_chart", "area_chart", "scatter_chart",
    "pie_chart", "map_chart", "gauge_chart", "waterfall_chart", "table",
]

CHART_TOOLS: list[dict] = [

    {
        "name": "emit_chart_spec",
        "description": (
            "Emit the chart specification and explanation for a single KPI. "
            "Choose the chart type that best communicates this KPI's story "
            "to the persona given the objective. "
            "Write explanations in plain business language — no jargon."
        ),
        "input_schema": _OBJECT(
            {
                "kpi_id": {**_STRING, "description": "The KPI id this spec belongs to"},
                "chart_type": {
                    "type": "string",
                    "enum": _CHART_TYPE_VALUES,
                    "description": "Chart type best suited for this KPI",
                },
                "x_axis":       {"oneOf": [_STRING, {"type": "null"}], "description": "Field for x-axis"},
                "y_axis":       {"oneOf": [_STRING, {"type": "null"}], "description": "Field for y-axis"},
                "x_axis_type":  {
                    "description": "Data type of the x-axis: 'categorical' | 'temporal' | 'numeric'",
                    "oneOf": [{"type": "string", "enum": ["categorical", "temporal", "numeric"]}, {"type": "null"}],
                },
                "aggregation":  {
                    "description": "How the y-axis value is aggregated: 'sum' | 'avg' | 'count' | 'min' | 'max'",
                    "oneOf": [{"type": "string", "enum": ["sum", "avg", "count", "min", "max"]}, {"type": "null"}],
                },
                "sort_order":   {
                    "description": "Default sort direction for the chart: 'asc' | 'desc' | 'none'",
                    "oneOf": [{"type": "string", "enum": ["asc", "desc", "none"]}, {"type": "null"}],
                },
                "breakdown_by": {"oneOf": [_STRING, {"type": "null"}], "description": "Field for breakdown/series"},
                "color_by":     {"oneOf": [_STRING, {"type": "null"}], "description": "Field for color encoding"},
                "sort_by":      {"oneOf": [_STRING, {"type": "null"}], "description": "Sort field"},
                "filters":      {**_ARRAY(_STRING), "description": "Active filter descriptions"},
                "chart_notes":  {"oneOf": [_STRING, {"type": "null"}], "description": "Rendering hints for frontend"},
                "explanation_what":         {**_STRING, "description": "What this KPI measures, in one sentence"},
                "explanation_why_matters":  {**_STRING, "description": "Why it matters for the business objective"},
                "explanation_trend":        {"oneOf": [_STRING, {"type": "null"}], "description": "Trend summary"},
                "explanation_risk":         {"oneOf": [_STRING, {"type": "null"}], "description": "Risk flag, if any"},
                "explanation_key_insight":  {"oneOf": [_STRING, {"type": "null"}], "description": "Standout insight"},
            },
            required=["kpi_id", "chart_type",
                      "explanation_what", "explanation_why_matters"],
        ),
    },
]
