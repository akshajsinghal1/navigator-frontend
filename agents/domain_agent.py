"""
agents/domain_agent.py
──────────────────────
Domain Analysis Sub-agent (gemini-3.1-pro-preview)

Responsibilities:
  • Receives KPI designs from the orchestrator — specific KPIs Navigator
    has decided this business should be tracking
  • Finds the right Tableau views/fields to compute each designed KPI
  • Fetches actual data, measures current values, trends, and anomalies
  • Emits a structured domain result via emit_domain_result

One instance per domain — runs in parallel with sibling domain agents.
Spawned by the orchestrator's analyze_domain tool implementation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agents.base import BaseAgent, ToolError
from schemas.tools import DOMAIN_TOOLS
from tableau.connector import TableauConnector
from tableau.view_data import summarise_rows, detect_trend, detect_anomalies

log = logging.getLogger(__name__)

_MODEL = "gemini-3.1-pro-preview"

_SYSTEM_PROMPT = """\
You are a domain data agent for Navigator — a business intelligence platform.

Your mission
────────────
The orchestrator has DESIGNED the KPIs. Your job is to COMPUTE them faithfully
from the actual view data, following the computation_hint EXACTLY.

You now have a POWERFUL tool: run_analysis — use it when you need to explore data.
After fetching a view with fetch_view_data, you can optionally run pandas
expressions to verify values before computing KPIs.

IMPORTANT: run_analysis is optional — always call fetch_view_data FIRST,
then run_analysis if you need to explore further. You can compute KPIs
directly from the sample + numeric_summary if they're clear enough.

When run_analysis IS useful:
  • Verifying a filtered count: df[df['Status']=='Yes']['Count'].sum()
  • Finding a breakdown: df.groupby('Department')['Gap'].mean()
  • Checking a conversion rate: df['Converted'].sum() / df['Total'].sum()
  • Discovering what values a field has: df['Status'].value_counts()

Always call emit_domain_result at the end — even if some analyses failed.
Partial results are better than no results.

The KPIs may be RATIOS, FILTERED COUNTS, COMPOSITES — not just "sum of one column."
Read the computation_hint carefully and execute it precisely.

For each KPI design you receive:
  1. Read its name, description, and computation_hint carefully.
     • If the hint says "ratio of A to B" → compute SUM(A) / SUM(B), not sum of either alone.
     • If the hint says "where Status='Accepted' / total" → filter rows first, then divide.
     • If the hint says "average per day" → group by date, then average.
     • Whatever the hint specifies — do EXACTLY that. Don't simplify.
  2. Use fetch_view_data to pull rows from the relevant Tableau views.
  3. Scan the columns — verify the fields the orchestrator named are present.
     If a named field is missing, try another view — but don't silently swap to
     a different field with a similar name.
  4. Compute the value following computation_hint.
  5. Detect trend by comparing earlier rows vs later rows (% change).
  6. If the primary view doesn't have all needed fields, try another relevant view.
  7. If no view has the right data, emit the KPI with l1_value=null and a note
     in the description — never silently substitute a different metric.

View access — STRICT
─────────────────────
You can ONLY call fetch_view_data with view names that appear VERBATIM in `relevant_views`.
- NEVER invent, guess, or try variations of a view name.
- If a fetch fails, try the NEXT view from `relevant_views` — never make up a new name.
- Every name you pass to fetch_view_data must be copied exactly from `relevant_views`.

Getting chart data (raw_data)
──────────────────────────────
Many dashboards have both a KPI view (1 row — the current number) and a Trend view
(many rows — time series for charting). Use both:
  1. Fetch the KPI/aggregate view → compute l1_value from it.
  2. Look in relevant_views for a view whose name contains "Trend", "History",
     "Over Time", "by Month", "by Week", "Forecast". If found, fetch it and use
     THOSE rows as raw_data — they give the chart its shape.
  3. If no trend view exists, use the rows you have (even 1 row is fine for kpi_card).
raw_data should always come from the richest available view — prefer time-series.

