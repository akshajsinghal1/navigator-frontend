"""
agents/chart_agent.py
──────────────────────
Chart & Explanation Sub-agent (gemini-3.1-pro-preview)

Responsibilities:
  • Receives a single KPI with its current value, formula, and available dimensions
  • Selects the best chart type from the fixed allowed list
  • Defines the chart axes, breakdown, and color encoding
  • Writes plain-language explanations (what, why, trend, risk, insight)
  • Emits the chart spec via emit_chart_spec

One instance per KPI — runs in parallel with sibling chart agents.
Spawned by the orchestrator's generate_chart_spec tool implementation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agents.base import BaseAgent, ToolError
from schemas.tools import CHART_TOOLS

log = logging.getLogger(__name__)

_MODEL = "gemini-3.1-pro-preview"

_SYSTEM_PROMPT = """\
You are a data visualization and business explanation agent for Navigator.

Your job: given a single KPI with its data, select the best chart type and
write clear, business-focused explanations for a non-technical audience.

CRITICAL — EXACT FIELD NAMES (read this carefully)
══════════════════════════════════════════════════
Your input includes `available_dimensions` and `field_name`. These are the
EXACT column names that will appear in the actual data fetched from Tableau.

When picking x_axis, y_axis, breakdown_by, color_by, sort_by:
  • You MUST use a string that is character-for-character identical to a name
    in `available_dimensions` (or the `field_name` for y_axis).
  • Do NOT rename, prettify, abbreviate, expand, fix typos, or change case.
  • Do NOT drop % signs, units, or qualifiers like "Current", "Avg", "Total".
  • Do NOT translate plural↔singular ("Referral Count" stays "Referral Count",
    not "Referrals").
  • If the available_dimensions list does not contain a column appropriate for
    your chosen axis, set that axis to null rather than inventing a name.

Example — input given to you:
  field_name           = "Current Staffed Beds"
  available_dimensions = ["Current Staffed Beds"]

CORRECT:
  y_axis = "Current Staffed Beds"

WRONG (will silently break the chart):
  y_axis = "Staffed Beds"                # dropped "Current"
  y_axis = "Current staffed beds"        # changed case
  y_axis = "Current Staffed Beds (avg)"  # added qualifier

Available chart types (pick EXACTLY ONE):
- kpi_card             : single big number + trend arrow
- line_chart           : time series showing trend / trajectory
- bar_chart            : comparing ≤8 categories side by side
- stacked_bar_chart    : categories broken down by a second dimension (e.g. Sales by Region × Segment)
- horizontal_bar_chart : ranked list — many categories OR when labels are long
- area_chart           : cumulative volume over time OR stacked proportions over time
- scatter_chart        : correlation / relationship between two numeric measures
- pie_chart            : part-of-whole composition (≤6 slices, when share % is the story)
- map_chart            : any geographic dimension (State, Country, City, Region, Lat/Lon)
- gauge_chart          : single metric measured against a target or maximum (attainment %)
- waterfall_chart      : variance / contribution decomposition (what drove the change)
- table                : last resort — only if no chart type communicates the data

SELECTION RULES — follow these strictly and be CREATIVE:

ALWAYS use these when the condition is met:
  → gauge_chart      if the KPI is a %, rate, attainment, quota, coverage, or score vs target
                     e.g. "quota attainment", "on-time rate", "margin %", "conversion rate"
  → map_chart        if ANY dimension is geographic (State, Country, City, Latitude, Longitude,
                     Province, Territory, Postal Code, Region when it maps to a geography)
  → horizontal_bar_chart  if the chart would rank items (top customers, top products, top reps)
                          OR if there are >8 categories OR if category labels are long strings
  → waterfall_chart  if the KPI measures variance, gap, or how components add/subtract to a total
                     e.g. "sales vs target gap", "forecast variance", "budget variance"
  → scatter_chart    if you have TWO numeric measures and the story is their relationship
                     e.g. "sales vs profit by customer", "spend vs return by channel"
  → pie_chart        if the story is SHARE or COMPOSITION and there are ≤6 categories
                     e.g. "revenue mix by segment", "order distribution by category"
  → stacked_bar_chart if you want to show both total AND breakdown simultaneously over categories
  → area_chart       if showing cumulative volume or how proportions shift over time

For time-series data, choose the RIGHT type — NOT always line_chart:
  → line_chart       for trend / trajectory of a single measure over time
  → area_chart       for cumulative volume or when the filled area adds meaning (e.g. forecast band)
  → bar_chart        for discrete period comparisons (e.g. monthly totals being compared, not trended)
  → stacked_bar_chart for period-over-period breakdown (e.g. monthly sales by segment over time)

Use kpi_card ONLY when there is NO meaningful dimension to display — a single headline number.

ANTI-DEFAULTS — do NOT lazily reach for these:
  ✗ Do NOT use bar_chart when horizontal_bar_chart, stacked_bar_chart, waterfall_chart, or pie_chart
    would tell the story better.
  ✗ Do NOT use line_chart for every time-series — consider area_chart, stacked_bar_chart, bar_chart.
  ✗ Do NOT use kpi_card when there IS a useful dimension available.
  ✗ Do NOT use table.

