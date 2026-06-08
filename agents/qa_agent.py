"""
agents/qa_agent.py
──────────────────
Post-pipeline Quality Assurance Agent

Runs AFTER the orchestrator assembles the Intelligence Config.
Reviews what was generated vs what's available, finds gaps, and
generates supplementary KPIs for anything important that was missed.

What it checks:
  1. Unused views — are there views with rich data that no KPI touched?
  2. Unused dimensions — are there categorical fields no KPI uses as breakdown?
  3. Missing breakdowns — does every KPI have the best possible breakdown?
  4. Financial/critical fields — are important measures surfaced somewhere?
  5. Time series gaps — are trends available but only snapshots were used?

It uses the same run_analysis tool as the domain agent to verify values
before proposing new KPIs — no assumptions.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agents.base import BaseAgent, ToolError
from schemas.config import IntelligenceConfig, KPI, L1Data, ChartSpec, Explanation
from tableau.connector import TableauConnector
from tableau.view_data import summarise_rows

log = logging.getLogger(__name__)

_MODEL = "gemini-3.1-pro-preview"

_SYSTEM_PROMPT = """\
You are a Quality Assurance agent for Navigator — a business intelligence platform.

Your role: review a generated Intelligence Config and find WHAT WAS MISSED.
You receive:
  - The assembled config (all personas and their KPIs)
  - The EDA analysis (all available views, dimensions, measures)
  - What views were actually used

Your job is to identify the most important gaps and generate NEW KPIs
to fill them. You have access to:
  - fetch_view_data: fetch actual data from any available view
  - run_analysis: run pandas on fetched data to verify values
  - emit_qa_result: submit your supplementary KPIs

IMPORTANT — efficiency rules:
  • All view data is ALREADY LOADED. You may call run_analysis directly on any
    view without fetch_view_data first. Numeric columns (incl. '%' values) are
    already coerced to numbers — do NOT strip '%' or call astype; just aggregate.
  • Run AT MOST 3-4 run_analysis calls total, then STOP and emit. Do not keep
    exploring view after view — pick the few highest-value gaps and move on.
  • You MUST call emit_qa_result before finishing — even if you find zero gaps
    (return an empty supplementary_kpis list). Never end without emitting.
  • If you have run 4 analyses, your NEXT action must be emit_qa_result.

Gap detection checklist:
  1. UNUSED VIEWS: views in available_views not used by any KPI
     → Could these views produce valuable KPIs?
     → Fetch and explore them with run_analysis

  2. UNUSED DIMENSIONS: dimension fields in all_dimensions not used as
     x_axis or breakdown_by in any existing KPI
     → Run: df['DimensionField'].nunique() on a relevant view
     → If 2-20 distinct values → strong breakdown candidate
     → Propose a KPI that uses this dimension as breakdown

  3. MISSING BREAKDOWNS: existing KPIs showing aggregate values when
     a meaningful breakdown exists in the data
     → e.g. "Total Revenue = $3.2M" but no "Revenue by Region" KPI

  4. CRITICAL MEASURES NEVER SURFACED: important numeric fields that
     appear in no KPI
     → Run: df['MeasureField'].describe() to verify it has real values
     → If sum/mean is non-trivial → propose a KPI

  5. SNAPSHOT vs TREND: KPIs showing point-in-time when a trend view exists
     → If "Occupancy 74.4%" exists but no "Occupancy Trend" KPI

For each gap found:
  - Fetch the relevant view
  - Run run_analysis to verify the data has real values
  - If confirmed → include in supplementary KPIs

Persona assignment for new KPIs:
  - Financial measures → assign to the most senior executive persona
  - Operational breakdowns → assign to the manager persona
  - Trend/forecast data → assign to whoever owns the base KPI

Output format: emit_qa_result with a list of supplementary KPIs.
Each must have a clear view_name, field_name, and verified l1_value.