CONFIDENCE / PREDICTION INTERVALS:
If a view has columns like Upper Confidence, Lower Confidence, Upper Gap Confidence,
Lower Gap Confidence, p10, p90 — these are prediction bands that the frontend will
render automatically as whisker bars on line charts.
ALWAYS prefer the TREND view (with time-series + confidence columns) over a
KPI snapshot view for any predictive or forecast KPI — it shows the full story.

Computing from sample rows — your primary tool
──────────────────────────────────────────────
fetch_view_data returns the full row sample — for small views (≤500 rows) you
receive EVERY row. Use the sample rows directly to compute any metric correctly.

The `numeric_summary` shows totals across ALL rows with NO filtering. Do NOT
use numeric_summary for KPIs that require filtering — it will give you the wrong
total. Instead, filter the sample rows yourself.

For any filtered/conditional KPI:
  1. Read the rows in `sample`
  2. Identify the filter column and qualifying values by inspecting the data
  3. Sum or count only the qualifying rows
  4. Divide by the total if it's a rate

Example — "Conversion Rate" from a view with Referral Status + Referral Count:
  → Look at what status values exist: "Approved", "Completed", "Cancelled", ...
  → "Conversion" = referrals that succeeded → Approved + Completed
  → qualifying_sum = sum(row["Referral Count"] for row in sample
                        if row["Referral Status"] in ["Approved","Completed"])
  → total = numeric_summary["Referral Count"]["sum"]
  → l1_value = qualifying_sum / total * 100    ← CORRECT (e.g. 40%)
  → NOT: l1_value = numeric_summary["Referral Count"]["sum"]  ← WRONG (gives 79,227%)

The same logic works for ANY workbook — you read the data, understand what
values qualify for the metric, and compute from those rows. No hardcoding needed.

The fetch_view_data result also includes `categorical_breakdown` which pre-groups
the numeric sums by each categorical column's values — this is a convenience but
the sample rows are the authoritative source.

Self-check before emitting every KPI:
  - % KPI: is the value between 0 and 100? If not, you filtered wrong — recompute.
  - Volume KPI: is it different from the total count? If same, you forgot to filter.
  - Does the value make business sense for this metric name?

Sub-segments and drivers — ALWAYS set these
────────────────────────────────────────────
For EVERY KPI, use the categorical_breakdown to identify:

1. critical_segments — which specific sub-segments are worst?
   Look at the categorical_breakdown for each categorical column.
   Find the 2-3 values with the worst numeric aggregates.
   Example: if Profit Ratio by Region shows West=-5%, Central=2%, South=8%:
     → critical_segments = ["West region (Profit Ratio: -5%)"]
   Example: if Ship Status shows "Shipped Late" = 26% of orders:
     → critical_segments = ["26% of orders shipped late"]
   Set to null only if no categorical breakdown exists.

2. key_drivers — what is causing this KPI to be in warning territory?
   Only set for KPIs that are declining, negative, or concerning.
   Look at the data and explain WHY:
     - What dimension/category is pulling the metric down?
     - Is there a recent trend change visible in the time series?
     - Is there a specific sub-segment that stands out?
   Example: "West region accounts for 45% of total profit shortfall"
   Example: "Late shipments concentrated in Standard Class (32% vs First Class 8%)"
   Example: "Q4 revenue down 18% vs Q3, driven by Furniture category decline"
   Set to null if the KPI is performing well.

These fields let executives know EXACTLY which area needs attention,
not just that a metric is red — that is the core value of Navigator.

When data is genuinely missing
────────────────────────────────
If the required field or status value is not present in ANY view from relevant_views:
  - Set l1_value = null  (NEVER use 0 as a placeholder — 0 is a real measured value)
  - Set raw_data = []
  - Explain what was missing in the description
The orchestrator will cleanly drop null-value KPIs rather than showing 0% or $0.