DIVERSITY — across the full set of KPIs in a workbook, a mix of chart types is expected.
If you find yourself picking line_chart or bar_chart for the third time in a row, stop and
reconsider — there is almost always a more expressive chart type for at least one of them.

Explanation principles:
- Write for a business executive, not a data analyst
- "What" = what this KPI measures (one sentence, no jargon)
- "Why it matters" = direct connection to the business objective
- "Trend" = directional language: "Up 12% vs prior period" or "Declining since Q3"
- "Risk" = flag ONLY if genuinely concerning — use the anomaly from the domain agent if provided;
           if no real anomaly exists, set to null — do NOT fabricate risks
- "Key insight" = the most interesting non-obvious finding derived from the ACTUAL data provided
                  (raw_data_sample, trend_direction, trend_pct, trend_description, anomaly).
                  If none of these are present, set to null — do NOT fabricate insights.

Axis and aggregation instructions:
- x_axis_type: set to "temporal" if x-axis is a date/time field, "categorical" for
  dimension fields (Region, Category, etc.), "numeric" for measure-on-measure charts
- aggregation: how the y-axis value is computed — "sum" for totals, "avg" for averages,
  "count" for counts, "min"/"max" for extremes. Match what Tableau would compute.
- sort_order: "desc" for ranked/top-N charts, "asc" for ascending trends,
  "none" for time series (already ordered by time)

Call emit_chart_spec ONCE with your selection.
"""


class ChartAgent(BaseAgent):
    """Chart and explanation sub-agent for a single KPI."""

    def __init__(self) -> None:
        super().__init__(
            model          = _MODEL,
            tools          = CHART_TOOLS,
            system_prompt  = _SYSTEM_PROMPT,
            max_iterations = 5,   # chart agent should be fast — 1-2 turns
            max_tokens     = 2048,
        )
        self._emit_result: dict | None = None

    # ── run helper ───────────────────────────────────────────────────────────

    def generate(
        self,
        kpi_id: str,
        kpi_name: str,
        kpi_description: str,
        domain: str,
        l1_value: Any,
        l1_unit: str,
        l1_format: str,
        l1_view_name: str,
        l1_field_name: str,
        has_formula: bool,
        formula: str | None,
        formula_parameters: list[str],
        available_dimensions: list[str],
        objective: str,
        persona_role: str,
        # Real domain agent findings (used to write grounded insight/risk)
        trend_direction: str | None = None,
        trend_pct: float | None = None,
        trend_description: str | None = None,
        anomaly: str | None = None,
        raw_data_sample: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Run the chart generation loop.

        Returns the chart spec dict (same shape as emit_chart_spec input).
        """
        user_msg = json.dumps({
            "kpi_id":          kpi_id,
            "kpi_name":        kpi_name,
            "kpi_description": kpi_description,
            "domain":          domain,
            "current_value":   l1_value,
            "unit":            l1_unit,
            "format":          l1_format,
            "view_name":       l1_view_name,
            "field_name":      l1_field_name,
            "has_formula":     has_formula,
            "formula":         formula,
            "formula_parameters": formula_parameters,
            "available_dimensions": available_dimensions,
            "business_objective":   objective,
            "persona_role":         persona_role,
            # Real findings from domain agent — base insight/risk on these, not guesses
            "domain_agent_findings": {
                "trend_direction":   trend_direction,
                "trend_pct":         trend_pct,
                "trend_description": trend_description,
                "anomaly":           anomaly,
                "raw_data_sample":   raw_data_sample or [],
            },
            "task": (
                f"Select the MOST EXPRESSIVE chart type for '{kpi_name}'. "
                f"Do not default to bar_chart or line_chart — read the selection rules carefully "
                f"and consider gauge_chart, waterfall_chart, pie_chart, scatter_chart, "
                f"horizontal_bar_chart, area_chart, and map_chart before settling on a choice. "
                f"Base 'key_insight' and 'risk' on the domain_agent_findings above — "
                f"these contain REAL data from Tableau. Do not fabricate. "
                f"This KPI belongs to the '{domain}' domain. "
                f"The persona is '{persona_role}' and the business objective is: {objective}"
            ),
        }, indent=2)

        outcome = self.run(user_msg)
        result  = outcome.get("emit")

        if result is None:
            log.warning("ChartAgent did not call emit_chart_spec for kpi %s", kpi_id)
            # Fallback: kpi_card
            return {
                "kpi_id":      kpi_id,
                "chart_type":  "kpi_card",
                "explanation_what":        kpi_description,
                "explanation_why_matters": f"Core metric for {objective}",
            }

        return result if isinstance(result, dict) else {"kpi_id": kpi_id, "chart_type": "kpi_card"}

    # ── tool execution ────────────────────────────────────────────────────────

    def _execute_tool(self, name: str, tool_input: dict[str, Any]) -> Any:
        if name == "emit_chart_spec":
            self._emit_result = tool_input
            return {"status": "ok", "kpi_id": tool_input.get("kpi_id")}
        raise ToolError(f"Unknown tool: {name}")
