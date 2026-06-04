"""
agents/orchestrator.py
───────────────────────
Orchestrator Agent (gemini-3.1-pro-preview)

The top-level agent that:
  1. Receives the semantic-filtered inventory
  2. Reasons about the business objective and personas entirely from the data
  3. Identifies as many distinct personas and business domains as the data supports
  4. Spins up domain sub-agents in parallel (via analyze_domain tool)
  5. Spins up chart sub-agents in parallel (via generate_chart_spec tool)
  6. Assembles the final Intelligence Config (one PersonaView per persona)
  7. Calls emit_intelligence_config to output the result

Nothing is hardcoded here — the orchestrator decides everything
from the workbook content alone.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any

from agents.base import BaseAgent, ToolError
from agents.chart_agent import ChartAgent
from agents.domain_agent import DomainAgent
from agents.summary_agent import SummaryAgent
from pipeline.l2_evaluator import evaluate_l2
from schemas.config import (
    ChartSpec,
    DashboardSection,
    Explanation,
    IntelligenceConfig,
    KPI,
    L1Data,
    L2Projection,
    Persona,
    PersonaView,
    SummaryCard,
    WorkbookMeta,
)
from schemas.tools import ORCHESTRATOR_TOOLS
from tableau.connector import TableauConnector

log = logging.getLogger(__name__)

_MODEL = "gemini-3.1-pro-preview"


# ── Persona level inference ────────────────────────────────────────────────────

def _infer_persona_level(role: str) -> str:
    """
    Classify a persona role as executive / manager / analyst.

    Used as fallback when the orchestrator agent omits persona_level.
    Matches on role title keywords (case-insensitive).
    """
    r = role.lower()

    # Executive signals — C-suite, VP, Director, Head of, Chief
    exec_signals = [
        "ceo", "cfo", "coo", "cto", "cmo", "cso", "ciso",
        "chief ", "vp ", "v.p.", "vice president",
        "director", "head of", "president",
        "medical director", "clinical director",
    ]
    if any(s in r for s in exec_signals):
        return "executive"

    # Analyst signals
    analyst_signals = [
        "analyst", "scientist", "engineer", "developer",
        "bi ", "data ", "intelligence ", "reporting",
        "technical", "architect",
    ]
    if any(s in r for s in analyst_signals):
        return "analyst"

    # Default: manager
    return "manager"


# ── unit / format inference helpers ──────────────────────────────────────────

def _infer_unit(kpi_name: str, field_name: str, value) -> str:
    """
    Infer a display unit from KPI name, field name, and value.
    Used as a fallback when the domain agent omits l1_unit.
    Covers retail, SaaS, healthcare, finance, manufacturing, HR, logistics.
    """
    # Pad with spaces so short keywords (los, ote, mrr…) match whole-word only
    combined = " " + (kpi_name + " " + field_name).lower() + " "

    # ── Monetary / currency (any industry) ────────────────────────────────────
    monetary = [
        "sales", "revenue", "profit", "compensation", "commission",
        "cost", "price", "spend", "earning", "income", "gross",
        "forecast", "quota", "ote", "budget", "expense", "overhead",
        # SaaS
        "mrr", "arr", "ltv", "cac", "acv", "tcv", "arpu", "expansion",
        # Finance
        "aum", "nav", "asset", "liability", "equity", "loan", "fund",
        "investment", "portfolio", "capital", "cash", "debt", "receivable",
        "payable", "balance",
        # Logistics
        "freight", "shipping cost", "fulfillment cost",
        # Generic
        "amount", "value", "payment", "transaction total", "billing",
        "invoice", "contract value",
    ]

    # ── Percentage / ratio (any industry) ─────────────────────────────────────
    # Use compound phrases where needed to avoid false positives
    # e.g. "churn rate" → % but "churn revenue" → USD (caught by monetary)
    # e.g. "nps" alone → score/'' not %; "nps rate"/"nps pct" → %
    pct = [
        "ratio", "rate", "margin", "attainment", "percent", "pct", "%",
        "growth %", "growth rate", "churn rate", "churn %",
        "conversion rate", "conversion %",
        "utilization", "efficiency", "oee",
        "yield rate", "uptime", "availability", "fill rate",
        "on-time", "on time", "accuracy", "adherence", "compliance",
        "engagement rate", "open rate", "click-through", "ctr",
        "roas", "return on", "occupancy rate", "occupancy %",
        # Healthcare
        "readmission rate", "mortality rate", "complication rate",
        "infection rate", "readmission %",
        # Manufacturing
        "defect rate", "scrap rate", "rework rate", "quality rate",
        # HR
        "turnover rate", "attrition rate", "absenteeism rate",
        "retention rate",
        # Finance
        "interest rate", "default rate", "yield %",
    ]

    # ── Time-based ─────────────────────────────────────────────────────────────
    time_units = [
        "days to", "lead time", "cycle time", "dso", "dpo", "tat",
        "avg days", "average days", "time to", "turnaround",
        "length of stay", "los", "hold time", "wait time",
        "response time", "resolution time",
    ]

    # ── Hour-based ─────────────────────────────────────────────────────────────
    hour_units = [
        "hours", "downtime hours", "machine hours", "labor hours",
    ]

    # ── Count / unitless ───────────────────────────────────────────────────────
    counts = [
        "count", "number of", "# of", "total orders", "total customers",
        "total patients", "total employees", "headcount", "volume",
        "units sold", "units produced", "transactions",
        "admissions", "visits", "encounters", "tickets", "leads",
        "shipments", "deliveries",
    ]

    def _match(keywords: list[str]) -> bool:
        """Match keyword against combined using word-boundary-safe padding."""
        for k in keywords:
            # Pad single-word short acronyms with spaces to avoid substring hits
            # e.g. "los" must not match inside "lost"
            padded = f" {k} " if " " not in k else k
            if padded in combined:
                return True
        return False

    # Check pct BEFORE monetary: "Profit Margin" → % not USD
    # "ratio/rate/margin/attainment" always wins over "profit/revenue"
    if _match(pct):
        return "%"
    if _match(time_units):
        return "days"
    if _match(hour_units):
        return "hours"
    if _match(monetary):
        return "USD"
    if _match(counts):
        return ""

    # ── Value heuristic — only apply when field name also suggests a ratio ─────
    # Do NOT apply blindly (e.g. MRR=0.5M would wrongly become %)
    ratio_hints = ["ratio", "rate", "pct", "%", "share", "fraction", "proportion"]
    try:
        v = float(value)
        if 0 < v < 1 and any(h in combined for h in ratio_hints):
            return "%"
    except (TypeError, ValueError):
        pass

    return ""


def _infer_format(unit: str) -> str:
    """Map a unit string to an l1_format value for the frontend."""
    if unit == "USD":
        return "currency"
    if unit == "%":
        return "percentage"
    return "number"

_SYSTEM_PROMPT = """\
You are the Navigator Orchestrator — an intelligence design agent that builds
a custom multi-persona business intelligence dashboard from a Tableau workbook.