Quality bar: only emit KPIs you have VERIFIED with run_analysis.
Do not propose KPIs you haven't checked against real data.
"""

_QA_TOOLS = [
    {
        "name": "fetch_view_data",
        "description": "Fetch data from a Tableau view to explore what's available.",
        "input_schema": {
            "type": "object",
            "properties": {
                "view_name": {"type": "string", "description": "View name from available_views"},
            },
            "required": ["view_name"],
        },
    },
    {
        "name": "run_analysis",
        "description": (
            "Run a pandas expression on fetched view data to verify values before proposing KPIs. "
            "Examples: df['Field'].nunique(), df.groupby('Dept')['Value'].mean(), df.describe()"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "view_name": {"type": "string"},
                "expression": {"type": "string"},
            },
            "required": ["view_name", "expression"],
        },
    },
    {
        "name": "emit_qa_result",
        "description": "Submit supplementary KPIs found during QA review.",
        "input_schema": {
            "type": "object",
            "properties": {
                "supplementary_kpis": {
                    "type": "array",
                    "description": "New KPIs to add — each verified with run_analysis",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id":              {"type": "string"},
                            "name":            {"type": "string"},
                            "description":     {"type": "string"},
                            "target_persona":  {"type": "string", "description": "Which persona role should own this KPI"},
                            "l1_view_name":    {"type": "string"},
                            "l1_field_name":   {"type": "string"},
                            "l1_value":        {"type": "number"},
                            "l1_unit":         {"type": "string"},
                            "chart_type":      {"type": "string"},
                            "x_axis":          {"type": "string"},
                            "x_axis_type":     {"type": "string"},
                            "aggregation":     {"type": "string", "enum": ["sum", "avg", "count", "min", "max"]},
                            "gap_filled":      {"type": "string", "description": "What gap this KPI fills"},
                        },
                        "required": ["id", "name", "description", "target_persona",
                                     "l1_view_name", "l1_field_name", "l1_value", "gap_filled"],
                    },
                },
                "gaps_found": {
                    "type": "array",
                    "description": "Gaps identified (including ones we couldn't fill — for logging)",
                    "items": {"type": "string"},
                },
            },
            "required": ["supplementary_kpis", "gaps_found"],
        },
    },
]


class QAAgent(BaseAgent):
    """
    Post-pipeline QA agent.

    Args:
        connector    : authenticated Tableau connector
        workbook_luid: workbook luid
    """

    def __init__(self, connector: TableauConnector, workbook_luid: str,
                 view_cache: dict[str, list[dict]] | None = None) -> None:
        super().__init__(
            model          = _MODEL,
            tools          = _QA_TOOLS,
            system_prompt  = _SYSTEM_PROMPT,
            max_iterations = 10,   # QA is a quick review, not deep exploration
            max_tokens     = 8192,
        )
        self._connector      = connector
        self._workbook_luid  = workbook_luid
        # Seed with profiler-fetched data — QA can run_analysis immediately, no re-fetch
        self._row_cache: dict[str, list[dict]] = dict(view_cache) if view_cache else {}
        self._emit_result: dict | None = None

    def review(
        self,
        config: IntelligenceConfig,
        eda: dict[str, Any],
        available_views: list[str],
        profiler_flags: list[dict] | None = None,   # quality flags from WorkbookProfile
    ) -> dict[str, Any]:
        """
        Review the assembled config and find gaps.

        Returns a dict with supplementary_kpis and gaps_found.
        """
        # Build a compact summary of what was generated
        used_views: set[str] = set()
        used_dimensions: set[str] = set()
        existing_kpi_names: list[str] = []

        for pv in config.personas:
            for sec in pv.dashboard_sections:
                for kpi in sec.kpis:
                    if kpi.l1 and kpi.l1.view_name:
                        used_views.add(kpi.l1.view_name)
                    if kpi.chart:
                        if kpi.chart.x_axis:
                            used_dimensions.add(kpi.chart.x_axis)
                        if kpi.chart.breakdown_by:
                            used_dimensions.add(kpi.chart.breakdown_by)
                    existing_kpi_names.append(kpi.name)

        unused_views = [v for v in available_views if v not in used_views]
        all_dims     = [d["name"] for d in eda.get("all_dimensions", [])]
        all_measures = [m["name"] for m in eda.get("all_measures", [])]
        unused_dims  = [d for d in all_dims if d not in used_dimensions]

        # Build persona summary for assignment
        personas_summary = [
            {
                "role":  pv.persona.role,
                "level": pv.persona.persona_level,
                "kpis":  [k.name for sec in pv.dashboard_sections for k in sec.kpis],
            }
            for pv in config.personas
        ]

        # ── Distil profiler flags into actionable QA context ──────────────────
        # Group flags by type so QA can validate coverage against known issues
        # rather than re-discovering them with run_analysis.
        flag_summary: dict[str, list[str]] = {}
        for f in (profiler_flags or []):
            flag_summary.setdefault(f.get("code", "unknown"), []).append(f.get("where", ""))

        # Build KPI → view mapping so QA can cross-reference flags with coverage
        kpi_view_map = {
            k.name: k.l1.view_name
            for pv in config.personas
            for sec in pv.dashboard_sections
            for k in sec.kpis
            if k.l1 and k.l1.view_name
        }

        user_msg = json.dumps({
            "task": (
                "Review the generated Intelligence Config against the profiler_flags. "
                "The flags are VERIFIED FACTS about the data — use them as your validation source. "
                "For each flag type, check whether the config already handles it. "
                "If a degenerate_breakdown flag exists for a view, check whether that breakdown "
                "appears in any KPI for that view — if yes, that is a gap. "
                "If a suspicious_uniform flag exists, check whether any KPI headlines 'top segment'. "
                "You may use run_analysis (at most 3-4 calls) to verify specific values. "
                "Call emit_qa_result when done."
            ),
            "profiler_flags": flag_summary,          # ← verified facts, not guesses
            "kpi_view_map":   kpi_view_map,           # ← which KPIs use which views
            "existing_kpis":  existing_kpi_names,
            "personas":       personas_summary,
            "unused_views":   unused_views[:15],
            "all_views_used": list(used_views),
            "available_views": available_views[:30],
        }, indent=2)

        outcome = self.run(user_msg)
        result  = outcome.get("emit")

        if result is None:
            log.warning("QAAgent did not call emit_qa_result")
            return {"supplementary_kpis": [], "gaps_found": []}

        return result if isinstance(result, dict) else {"supplementary_kpis": [], "gaps_found": []}

    # ── Tool execution ────────────────────────────────────────────────────────

    def _execute_tool(self, name: str, tool_input: dict[str, Any]) -> Any:
        if name == "fetch_view_data":
            return self._tool_fetch_view_data(tool_input)
        if name == "run_analysis":
            return self._tool_run_analysis(tool_input)
        if name == "emit_qa_result":
            self._emit_result = tool_input
            n_kpis = len(tool_input.get("supplementary_kpis", []))
            n_gaps = len(tool_input.get("gaps_found", []))
            return {"status": "ok", "supplementary_kpis": n_kpis, "gaps_found": n_gaps}
        raise ToolError(f"Unknown tool: {name}")

    def _tool_fetch_view_data(self, inp: dict) -> dict:
        view_name = inp["view_name"]
        # Reuse profiler-seeded data when present (includes Hyper [TABLE] entries)
        rows = self._row_cache.get(view_name)
        if rows is None:
            if view_name.startswith("[TABLE]"):
                # [TABLE] views are Hyper tables — only in cache, not in Tableau
                raise ToolError(
                    f"[TABLE] view '{view_name}' is a Hyper table — data is only available "
                    f"if it was loaded during the pipeline run. It may not be in the current cache."
                )
            try:
                rows = self._connector.get_view_data_by_name(
                    workbook_luid = self._workbook_luid,
                    view_name     = view_name,
                    max_rows      = 0,
                )
            except Exception as exc:
                raise ToolError(f"Failed to fetch {view_name!r}: {exc}") from exc
            self._row_cache[view_name] = rows
        sample_limit = len(rows) if len(rows) <= 500 else 200
        return summarise_rows(rows, max_rows=sample_limit)

    def _tool_run_analysis(self, inp: dict) -> dict:
        from agents.analysis_sandbox import run_pandas_analysis, format_result

        view_name  = inp["view_name"]
        expression = inp["expression"].strip()
        rows       = self._row_cache.get(view_name)

        # Data is pre-loaded from the profiler; fetch lazily if somehow missing
        if rows is None:
            if view_name.startswith("[TABLE]"):
                raise ToolError(f"[TABLE] view '{view_name}' not in cache — call fetch_view_data first")
            try:
                rows = self._connector.get_view_data_by_name(
                    workbook_luid=self._workbook_luid, view_name=view_name, max_rows=0,
                )
                self._row_cache[view_name] = rows
            except Exception as exc:
                raise ToolError(f"No data for {view_name!r}: {exc}") from exc

        try:
            value = run_pandas_analysis(expression, rows)
            return {"view": view_name, "expression": expression,
                    "result": format_result(value), "rows_used": len(rows)}
        except Exception as exc:
            cols = list(rows[0].keys()) if rows else []
            raise ToolError(
                f"Analysis failed: {exc}. Columns: {cols}. "
                f"Numeric columns are already coerced — don't strip '%' or astype."
            ) from exc