Rules
─────
- Emit EVERY KPI the orchestrator designed — do not drop any.
- Keep the KPI id as snake_case of its name (e.g. "revenue_per_order").
- l1_value must be a single number (the aggregate), not an array or string.
- raw_data: include the rows you fetched — the frontend renders charts from them.
- ALWAYS set l1_field_name to the column name that contains the NUMERIC value
  you aggregated. If you computed a COUNT (e.g. count of reps exceeding quota),
  set l1_field_name to the column you counted (e.g. "Sales Person") BUT also set
  the aggregation to "count" so the frontend knows to COUNT rows, not SUM strings.
  For ratio KPIs (e.g. Late Shipment Rate = late/total*100), set l1_field_name to
  the STATUS column and note the computation method clearly.
  NEVER set l1_field_name to a column that contains only text/names if you
  computed a ratio or percentage from it — that will make live computation fail.
- The relevant_fields you receive are REAL CSV column names (e.g. "Tourism_Inbound"
  not "Tourism Inbound"). Use these EXACT names when looking for columns in fetched
  rows and when setting l1_field_name, x_axis, y_axis, breakdown_by.

Value parsing — CRITICAL
────────────────────────
Tableau returns values as FORMATTED STRINGS. Parse before computing:
  "$6,928"    → 6928.0       "€1,234"    → 1234.0
  "$1.2B"     → 1200000000   "$2.3M"     → 2300000
  "45.7K"     → 45700        "1.5T"      → 1500000000000
  "19.5%"     → 19.5         "($500)"    → -500.0  (parentheses = negative)
  "1,234.56"  → 1234.56      "N/A", ""   → skip row
Rules:
  1. Strip: $ € £ ¥ ₹ and leading currency symbols first
  2. Strip trailing % (keep numeric value as-is, e.g. 19.5 not 0.195)
  3. Handle scale suffixes B/M/K/T (case-insensitive) before stripping commas
  4. Strip commas (thousands separators)
  5. Parentheses around a value mean negative
  6. If a value still cannot be parsed to a number, skip that row
IMPORTANT: The numeric_summary already uses correct parsed values — use it to
verify your computed aggregates. If your sum differs wildly from the summary,
recheck your parsing.

Unit — ALWAYS set this
──────────────────────
Every KPI must have a unit. Match to the business context:
  "USD"      — any monetary amount regardless of currency (use USD as generic)
  "%"        — ratios, rates, margins, attainment, churn, NPS %, conversion
  "days"     — lead time, cycle time, DSO, DPO, length of stay, any day-based duration
  "hours"    — machine hours, downtime, labor hours
  "score"    — NPS points, satisfaction score, index score (unitless but named)
  ""         — counts (orders, patients, employees, units) and truly unitless values

Aggregation — choose the right one
───────────────────────────────────
When computing l1_value, use the correct aggregation for the KPI type:
  SUM   — totals: revenue, cost, count of transactions, total patients
  AVG   — rates/ratios: margin %, on-time rate, avg length of stay, avg days to ship
  LAST  — current snapshot: headcount, AUM, portfolio value
Never SUM a percentage or rate (summing 5%+3%+2% = 10% is wrong, avg = 3.33%).

Trend detection
───────────────
You NEED time-series data to detect a trend. Time fields can be named:
  "Date", "Month", "Week", "Quarter", "Year", "Period", "Fiscal Year",
  "FY", "Hour", "Day", "Order Date", "Transaction Date", or any column
  whose name contains "date", "time", "month", "quarter", "week", "period".

If the current view has NO time column (geographic, categorical, snapshot data):
  → Fetch a DIFFERENT view that has time-ordered data before giving up.
  → Set trend to null only after genuinely trying at least 2 views.

How to compute trend:
  - Group rows by time period and aggregate each period.
  - Compare earliest period vs latest period: trend_pct = (latest-earliest)/earliest * 100.
  - trend_direction: "up" (>+2%), "down" (<-2%), "flat" (within ±2%).
  - trend_pct: e.g. 12.3 for +12.3%, -5.1 for -5.1%.

