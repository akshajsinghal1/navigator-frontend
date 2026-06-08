"""
agents/summary_agent.py
────────────────────────
Summary Card Agent

Generates per-persona dashboard content from live KPI data:
  - Summary cards  — one per distinct KPI theme (no hard cap)
  - Action items   — one per KPI, concrete and role-specific
  - KPI drivers    — 2-4 bullet-point drivers per KPI

Called AFTER all KPIs are assembled AND on every data refresh so all
three outputs stay in sync with current values.
Nothing is fabricated — every item must be grounded in real numbers.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agents.base import BaseAgent, ToolError

log = logging.getLogger(__name__)

_MODEL = "gemini-3.1-pro-preview"

_SYSTEM_PROMPT = """\
You are an executive briefing writer for a business intelligence platform.

Your job: given a set of KPIs with real values, trends, and anomalies for a
specific business persona, write concise summary cards that will appear at the
top of that persona's dashboard.

Rules
─────
- Write ONE card per distinct theme in the KPI data — do not merge unrelated KPIs
  into one card. If there are 8 KPIs covering staffing, occupancy, cost, and
  referrals, write 4 cards (one per theme).
- GROUND every sentence in the actual data provided — real numbers, real trends.
  Never write vague sentences like "performance is improving" without a number.
- Good: "Total Sales reached $2.3M this period, up 12% vs prior, with the West
  region contributing 40% of that growth."
- Bad: "Sales performance is strong and showing positive trends."
- Each card body is 2-3 sentences maximum. Be crisp.
- signal must match the actual data: "positive" only if the data genuinely shows
  good news, "warning" only if there is a real risk or underperformance, "neutral"
  for context or mixed signals.
- Write for the persona's role — a CFO cares about margins and cash; an Ops Manager
  cares about throughput and delays; a Sales Manager cares about pipeline and quota.
- Do not repeat the title in the body.
- Minimum 2 cards, no hard maximum — cover all meaningful themes.

Action Items rules
──────────────────
- For each KPI with risk=true OR trend_direction="down" with a large magnitude: write 1 concrete action.
- For KPIs that are healthy/stable: write 1 status-confirmation action ("No action needed — X is stable").
- Each action must be a single, direct, role-specific sentence. No vague language.
  Good: "Contact on-call pool for ICU — Thursday and Friday shifts are highest risk."
  Bad: "Consider reviewing staffing levels."
- signal rules:
    "critical" → risk=true AND trend is negative AND trend_pct < -10
    "watch"    → risk=true OR trend is negative
    "stable"   → everything else

KPI Drivers rules
─────────────────
- For EVERY KPI, write 2–4 bullet-point drivers explaining what is causing the current value/trend.
- Each bullet must be a short, specific, data-grounded phrase. No full sentences — fragments are fine.
  Good: "ICU gap widened 42.9% week-over-week"
  Good: "North General contributing 38% of total shortfall"
  Good: "Thursday–Friday shifts highest risk window"
  Bad:  "The staffing situation has worsened due to various factors"
- For stable/healthy KPIs: write drivers that explain WHY it's stable.
  Good: "Productivity held steady at 90.7% across all 5 facilities"
- kpi_name must exactly match the name from the kpis input.

Call emit_summary_cards exactly once with cards, action_items, and kpi_drivers.
"""

_TOOLS: list[dict] = [
    {
        "name": "emit_summary_cards",
        "description": "Emit all summary cards, action items, and KPI drivers for this persona's dashboard.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cards": {
                    "type": "array",
                    "description": "One card per distinct KPI theme — minimum 2, no hard cap",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title":  {"type": "string", "description": "Short card title (3-6 words)"},
                            "body":   {"type": "string", "description": "2-3 sentences grounded in real data"},
                            "signal": {
                                "type": "string",
                                "enum": ["positive", "warning", "neutral"],
                            },
                        },
                        "required": ["title", "body", "signal"],
                    },
                    "minItems": 2,
                },
                "action_items": {
                    "type": "array",
                    "description": "One action item per KPI — concrete, role-specific, grounded in data",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kpi_name": {"type": "string", "description": "Name of the KPI this action relates to"},
                            "action":   {"type": "string", "description": "Single concrete action sentence"},
                            "signal": {
                                "type": "string",
                                "enum": ["critical", "watch", "stable"],
                            },
                        },
                        "required": ["kpi_name", "action", "signal"],
                    },
                    "minItems": 1,
                },
                "kpi_drivers": {
                    "type": "array",
                    "description": "2-4 bullet-point drivers per KPI explaining what is causing the current value/trend",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kpi_name": {"type": "string", "description": "Exact KPI name from the input"},
                            "drivers":  {
                                "type": "array",
                                "description": "2-4 short data-grounded driver phrases",
                                "items": {"type": "string"},
                                "minItems": 2,
                                "maxItems": 4,
                            },
                        },
                        "required": ["kpi_name", "drivers"],
                    },
                    "minItems": 1,
                },
            },
            "required": ["cards", "action_items", "kpi_drivers"],
        },
    }
]


class SummaryAgent(BaseAgent):
    """Generates summary cards, action items, and KPI drivers for one persona."""

    def __init__(self) -> None:
        super().__init__(
            model          = _MODEL,
            tools          = _TOOLS,
            system_prompt  = _SYSTEM_PROMPT,
            max_iterations = 4,
            max_tokens     = 6000,   # cards + action_items + kpi_drivers for 8+ KPIs needs headroom
        )

    def generate(
        self,
        persona_role:       str,
        focus_areas:        list[str],
        business_objective: str,
        kpis:               list[dict[str, Any]],
    ) -> dict[str, list]:
        """
        Returns a dict with:
          "cards":        one card per KPI theme
          "action_items": one action item per KPI
          "kpi_drivers":  2-4 driver bullets per KPI
        Each card:   {"title": str, "body": str, "signal": "positive"|"warning"|"neutral"}
        Each action: {"kpi_name": str, "action": str, "signal": "critical"|"watch"|"stable"}
        Each driver: {"kpi_name": str, "drivers": [str, ...]}
        """
        user_msg = json.dumps({
            "persona_role":       persona_role,
            "focus_areas":        focus_areas,
            "business_objective": business_objective,
            "kpis":               kpis,
            "task": (
                f"For the '{persona_role}' dashboard: "
                f"(1) write one summary card per distinct KPI theme, "
                f"(2) write one action item per KPI, "
                f"(3) write 2-4 driver bullets per KPI explaining what is causing the current value/trend. "
                f"Every item must reference specific numbers from the kpis data. "
                f"Call emit_summary_cards once with cards, action_items, and kpi_drivers."
            ),
        }, indent=2)

        outcome = self.run(user_msg)
        result  = outcome.get("emit")   # {"cards": [...], "action_items": [...], "kpi_drivers": [...]}

        if not isinstance(result, dict):
            log.warning("SummaryAgent did not call emit_summary_cards for '%s'", persona_role)
            return {"cards": [], "action_items": [], "kpi_drivers": []}

        return {
            "cards":        result.get("cards", []),
            "action_items": result.get("action_items", []),
            "kpi_drivers":  result.get("kpi_drivers", []),
        }

    def _execute_tool(self, name: str, tool_input: dict[str, Any]) -> Any:
        if name == "emit_summary_cards":
            n_cards   = len(tool_input.get("cards", []))
            n_actions = len(tool_input.get("action_items", []))
            n_drivers = len(tool_input.get("kpi_drivers", []))
            return {"status": "ok", "cards": n_cards, "action_items": n_actions, "kpi_drivers": n_drivers}
        raise ToolError(f"Unknown tool: {name}")