Your job is INTELLIGENCE DESIGN, not view duplication.

Data access constraints (READ THIS CAREFULLY)
─────────────────────────────────────────────
- You can ONLY pull data via existing Tableau views. View CSV is the API.
- The `reachable_fields` list in your input shows EVERY column real data
  fetches will return. Each entry is tagged with the view it lives on.
- You CANNOT combine fields across views into a single KPI — pick ONE view
  per KPI, and pick columns that exist in THAT view.
- If a metric you want is not powered by any reachable field, skip it.

Anti-pattern — DO NOT DO THIS
─────────────────────────────
The workbook author has already built views like "Sales KPI", "Productivity Ratio KPI",
"Overtime KPI". You can SEE those view names. DO NOT just rename each one and call
it a KPI. That is not intelligence — that is renaming.

Bad example (do not produce this):
  workbook has view "PRODUCTIVITY RATIO KPI"
  → you design KPI "productivity_ratio"     ← LAZY 1:1 rename
  → l1_view_name = "PRODUCTIVITY RATIO KPI"
  → field_name = "Productive Ratio"

What you MUST do instead — design NEW intelligence from the same fields
──────────────────────────────────────────────────────────────────────
The reachable fields are your raw material. The workbook used them ONE way.
Use them in NEW ways to surface NEW insight. Several techniques:

1. REFRAME the aggregation
   Same field, different angle:
   - "Total Referrals" view (existing) → re-aggregate as "Acceptance Rate"
     (count where Status='Accepted' / total count)
   - "Sales" field → "Sales Velocity" (sales per day) or "Sales Concentration"
     (top 5 segments as % of total)

2. SLICE by available dimensions
   Pick a less-obvious dimension on the same view as the breakdown:
   - View has (Region, Category, Sales) → workbook shows by Region;
     you show by Category, or Category WITHIN Region

   ALWAYS look for these high-value dimensions if they exist in the data:
   - Facility / Facility Name / Site → show KPIs per facility, not just aggregate
   - Department / Unit / Team → break KPIs down to operational unit level
   - Region / Geography → geographic breakdown of any metric
   - Shift / Shift Name → operational timing breakdown

   A KPI showing aggregate occupancy across all facilities is far less actionable
   than one showing which SPECIFIC facility is at capacity risk.
   If Facility Name is in the reachable fields, at least ONE KPI per persona
   should use it as the x-axis or breakdown dimension.

3. CONSTRUCT ratios from same-view fields
   - View has (Profit, Sales) → KPI "Margin per Region" = Profit/Sales
   - View has (Returned, Quantity) → KPI "Return Rate" = Returned/Quantity

4. EXTRACT health signals
   - View has time-series → KPI "Volatility Index" = std-dev / mean
   - View has counts by status → KPI "Pipeline Stickiness" = pending / total

5. PERSONA-SPECIFIC FRAMING
   A "Total Sales" KPI for a CFO is a "Revenue Forecast Gap"; for a Sales Manager
   it's "Quota Coverage". Same field, totally different KPI definition + meaning.

Step 1 — UNDERSTAND THE BUSINESS
  - Read field names, formulas, EDA, reachable_fields list
  - Notice: which existing views look LAZY (just one field + L1) vs RICH
    (multiple fields with breakdowns)? Rich views give you raw material.

Step 2 — DESIGN BUSINESS DOMAINS
Different DECISION AREAS, not different view names.
e.g., "Revenue Health" not "Sales Charts".
Design as many domains as the workbook genuinely warrants — don't artificially cap.

Step 3 — DESIGN KPIs PER DOMAIN  (THIS IS WHERE THE CRAFT LIVES)
Design as many KPIs per domain as the data can genuinely support — don't cap arbitrarily.
Richer workbooks should yield more KPIs; simpler ones fewer.
For each KPI:
  a. Pick a view from reachable_fields whose columns can power this KPI.
  b. Pick the EXACT column names you'll use (l1 field, x_axis, y_axis, breakdown).
     CRITICAL: these must be the exact 'name' values from reachable_fields — the
     real CSV column names like "Sales", "Profit_Ratio", "Tourism_Inbound".
     NEVER use inventory display names with spaces (e.g. NOT "Tourism Inbound").
  c. Write what it measures — phrase as a QUESTION the business asks, not as
     a field name. ("Are we shipping to customers fast enough?" not "Days to Ship")
  d. Write why it matters — connect to a decision, not just a chart.
  e. Specify the computation hint — use EXACT 'name' values from reachable_fields
     when referencing columns (e.g. "sum of 'Sales' where 'Region'='West'", not
     "sum of sales where region is West").

ADAPT KPI DEPTH TO PERSONA ROLE — this is not a fixed rule, use your judgment:
The persona's role naturally signals what kind of intelligence they need.
  → A CFO, VP, or Director cares about the headline: is the business healthy?
    Design fewer KPIs (4-6), high business impact, simple framing.
    KPI names should be questions an executive asks in a board meeting.
    Focus on outcomes (revenue, margin, risk) not operational mechanics.
  → A Manager or Operations lead cares about levers they can pull:
    Design more KPIs (6-10), operational coverage, team and process metrics.
    Include breakdowns, efficiency ratios, and performance against targets.
  → An Analyst or technical persona cares about the full picture:
    Design comprehensive KPIs including derived ratios, correlations, distributions.
    More KPIs are fine, include technical metrics and multi-dimensional analysis.