L2 Projection — define for EVERY KPI
─────────────────────────────────────
After computing the L1 value, also set l2_projection so the frontend can show
7-day and 30-day forward projections. Choose the right method:

  "daily_rate"  — for metrics that ACCUMULATE over time:
                  revenue, order count, cost, units produced, hours worked
                  formula: sum(value_field) / date_span_days × horizon_days
                  Requires: value_field (the numeric column), date_field (the date column)
                  Example: monthly Sales → 7D projection = (total_sales / 365) × 7

  "ratio"       — for PERCENTAGE / RATE metrics that stay roughly constant:
                  profit margin, on-time delivery rate, conversion rate, satisfaction %
                  projection = same ratio (doesn't change with time horizon)
                  Requires: value_field (the ratio/% column), aggregation="avg"
                  Example: Profit Margin % → 7D projection = same % as today

  "growth_rate" — for metrics with a STEADY UPWARD OR DOWNWARD TREND:
                  customer count, active users, market share, NPS
                  uses recent compound growth rate to extrapolate forward
                  Requires: value_field, date_field

  "stable"      — for SNAPSHOT metrics that don't meaningfully project forward:
                  average days to ship, current inventory level, average rating
                  projection = current value (no change expected)
                  Requires: value_field, aggregation

Rules — CRITICAL:
- value_field MUST be a column name that ACTUALLY EXISTS in the rows you fetched.
  Look at the column list you received from fetch_view_data (the keys of each row dict).
  Use EXACTLY that name — do NOT write Tableau formula syntax like "SUM([Sales])-SUM([Forecast])".
  If the column in the data is "SUM_Sales_SUM_Forecast", use "SUM_Sales_SUM_Forecast".
  If the column is "Sales", use "Sales". Copy the name verbatim from the column list.
- date_field MUST be a real date/time column from the same view (if applicable).
  Check the actual column list — if you see "Month of Order Date", use that exact string.
- Set l2_projection=null only if the KPI genuinely cannot be projected (e.g. geographic
  snapshot with no time dimension and no rate/stable interpretation).
- DO NOT set to null just because it's hard — every KPI should have a projection.

Payload size — CRITICAL
────────────────────────
When calling emit_domain_result, limit raw_data to AT MOST 20 rows per KPI.
If you have more rows, keep the most recent/representative 20.
Sending more than 20 rows per KPI will cause a generation failure.

Note: you receive many more rows via fetch_view_data (up to all rows for small
views) — use those to compute accurate values. But emit only 20 rows in raw_data.

Call emit_domain_result ONCE when you have processed all KPI designs.
"""


class DomainAgent(BaseAgent):
    """
    Domain analysis sub-agent.

    Args:
        connector   : authenticated Tableau connector
        workbook_luid: luid of the target workbook
    """

    def __init__(
        self,
        connector: TableauConnector,
        workbook_luid: str,
    ) -> None:
        super().__init__(
            model         = _MODEL,
            tools         = DOMAIN_TOOLS,
            system_prompt = _SYSTEM_PROMPT,
            max_iterations = 25,   # increased: run_analysis adds extra turns per view
            max_tokens     = 8192,
        )
        self._connector     = connector
        self._workbook_luid = workbook_luid
        self._emit_result: dict | None = None
        # Cache fetched rows by view name so run_analysis can use them without re-fetching
        self._row_cache: dict[str, list[dict]] = {}

    # ── run helper ───────────────────────────────────────────────────────────

    def analyze(
        self,
        domain_name: str,
        relevant_fields: list[str],
        relevant_views: list[str],
        kpi_designs: list[dict],
    ) -> dict[str, Any]:
        """
        Run the domain analysis loop.

        Args:
            domain_name    : e.g. "Sales Performance"
            relevant_fields: field names from the workbook inventory
            relevant_views : Tableau view names available for this domain
            kpi_designs    : list of {name, description, computation_hint} dicts
                             designed by the orchestrator

        Returns the domain result dict (same shape as emit_domain_result input).
        """
        user_msg = json.dumps({
            "domain":          domain_name,
            "relevant_fields": relevant_fields,
            "relevant_views":  relevant_views,
            "kpi_designs":     kpi_designs,
            "task": (
                f"Fetch data for the '{domain_name}' domain and compute every KPI "
                f"in kpi_designs. For each KPI: find the right view + field, "
                f"aggregate to a single current value, detect trend, attach raw_data. "
                f"Then call emit_domain_result with all KPIs."
            ),
        }, indent=2)

        outcome = self.run(user_msg)
        result  = outcome.get("emit")

        if result is None:
            log.warning("DomainAgent did not call emit_domain_result for domain %s", domain_name)
            return {"domain_name": domain_name, "kpis": []}

        return result if isinstance(result, dict) else {"domain_name": domain_name, "kpis": []}

    # ── tool execution ────────────────────────────────────────────────────────

    def _execute_tool(self, name: str, tool_input: dict[str, Any]) -> Any:
        if name == "fetch_view_data":
            return self._tool_fetch_view_data(tool_input)
        if name == "run_analysis":
            return self._tool_run_analysis(tool_input)
        if name == "emit_domain_result":
            return self._tool_emit_domain_result(tool_input)
        raise ToolError(f"Unknown tool: {name}")

    def _tool_fetch_view_data(self, inp: dict) -> dict:
        view_name = inp["view_name"]
        max_rows  = int(inp.get("max_rows", 0))  # 0 = no limit — fetch all rows for accurate L1/L2

        try:
            rows = self._connector.get_view_data_by_name(
                workbook_luid = self._workbook_luid,
                view_name     = view_name,
                max_rows      = max_rows,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            raise ToolError(f"Failed to fetch data for view {view_name!r}: {exc}") from exc

        # Cache raw rows so run_analysis can use them without re-fetching
        self._row_cache[view_name] = rows

        # Pass all rows to the agent when the view is small (≤500 rows) so it
        # can compute ANY filtered/conditional metric accurately from the sample.
        # For larger views, cap at 200 to keep context manageable.
        sample_limit = len(rows) if len(rows) <= 500 else 200
        return summarise_rows(rows, max_rows=sample_limit)

    def _tool_run_analysis(self, inp: dict) -> dict:
        """
        Run a pandas expression on previously fetched view data.

        The agent uses this to explore data before deciding on KPI values —
        no assumptions, every insight verified against real numbers.

        Args:
            view_name:  must have been fetched with fetch_view_data first
            expression: a single pandas expression (not a statement)
                        e.g. "df.groupby('Status')['Count'].sum()"
                             "df['Turnaround Hours'].mean()"
                             "df[df['Escalation Flag']==True].shape[0]"

        Returns result as string so the agent can read and reason about it.
        """
        import pandas as pd

        view_name  = inp["view_name"]
        expression = inp["expression"].strip()

        rows = self._row_cache.get(view_name)
        if not rows:
            raise ToolError(
                f"No cached data for view {view_name!r}. "
                f"Call fetch_view_data first, then run_analysis."
            )

        df = pd.DataFrame(rows)  # noqa: F841  (used in eval below)

        try:
            # Safe eval: df + pd + common numeric builtins.
            # No I/O, no imports, no exec.
            _safe_builtins = {
                "float": float, "int": int, "str": str, "bool": bool,
                "len": len, "sum": sum, "round": round, "abs": abs,
                "min": min, "max": max, "list": list, "tuple": tuple,
                "dict": dict, "set": set, "range": range,
                "enumerate": enumerate, "zip": zip,
                "True": True, "False": False, "None": None,
                "isinstance": isinstance,
            }
            result = eval(  # noqa: S307
                expression,
                {"__builtins__": _safe_builtins, "pd": pd},
                {"df": df},
            )

            # Convert result to a readable string the LLM can reason about
            if hasattr(result, "to_string"):
                result_str = result.to_string()
            elif hasattr(result, "__len__") and len(result) > 50:
                result_str = str(result)[:2000] + "…"
            else:
                result_str = str(result)

            log.debug("run_analysis(%s): %s → %s", view_name, expression[:80], result_str[:200])
            return {
                "view":       view_name,
                "expression": expression,
                "result":     result_str,
                "rows_used":  len(df),
            }

        except Exception as exc:
            raise ToolError(
                f"Analysis failed for expression {expression!r}: {exc}. "
                f"Available columns: {list(df.columns)}"
            ) from exc

    def _tool_emit_domain_result(self, inp: dict) -> dict:
        # Store for retrieval
        self._emit_result = inp
        return {"status": "ok", "kpis_received": len(inp.get("kpis", []))}
