"""
agents/summary_agent.py
────────────────────────
Summary Card Agent

Writes exactly 3 AI summary cards for a persona's dashboard.
Called AFTER all KPIs are fully assembled so it receives real numbers:
  - actual L1 values from Tableau
  - trend direction and % change
  - anomalies flagged by the domain agent
  - key insights from the chart agent

Nothing is fabricated — every sentence in a card must be grounded in
the structured KPI data passed to it.
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
specific business persona, write exactly 3 concise summary cards that will
appear at the top of that persona's dashboard.

Rules
─────
- GROUND every sentence in the actual data provided — real numbers, real trends.
  Never write vague sentences like "performance is improving" without a number.
- Good: "Total Sales reached $2.3M this period, up 12% vs prior, with the West
  region contributing 40% of that growth."
- Bad: "Sales performance is strong and showing positive trends."
- Each card body is 2-3 sentences maximum. Be crisp.
- signal must match the actual data: "positive" only if the data genuinely shows
  good news, "warning" only if there is a real risk or underperformance, "neutral"
  for context or mixed signals.
- Cover different angles across the 3 cards. Suggested structure:
    Card 1: Overall state — the headline number(s) for this persona.
    Card 2: The most important risk or underperforming metric (if any).
            If everything looks healthy, use the most critical secondary insight.
    Card 3: An opportunity, a trend to leverage, or the most actionable finding.
- Write for the persona's role — a CFO cares about margins and cash; an Ops Manager
  cares about throughput and delays; a Sales Manager cares about pipeline and quota.
- Do not repeat the title in the body.

Call emit_summary_cards exactly once with an array of exactly 3 cards.
"""

_TOOLS: list[dict] = [
    {
        "name": "emit_summary_cards",
        "description": "Emit the 3 summary cards for this persona's dashboard.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cards": {
                    "type": "array",
                    "description": "Exactly 3 summary cards",
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
                    "minItems": 3,
                    "maxItems": 3,
                }
            },
            "required": ["cards"],
        },
    }
]


class SummaryAgent(BaseAgent):
    """Writes 3 grounded summary cards for one persona."""

    def __init__(self) -> None:
        super().__init__(
            model          = _MODEL,
            tools          = _TOOLS,
            system_prompt  = _SYSTEM_PROMPT,
            max_iterations = 4,
            max_tokens     = 2048,
        )

    def generate(
        self,
        persona_role:       str,
        focus_areas:        list[str],
        business_objective: str,
        kpis:               list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Returns a list of exactly 3 summary card dicts.
        Each dict: {"title": str, "body": str, "signal": "positive"|"warning"|"neutral"}
        """
        user_msg = json.dumps({
            "persona_role":       persona_role,
            "focus_areas":        focus_areas,
            "business_objective": business_objective,
            "kpis":               kpis,
            "task": (
                f"Write exactly 3 summary cards for the '{persona_role}' dashboard. "
                f"Every sentence must reference specific numbers from the kpis data above. "
                f"Call emit_summary_cards once."
            ),
        }, indent=2)

        outcome = self.run(user_msg)
        result  = outcome.get("emit")   # {"cards": [...]}

        if not isinstance(result, dict):
            log.warning("SummaryAgent did not call emit_summary_cards for '%s'", persona_role)
            return []

        return result.get("cards", [])[:3]

    def _execute_tool(self, name: str, tool_input: dict[str, Any]) -> Any:
        if name == "emit_summary_cards":
            count = len(tool_input.get("cards", []))
            return {"status": "ok", "count": count}
        raise ToolError(f"Unknown tool: {name}")