This is about what the DATA and ROLE together suggest — not a rigid formula.
A CFO with rich financial data might still warrant 8 KPIs. An analyst with
limited data might only support 4. Let context drive the judgment.

QUALITY GATE — before emitting, ask yourself for each KPI:
  - Could a non-analyst tell this apart from the workbook's existing view? If "no"
    you've been lazy. Redesign.
  - Would TWO different personas frame this KPI differently? If you can't think
    of how, the KPI is too thin. Add a slice/ratio/framing.
  - Does the KPI's NAME read like a metric or a question? Prefer questions.

Step 4 — IDENTIFY PERSONAS + CLASSIFY LEVEL
Real decision-makers (CFO, Ops Manager, Field Rep, etc.) — not generic labels.
Each persona's dashboard MUST contain a DIFFERENT SET of KPIs — no kpi_id may
appear in more than one persona. Think of it as assigning ownership:

CRITICAL — set persona_level for every persona:
  "executive" → C-suite, VP, Director, Chief X Officer, Medical Director
                They see ONE screen, 4-6 KPIs, big numbers, no jargon.
                Examples: CEO, CFO, COO, "VP of Sales", "Chief Revenue Officer"
  "manager"   → Department head, operations lead, team manager
                They see comprehensive dashboards with all KPIs and breakdowns.
                Examples: "Sales Operations Manager", "Supply Chain Manager"
  "analyst"   → BI analyst, data scientist, power user
                They see everything — all metadata and technical detail.
                Examples: "BI Analyst", "Data Scientist", "Revenue Operations Analyst"

When in doubt: if the role has "VP", "Chief", "Director", "Head of" → executive.
If it has "Manager", "Lead", "Coordinator" → manager. If "Analyst", "Scientist" → analyst.
  - CFO owns the financial health KPIs
  - Ops Manager owns throughput + delay KPIs
  - Sales Manager owns quota + pipeline KPIs
If two personas seem to want the same KPI, give it only to the persona whose
job most depends on it and design a DIFFERENT angle for the other persona.
"Total Sales" for a CFO becomes "Revenue Forecast Gap"; the Sales Manager gets
"Quota Coverage Rate" instead — same underlying field, completely different KPI.

Step 5 — CALL analyze_domain (Phase A — repeat until all views are covered)
ONLY use view names from `available_api_views`.
Pass relevant_fields using EXACT 'name' values from reachable_fields.
See "Tool call strategy" below for how to batch across multiple turns.

Step 6 — CALL generate_chart_spec FOR ALL KPIs IN PARALLEL (Phase B — one turn)

Step 7 — CALL emit_intelligence_config ONCE (Phase C)
Summary cards are generated automatically — pass summary_cards as [].

Rules
─────
- ALL field names (l1_field_name, x_axis, y_axis, breakdown_by, and any field
  name mentioned in computation_hint) MUST use the exact 'name' values from
  `reachable_fields` — the real CSV column names. Do not rename, prettify, or
  guess. The inventory may show display names with spaces (e.g. "Tourism Inbound")
  but the actual CSV column is underscored (e.g. "Tourism_Inbound") — always use
  the reachable_fields 'name', never the inventory display name.
- KPI names should sound like business questions or constructs, not Tableau pills.
- Forbidden: KPI names that are 1:1 with existing view names (you can SEE which
  view names exist via available_api_views — make your KPI names different).
- One objective sentence. Design as many personas, domains, and KPIs as the workbook genuinely
  supports — NEVER cap artificially. Every KPI must be distinct and data-backed.

- VIEW COVERAGE — mandatory:
  Every view in `available_api_views` must appear in at least one domain's relevant_views.
  Before calling generate_chart_spec, mentally walk through available_api_views and confirm
  each view has been assigned to a domain. If any view is missing, add it to an appropriate
  domain (or create a new domain for it) and design at least one KPI from it.
  The only exception: a view that is purely administrative (no measurable business metric
  inside) — exclude it explicitly with a one-line note in that domain's description.

- DEDUPLICATION — before emitting, scan your KPI list for near-duplicate metrics. Two KPIs
  measuring the same underlying fact are near-duplicates even if named differently:
  e.g. "Predicted Staffing Shortage" (-0.7) and "Predicted Shortage Absolute" (0.7) are the
  same metric — one is just abs() of the other. Keep only the richer version (the one with a
  chart or breakdown). Other near-duplicate patterns to eliminate: "Total Revenue" + "Revenue
  Sum", "Avg Bed Occupancy" + "Mean Occupancy Rate", "Count of Referrals" + "Referral Volume".
  If you find a near-duplicate pair, drop the weaker one entirely. Never surface the same
  business fact twice with slightly different math or naming.

- Every KPI must appear in EXACTLY ONE persona's dashboard. No kpi_id may be listed
  in multiple personas' dashboard_sections. The assembler enforces this — duplicates
  are silently dropped, so if you repeat a kpi_id across personas one persona will
  have an empty section. Assign each KPI to the persona that most needs it.
- Complete all analysis before emitting. Quality over speed.

Tool call strategy — maximize coverage, not minimize turns
──────────────────────────────────────────────────────────
You MUST cover every available view. Use as many analyze_domain turns as needed.

Phase A — Domain analysis (repeat until ALL views are covered):
  Group related views into domains. Call analyze_domain for multiple domains
  simultaneously within each turn. Keep going until every view in
  available_api_views has been assigned to a domain and analyzed.
  Group by business topic — not by a fixed view count. A domain can have
  1 view or 8 views depending on how related they are.
  Each domain call receives relevant_views and kpi_designs — the domain agent
  fetches data and computes ALL KPIs in that domain in a single call.

  STOP Phase A only when every view in available_api_views has been covered.

Phase B — Chart specs (one turn):
  Call generate_chart_spec for ALL KPIs from all domains simultaneously.

Phase C — Emit (one turn):
  Call emit_intelligence_config ONCE.

There is NO turn limit. Breadth of coverage matters more than speed.
Do not compress many views into a single domain just to finish faster.
"""


class OrchestratorAgent(BaseAgent):
    """
    Top-level orchestrator agent.

    Args:
        connector    : authenticated Tableau connector
        workbook_luid: luid of the target workbook
        workbook_meta: basic workbook metadata dict
    """

    def __init__(
        self,
        connector: TableauConnector,
        workbook_luid: str,
        workbook_meta: dict[str, Any],
        available_views: list[str] | None = None,
        manifest = None,   # pipeline.manifest.WorkbookManifest | None
    ) -> None:
        super().__init__(
            model          = _MODEL,
            tools          = ORCHESTRATOR_TOOLS,
            system_prompt  = _SYSTEM_PROMPT,
            max_iterations = 30,  # multi-phase: A(domain batches) + B(charts) + C(emit)
            max_tokens     = 8192,
        )
        self._connector       = connector
        self._workbook_luid   = workbook_luid
        self._workbook_meta   = workbook_meta
        self._available_views = available_views or []
        self._manifest        = manifest   # exact field names + reachability info

        # Semaphore: cap concurrent domain agents to avoid Gemini rate limits.
        # With multi-phase Phase-A, the orchestrator may issue 8-10 analyze_domain
        # calls per turn. Without a cap that's 8-10 simultaneous Gemini sessions,
        # each making their own calls — quickly hits quota. 5 is enough parallelism.
        self._domain_sem = threading.Semaphore(5)

        # Accumulated results from sub-agents
        self._domain_results: dict[str, dict] = {}   # domain_name -> result
        self._chart_specs:    dict[str, dict] = {}   # kpi_id -> spec
        self._kpi_meta:       dict[str, dict] = {}   # kpi_id -> {name, desc, l1_*} from chart tool input

        # Final emit
        self._config_emit: dict | None = None

    # ── public API ────────────────────────────────────────────────────────────

    def run_pipeline(
        self,
        filtered_inventory: dict[str, Any],
        eda: dict[str, Any] | None = None,
    ) -> IntelligenceConfig:
        """
        Run the full orchestration pipeline.

        Args:
            filtered_inventory: output of semantic_filter.filter_inventory()
            eda               : optional EDA pre-analysis dict from pipeline.eda.run_eda()

        Returns:
            IntelligenceConfig — the complete, assembled config
        """
        from pipeline.eda import format_eda_for_agent  # late import

        eda_text = format_eda_for_agent(eda) if eda else ""

        def _json_default(obj: Any) -> Any:
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

        # ── Build a compact "reachable fields" summary from the manifest ──────
        # This is the source of truth for what the agent should design with.
        reachable_fields_summary: list[dict[str, Any]] = []
        if self._manifest is not None:
            for f in self._manifest.all_fields():
                if f.reachable_via == "unreachable":
                    continue
                entry = {
                    "name":          f.real_name,           # exact name in real data
                    "data_type":     f.data_type,
                    "role":          f.role,
                    "via":           f.reachable_via,       # "vds" or "view"
                    "is_calculated": f.is_calculated,
                }
                if f.view_name:        entry["view"]       = f.view_name
                if f.datasource_luid:  entry["datasource"] = f.datasource_name
                reachable_fields_summary.append(entry)

        n_views  = len(self._available_views) if self._available_views else 0
        n_fields = len(reachable_fields_summary)
        # n_views / n_fields are passed as informational context only — no hardcoded minimums

        user_msg = json.dumps({
            "workbook_inventory":  filtered_inventory,
            "eda_pre_analysis":    eda_text or None,
            "available_api_views": self._available_views or None,
            "reachable_fields":    reachable_fields_summary or None,
            "workbook_scale": {
                "total_views":  n_views,
                "total_fields": n_fields,
                "coverage_requirement": (
                    "Every view in available_api_views must be covered by at least one domain. "
                    "Run as many Phase-A analyze_domain turns as needed to achieve full coverage. "
                    "Do not compress all views into a single batch — group by business topic."
                ),
            },
            "task": (
                "Analyze this Tableau workbook. Identify the single business objective and "
                "design intelligence that fully covers every available view — use as many domains "
                "and personas as the data genuinely warrants, and use as many analyze_domain "
                "turns as needed (Phase A) to cover all views before moving to chart specs (Phase B) "
                "and emit (Phase C). "
                "CRITICAL — use EXACT 'name' values from 'reachable_fields' for ALL field names. "
                "When calling analyze_domain, only pass view names from 'available_api_views'."
            ),
        }, indent=2, default=_json_default)

        outcome = self.run(user_msg)

        if self._config_emit is None:
            log.error("Orchestrator did not call emit_intelligence_config")
            raise RuntimeError("Orchestrator failed to emit config — check logs")

        return self._assemble_config(self._config_emit, filtered_inventory)

    # ── tool execution ────────────────────────────────────────────────────────

    def _execute_tool(self, name: str, tool_input: dict[str, Any]) -> Any:
        if name == "analyze_domain":
            return self._tool_analyze_domain(tool_input)
        if name == "generate_chart_spec":
            return self._tool_generate_chart_spec(tool_input)
        if name == "emit_intelligence_config":
            return self._tool_emit_intelligence_config(tool_input)
        raise ToolError(f"Unknown tool: {name}")

    def _tool_analyze_domain(self, inp: dict) -> dict:
        domain_name     = inp["domain_name"]
        relevant_fields = inp.get("relevant_fields", [])
        relevant_views  = inp.get("relevant_views", [])
        kpi_designs     = inp.get("kpi_designs", [])

        log.info("Spinning up domain agent for: %s (%d KPI designs)", domain_name, len(kpi_designs))

        with self._domain_sem:   # max 5 domain agents run concurrently
            agent  = DomainAgent(self._connector, self._workbook_luid)
            result = agent.analyze(domain_name, relevant_fields, relevant_views, kpi_designs)

        # Retry once if the agent finished without emitting (Gemini returned text
        # instead of calling the tool — happens when API is slow / overloaded)
        kpis = result.get("kpis", [])
        if not kpis:
            log.warning(
                "Domain '%s' returned 0 KPIs on first attempt — retrying once",
                domain_name,
            )
            agent2  = DomainAgent(self._connector, self._workbook_luid)
            result2 = agent2.analyze(domain_name, relevant_fields, relevant_views, kpi_designs)
            if result2.get("kpis"):
                result = result2
                log.info("Domain '%s' retry succeeded — got %d KPIs", domain_name, len(result2["kpis"]))
            else:
                log.warning("Domain '%s' retry also returned 0 KPIs — skipping domain", domain_name)

        self._domain_results[domain_name] = result
        kpis = result.get("kpis", [])
        kpi_count = len(kpis)

        # Defensively generate an id from name if the agent omitted it
        for kpi in kpis:
            if "id" not in kpi or not kpi["id"]:
                raw_name = kpi.get("name", f"kpi_{domain_name}")
                kpi["id"] = re.sub(r"[^a-z0-9]+", "_", raw_name.lower()).strip("_")
                log.warning(
                    "Domain '%s' returned KPI without id — generated: %s",
                    domain_name, kpi["id"],
                )

        log.info("Domain '%s' returned %d KPIs", domain_name, kpi_count)
        return {
            "status":     "ok",
            "domain":     domain_name,
            "kpi_count":  kpi_count,
            "kpi_ids":    [k["id"] for k in kpis],
            "kpi_names":  [k.get("name", k["id"]) for k in kpis],
        }

    def _tool_generate_chart_spec(self, inp: dict) -> dict:
        kpi_id   = inp["kpi_id"]
        kpi_name = inp["kpi_name"]

        log.info("Spinning up chart agent for KPI: %s", kpi_name)

        # ── Compute reachable_fields_in_view from manifest ────────────────────
        # If the KPI's l1_view_name maps to a view we've probed, expose THAT
        # view's exact CSV column list to the chart agent so it picks axes
        # from real names — never a metadata guess. Case-insensitive match
        # protects against the agent paraphrasing the view name slightly.
        view_name      = inp.get("l1_view_name", "")
        view_name_norm = view_name.strip().lower()
        reachable_columns: list[str] = []
        if self._manifest is not None and view_name_norm:
            for v in self._manifest.views:
                if (v.name or "").strip().lower() == view_name_norm and v.columns:
                    reachable_columns = list(v.columns)
                    break
        # Fall back to whatever the agent already learned, but prefer manifest
        if not reachable_columns:
            reachable_columns = list(inp.get("available_dimensions", []))

        # ── Pull real trend/anomaly data from the domain agent result ────────────
        # Domain agent has already fetched and analyzed the raw rows.
        # Pass those grounded findings to the chart agent so it writes
        # accurate key_insight and risk text, not guesses.
        kpi_domain_data: dict = {}
        for domain_result in self._domain_results.values():
            for k in domain_result.get("kpis", []):
                if k.get("id") == kpi_id:
                    kpi_domain_data = k
                    break

        agent = ChartAgent()
        spec  = agent.generate(
            kpi_id               = kpi_id,
            kpi_name             = kpi_name,
            kpi_description      = inp.get("kpi_description", ""),
            domain               = inp.get("domain", ""),
            l1_value             = inp.get("l1_value"),
            l1_unit              = inp.get("l1_unit", ""),
            l1_format            = inp.get("l1_format", "number"),
            l1_view_name         = inp.get("l1_view_name", ""),
            l1_field_name        = inp.get("l1_field_name", ""),
            has_formula          = inp.get("has_formula", False),
            formula              = inp.get("formula"),
            formula_parameters   = inp.get("formula_parameters", []),
            available_dimensions = reachable_columns,
            objective            = inp.get("objective", ""),
            persona_role         = inp.get("persona_role", ""),
            # Real data from domain agent ↓
            trend_direction      = kpi_domain_data.get("trend_direction"),
            trend_pct            = kpi_domain_data.get("trend_pct"),
            trend_description    = kpi_domain_data.get("trend_description"),
            anomaly              = kpi_domain_data.get("anomaly"),
            raw_data_sample      = (kpi_domain_data.get("raw_data") or [])[:10],
        )

        self._chart_specs[kpi_id] = spec
        # Store KPI metadata so we can build stub KPIs if domain agents fail
        self._kpi_meta[kpi_id] = {
            "name":          kpi_name,
            "description":   inp.get("kpi_description", ""),
            "l1_value":      inp.get("l1_value"),
            "l1_unit":       inp.get("l1_unit", ""),
            "l1_format":     inp.get("l1_format", "number"),
            "l1_view_name":  inp.get("l1_view_name", ""),
            "l1_field_name": inp.get("l1_field_name", ""),
        }

        log.info("Chart spec for '%s': type=%s", kpi_name, spec.get("chart_type", "?"))
        return {
            "status":     "ok",
            "kpi_id":     kpi_id,
            "chart_type": spec.get("chart_type"),
        }

    def _tool_emit_intelligence_config(self, inp: dict) -> dict:
        self._config_emit = inp
        persona_count = len(inp.get("personas", []))
        log.info("Orchestrator emitted config with %d personas", persona_count)
        for p in inp.get("personas", []):
            log.info("  Persona '%s': %d sections", p.get("role"), len(p.get("dashboard_sections", [])))
        return {"status": "ok", "personas": persona_count}

    # ── axis normalisation (post-agent validation) ───────────────────────────

    @staticmethod
    def _normalise_chart_axes(
        spec_x:      str | None,
        spec_y:      str | None,
        l1_field:    str,
        actual_cols: list[str],
        chart_type:  str,
    ) -> tuple[str | None, str | None]:
        """
        Force chart axes to columns that ACTUALLY exist in the fetched data.

        Strategy:
          • If the agent's pick is already a real column, keep it.
          • Otherwise try the L1 field_name (which the domain agent set after
            inspecting real data — usually correct).
          • Otherwise fall back to a fuzzy match against actual_cols.
          • If all else fails, return None — the frontend will degrade to L1-only.
        """
        if not actual_cols:
            # No data to validate against — return what the agent picked.
            return spec_x, spec_y

        def _find(name: str | None) -> str | None:
            """Find a real column matching `name`. Does NOT fall back to l1_field —
            that's specific to y_axis and handled separately."""
            if not name:
                return None
            if name in actual_cols:
                return name
            # Fuzzy: normalise both sides and compare
            import re
            def _n(s: str) -> str:
                return re.sub(r"[^a-z0-9]+", "", s.lower())
            target = _n(name)
            for c in actual_cols:
                if _n(c) == target:
                    return c
            # Substring match
            for c in actual_cols:
                nc = _n(c)
                if target and (target in nc or nc in target):
                    return c
            return None

        # ── y_axis: agent's pick → exact/fuzzy match → l1_field as last resort ──
        y_resolved = _find(spec_y)
        if not y_resolved and l1_field and l1_field in actual_cols:
            y_resolved = l1_field

        # ── x_axis: agent's pick → temporal column → ANY label col except y ────
        x_resolved = _find(spec_x)
        if not x_resolved and chart_type not in ("kpi_card", "gauge_chart", "scorecard"):
            # Heuristic: temporal column first (date/month/year/etc.)
            for c in actual_cols:
                nc = c.lower()
                if any(k in nc for k in ("date", "month", "year", "period", "quarter", "week", "day")):
                    x_resolved = c
                    break
            # Otherwise: any column that is NOT the y_axis (prefer non-"measure" containers)
            if not x_resolved:
                preferred = [c for c in actual_cols if c != y_resolved
                             and "measure" not in c.lower()
                             and not c.lower().startswith("latitude")
                             and not c.lower().startswith("longitude")]
                if preferred:
                    x_resolved = preferred[0]
                else:
                    # absolute last resort: anything that isn't y
                    rest = [c for c in actual_cols if c != y_resolved]
                    if rest:
                        x_resolved = rest[0]

        # Defensive: never let x == y
        if x_resolved is not None and x_resolved == y_resolved:
            x_resolved = None

        return x_resolved, y_resolved

    # ── config assembly ───────────────────────────────────────────────────────

    def _assemble_config(
        self,
        emit: dict,
        filtered_inventory: dict[str, Any],
    ) -> IntelligenceConfig:
        """
        Build the final IntelligenceConfig from all collected data.
        Assembles: domain results + chart specs → full config (multi-persona).
        """
        now = datetime.now(timezone.utc).isoformat()

        # ── workbook metadata ────────────────────────────────────────────────
        wb_meta = WorkbookMeta(
            name              = self._workbook_meta.get("name", "Unknown"),
            project           = self._workbook_meta.get("project_name"),
            tableau_updated_at= self._workbook_meta.get("updated_at"),
            data_sources      = [
                c["datasource"]
                for c in filtered_inventory.get("data_connections", [])
            ],
        )

        # ── build KPI map (kpi_id → KPI object) ─────────────────────────────
        all_kpis: dict[str, KPI] = {}
        explicitly_dropped: set[str] = set()  # KPIs dropped due to missing/hallucinated data

        for domain_name, domain_result in self._domain_results.items():
            for kpi_raw in domain_result.get("kpis", []):
                kpi_id = kpi_raw["id"]
                spec   = self._chart_specs.get(kpi_id, {})

                # Resolve unit first — used by both L1 and chart aggregation
                raw_unit  = kpi_raw.get("l1_unit", "")
                raw_value = kpi_raw.get("l1_value")
                kpi_unit  = raw_unit or _infer_unit(
                    kpi_raw.get("name", ""),
                    kpi_raw.get("l1_field_name", ""),
                    raw_value,
                )

                # L1
                l1 = None
                if kpi_raw.get("l1_view_name"):
                    l1 = L1Data(
                        value      = raw_value,
                        unit       = kpi_unit,
                        format     = kpi_raw.get("l1_format", "number") or _infer_format(kpi_unit),
                        view_name  = kpi_raw["l1_view_name"],
                        field_name = kpi_raw["l1_field_name"],
                    )

                # L2 (deterministic — Tableau formula evaluation)
                l2 = evaluate_l2(kpi_raw, filtered_inventory)

                # L2 Projection — agent-defined method for 7D/30D forecasts
                l2_proj_raw = kpi_raw.get("l2_projection")
                l2_projection: L2Projection | None = None
                if l2_proj_raw and isinstance(l2_proj_raw, dict):
                    try:
                        l2_projection = L2Projection(**l2_proj_raw)
                    except Exception as exc:
                        log.warning("Invalid l2_projection for KPI '%s': %s", kpi_id, exc)

                # Chart spec — fill in aggregation default if agent omitted it
                chart_type  = spec.get("chart_type", "kpi_card")
                aggregation = spec.get("aggregation")
                if not aggregation and chart_type not in ("kpi_card", "gauge_chart", "pie_chart", "map_chart"):
                    # Rate/ratio/time KPIs → avg; monetary/count → sum
                    aggregation = "avg" if kpi_unit in ("%", "score", "days", "hours") else "sum"

                # ── VALIDATION: ensure chart axes match REAL columns in raw_data ──
                # Catches the case where the chart agent guessed names like 'Year'
                # that don't exist in the actual fetched CSV.
                raw_rows = kpi_raw.get("raw_data") or []
                actual_cols = list(raw_rows[0].keys()) if raw_rows else []
                x_axis_resolved, y_axis_resolved = self._normalise_chart_axes(
                    spec_x      = spec.get("x_axis"),
                    spec_y      = spec.get("y_axis"),
                    l1_field    = kpi_raw.get("l1_field_name", ""),
                    actual_cols = actual_cols,
                    chart_type  = chart_type,
                )

                chart = ChartSpec(
                    type         = chart_type,
                    x_axis       = x_axis_resolved,
                    y_axis       = y_axis_resolved,
                    x_axis_type  = spec.get("x_axis_type"),
                    aggregation  = aggregation,
                    sort_order   = spec.get("sort_order"),
                    breakdown_by = spec.get("breakdown_by") if spec.get("breakdown_by") in actual_cols else None,
                    color_by     = spec.get("color_by")     if spec.get("color_by")     in actual_cols else None,
                    sort_by      = spec.get("sort_by")      if spec.get("sort_by")      in actual_cols else None,
                    filters      = spec.get("filters", []),
                    notes        = spec.get("chart_notes"),
                )

                # Explanation — enrich risk + key_insight with sub-segment and driver data
                # from the domain agent (critical_segments, key_drivers) when available
                raw_risk    = spec.get("explanation_risk") or kpi_raw.get("anomaly")
                raw_insight = spec.get("explanation_key_insight")

                critical_segments = kpi_raw.get("critical_segments")
                key_drivers       = kpi_raw.get("key_drivers")

                # Prepend critical segments to risk flag so it surfaces in the UI
                if critical_segments and isinstance(critical_segments, list):
                    seg_text = "Critical sub-segments: " + ", ".join(critical_segments[:3])
                    raw_risk = f"{seg_text}. {raw_risk}" if raw_risk else seg_text

                # Prepend key drivers to key_insight
                if key_drivers and isinstance(key_drivers, list):
                    drivers_text = " · ".join(key_drivers[:3])
                    raw_insight = f"{drivers_text}. {raw_insight}" if raw_insight else drivers_text

                explanation = Explanation(
                    what          = spec.get("explanation_what", kpi_raw.get("description", "")),
                    why_it_matters= spec.get("explanation_why_matters", ""),
                    trend         = spec.get("explanation_trend") or kpi_raw.get("trend_description"),
                    risk          = raw_risk,
                    key_insight   = raw_insight,
                )

                kpi_obj = KPI(
                    id              = kpi_id,
                    name            = kpi_raw["name"],
                    description     = kpi_raw.get("description", ""),
                    layer           = kpi_raw.get("layer", "L1"),
                    l1              = l1,
                    l2              = l2,
                    l2_projection   = l2_projection,
                    trend_direction = kpi_raw.get("trend_direction"),
                    trend_pct       = kpi_raw.get("trend_pct"),
                    chart           = chart,
                    explanation     = explanation,
                    raw_data        = kpi_raw.get("raw_data", []),
                )

                # ── BROKEN-KPI FILTER ────────────────────────────────────────
                # Drop KPIs that would display wrong or misleading data:
                #   1. l1_value is explicitly null → agent said data not found
                #   2. No L1 value AND no raw rows → "$0/0 with empty chart"
                #   3. Non-trivial chart type with no rows → empty chart
                #   4. Unit is "%" but value > 100 → impossible, bad computation
                no_data  = not (kpi_raw.get("raw_data") or [])
                ROW_BASED_CHARTS = {
                    "line_chart", "bar_chart", "stacked_bar_chart",
                    "horizontal_bar_chart", "area_chart", "scatter_chart",
                    "pie_chart", "map_chart", "waterfall_chart",
                }

                # Rule 1: null l1_value = agent explicitly couldn't find data
                if raw_value is None:
                    log.warning(
                        "Dropping KPI '%s' — agent returned null l1_value "
                        "(field=%r not found in view=%r).",
                        kpi_id, kpi_raw.get("l1_field_name"), kpi_raw.get("l1_view_name"),
                    )
                    explicitly_dropped.add(kpi_id)
                    continue

                # Rule 2: zero value AND no data = nothing real was computed
                if raw_value == 0 and no_data:
                    log.warning(
                        "Dropping KPI '%s' — l1_value=0 and no raw_data "
                        "(field=%r view=%r likely not found).",
                        kpi_id, kpi_raw.get("l1_field_name"), kpi_raw.get("l1_view_name"),
                    )
                    explicitly_dropped.add(kpi_id)
                    continue

                # Rule 3: chart needs rows but agent fetched none
                if no_data and chart_type in ROW_BASED_CHARTS:
                    log.warning(
                        "Dropping KPI '%s' — chart type %r needs row data but "
                        "agent fetched 0 rows (L1=%r field=%r view=%r).",
                        kpi_id, chart_type, raw_value,
                        kpi_raw.get("l1_field_name"), kpi_raw.get("l1_view_name"),
                    )
                    explicitly_dropped.add(kpi_id)
                    continue

                # Rule 4: impossible percentage (> 100 means wrong denominator/unit)
                # Parse raw_value robustly — agent may return a string like "79,227"
                _numeric_value: float | None = None
                if isinstance(raw_value, (int, float)):
                    _numeric_value = float(raw_value)
                elif isinstance(raw_value, str):
                    try:
                        _numeric_value = float(raw_value.replace(",", "").replace("%", "").strip())
                    except (ValueError, TypeError):
                        pass

                if kpi_unit == "%" and _numeric_value is not None and _numeric_value > 100:
                    log.warning(
                        "Dropping KPI '%s' — value %.1f%% > 100%% is impossible "
                        "(wrong denominator or unit mislabelled).",
                        kpi_id, raw_value,
                    )
                    explicitly_dropped.add(kpi_id)
                    continue

                all_kpis[kpi_id] = kpi_obj

        # ── fallback: build stub KPIs from chart-spec metadata when domain agents failed ──
        # Never rebuild a KPI that was explicitly dropped (no data / hallucinated value).
        for kpi_id, meta in self._kpi_meta.items():
            if kpi_id in all_kpis:
                continue  # already have full data from domain agent
            if kpi_id in explicitly_dropped:
                log.info("Skipping stub for dropped KPI '%s' (no real data exists)", kpi_id)
                continue
            spec = self._chart_specs.get(kpi_id, {})
            chart_type  = spec.get("chart_type", "kpi_card")
            kpi_unit    = meta.get("l1_unit", "")
            aggregation = spec.get("aggregation")
            if not aggregation and chart_type not in ("kpi_card", "gauge_chart", "pie_chart", "map_chart"):
                aggregation = "avg" if kpi_unit in ("%", "score", "days", "hours") else "sum"
            l1 = None
            if meta.get("l1_view_name"):
                l1 = L1Data(
                    value      = meta.get("l1_value"),
                    unit       = kpi_unit or None,
                    format     = meta.get("l1_format", "number") or _infer_format(kpi_unit),
                    view_name  = meta["l1_view_name"],
                    field_name = meta.get("l1_field_name", ""),
                )
            chart = ChartSpec(
                type         = chart_type,
                x_axis       = spec.get("x_axis"),
                y_axis       = spec.get("y_axis"),
                x_axis_type  = spec.get("x_axis_type"),
                aggregation  = aggregation,
                sort_order   = spec.get("sort_order"),
                breakdown_by = spec.get("breakdown_by"),
                color_by     = spec.get("color_by"),
                sort_by      = spec.get("sort_by"),
                filters      = spec.get("filters", []),
                notes        = spec.get("chart_notes"),
            )
            all_kpis[kpi_id] = KPI(
                id              = kpi_id,
                name            = meta.get("name", kpi_id),
                description     = meta.get("description", ""),
                l1              = l1,
                l2              = None,
                trend_direction = None,
                trend_pct       = None,
                chart           = chart,
                explanation     = Explanation(
                    what           = spec.get("explanation_what", meta.get("description", "")),
                    why_it_matters = spec.get("explanation_why_matters", ""),
                    trend          = spec.get("explanation_trend"),
                    risk           = spec.get("explanation_risk"),
                    key_insight    = spec.get("explanation_key_insight"),
                ),
                raw_data        = [],
            )
            log.info("Built stub KPI '%s' from chart spec (domain agent did not emit result)", kpi_id)

        log.info("KPIs assembled: %s", list(all_kpis.keys()))
        for p_raw in emit.get("personas", []):
            for sec in p_raw.get("dashboard_sections", []):
                log.info(
                    "Persona '%s' section '%s' references kpi_ids: %s",
                    p_raw.get("role"), sec.get("id"), sec.get("kpi_ids", [])
                )

        # ── assemble persona views ────────────────────────────────────────────
        # Sections are built per-persona so each persona has an independent KPI list.
        # KPIs are exclusive: once a kpi_id is assigned to a persona it cannot
        # appear in any later persona (prevents duplicates across dashboards).
        persona_views: list[PersonaView] = []
        claimed_kpi_ids: set[str] = set()

        for p_raw in emit.get("personas", []):
            persona = Persona(
                role          = p_raw["role"],
                focus_areas   = p_raw.get("focus_areas", []),
                rationale     = p_raw.get("rationale", ""),
                persona_level = p_raw.get("persona_level") or _infer_persona_level(p_raw["role"]),
            )

            # Build this persona's sections independently
            sections: list[DashboardSection] = []
            for sec_raw in p_raw.get("dashboard_sections", []):
                # Exclude KPIs already claimed by an earlier persona
                kpi_objects = [
                    all_kpis[kid]
                    for kid in sec_raw.get("kpi_ids", [])
                    if kid in all_kpis and kid not in claimed_kpi_ids
                ]
                duplicate_ids = [
                    kid for kid in sec_raw.get("kpi_ids", [])
                    if kid in claimed_kpi_ids
                ]
                if duplicate_ids:
                    log.warning(
                        "Persona '%s' section '%s': KPI(s) %s already used by an "
                        "earlier persona — skipping to prevent dashboard duplication",
                        p_raw.get("role"), sec_raw.get("id"), duplicate_ids,
                    )
                if kpi_objects:
                    sections.append(DashboardSection(
                        id          = sec_raw["id"],
                        title       = sec_raw["title"],
                        description = sec_raw.get("description", ""),
                        kpis        = kpi_objects,
                    ))

            if not sections:
                log.warning("Persona '%s' has no valid sections — skipping", p_raw["role"])
                continue

            # Mark all KPIs in this persona as claimed so later personas can't duplicate them
            for sec in sections:
                for kpi in sec.kpis:
                    claimed_kpi_ids.add(kpi.id)

            # ── Generate data-grounded summary cards via SummaryAgent ────────────
            # Collect all real KPI data for this persona (values, trends, anomalies,
            # key insights) and pass to a dedicated agent that writes grounded cards.
            persona_kpi_data: list[dict] = []
            for sec in sections:
                for kpi in sec.kpis:
                    # Find domain agent findings for this KPI
                    domain_findings: dict = {}
                    for dr in self._domain_results.values():
                        for dk in dr.get("kpis", []):
                            if dk.get("id") == kpi.id:
                                domain_findings = dk
                                break

                    persona_kpi_data.append({
                        "name":             kpi.name,
                        "description":      kpi.description,
                        "layer":            kpi.layer,
                        "value":            kpi.l1.value if kpi.l1 else None,
                        "unit":             kpi.l1.unit  if kpi.l1 else "",
                        "trend_direction":  kpi.trend_direction,
                        "trend_pct":        kpi.trend_pct,
                        "trend_description": domain_findings.get("trend_description"),
                        "anomaly":          domain_findings.get("anomaly"),
                        "key_insight":      kpi.explanation.key_insight if kpi.explanation else None,
                        "risk":             kpi.explanation.risk        if kpi.explanation else None,
                    })

            log.info(
                "Running SummaryAgent for persona '%s' with %d KPIs",
                p_raw["role"], len(persona_kpi_data),
            )
            try:
                raw_cards = SummaryAgent().generate(
                    persona_role       = p_raw["role"],
                    focus_areas        = p_raw.get("focus_areas", []),
                    business_objective = emit.get("objective", ""),
                    kpis               = persona_kpi_data,
                )
            except Exception as exc:
                log.warning("SummaryAgent failed for '%s': %s — using empty cards", p_raw["role"], exc)
                raw_cards = []

            summary_cards = [
                SummaryCard(
                    title  = c.get("title", "Summary"),
                    body   = c.get("body", ""),
                    signal = c.get("signal", "neutral"),
                )
                for c in raw_cards
            ]

            persona_views.append(PersonaView(
                persona            = persona,
                summary_cards      = summary_cards,
                dashboard_sections = sections,
            ))

        if not persona_views:
            log.error("No valid persona views assembled — check orchestrator emit")
            raise RuntimeError("Orchestrator produced no valid persona views")

        return IntelligenceConfig(
            generated_at = now,
            workbook     = wb_meta,
            objective    = emit["objective"],
            personas     = persona_views,
        )
