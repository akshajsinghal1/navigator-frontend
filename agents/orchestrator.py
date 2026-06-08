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
        # NOTE: 'forecast'/'value'/'amount' removed — too generic, they wrongly
        # tagged non-monetary metrics (e.g. "Staffing Gap Forecast" -> $) as USD.
        "quota", "ote", "budget", "expense", "overhead",
        # SaaS
        "mrr", "arr", "ltv", "cac", "acv", "tcv", "arpu", "expansion",
        # Finance
        "aum", "nav", "asset", "liability", "equity", "loan", "fund",
        "investment", "portfolio", "capital", "cash", "debt", "receivable",
        "payable", "balance",
        # Logistics
        "freight", "shipping cost", "fulfillment cost",
        # Generic
        "payment", "transaction total", "billing",
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
        "roas", "return on", "occupancy", "occupancy rate", "occupancy %",
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
    u = (unit or "").strip().lower()
    if u in ("usd", "$", "€", "£", "¥"):
        return "currency"
    # Domain agents sometimes return 'ratio', 'rate', 'percent' etc. instead of '%'
    if u in ("%", "percent", "pct", "percentage", "rate", "ratio"):
        return "percentage"
    if u in ("days", "hrs", "hours"):
        return ",.1f"
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

Step 0 — DATA SOURCE PRIORITY (read before everything else)
──────────────────────────────────────────────────────────
Your input includes HYPER EXTRACT tables (prefixed with [TABLE]).
These raw tables are the PRIMARY data source for ALL KPI computation.

RULE 1 — ALWAYS prefer [TABLE] over Tableau views for data:
  [TABLE] demo_bed_utilization_hourly  →  100,800 hourly rows  ← USE THIS
  Occupancy Trend view                 →      355 aggregated rows  ← avoid for data

RULE 2 — Tableau views are for REFERENCE ONLY:
  Use views to understand what metrics exist and business context.
  Use [TABLE] sources to actually fetch and compute the data.

RULE 3 — Compute derived metrics yourself from raw columns:
  The HYPER EXTRACT section lists calculated field formulas.
  Use run_analysis in domain agents to compute them from raw columns.
  Example: Occupancy % = occupied_beds / staffed_beds × 100
           (compute from demo_bed_utilization_hourly, not from Occupancy Trend view)

RULE 4 — Only fall back to Tableau views when:
  (a) The metric is a pre-computed ML forecast (FORECAST_OCCUPANCY, FORECAST_STAFFING_RISK)
  (b) No [TABLE] source exists for that metric
  Both are rare — most metrics can be computed from raw [TABLE] columns.

Step 0a — READ VIEW_QUALITY BEFORE DESIGNING DOMAINS
Your input includes VIEW_QUALITY — a compact, deterministic index of every data view.
Before assigning any view to a domain, check its entry:

  is_scalar = true
    → This view has ONE row. Use it only for kpi_card or gauge_chart KPIs.
      Do NOT design a time-series, breakdown, or trend KPI from a scalar view.

  degenerate_breakdowns = [...]
    → These dimension columns do NOT carry meaningful signal within this view.
      Do NOT use them as breakdown_by in any KPI design from this view.
      Using a degenerate breakdown produces a misleading chart.

  entity_dims = {"Facility Name": {"distinct": 5}}
    → This is a verified entity dimension. It IS a good breakdown candidate.
      If designing a KPI "by Facility" from this view, use this column.

  flag_codes
    → "suspicious_uniform" → do NOT headline a 'top segment' from this view.
    → "high_null"          → treat this view's data as unreliable for primary KPIs.

Step 0b — OBEY THE VERIFIED DATA PROFILE (highest priority)
The input contains VERIFIED_DATA_PROFILE — deterministic ground truth computed
from the real data. It is more authoritative than view names or your assumptions.
Its "MANDATORY DATA-QUALITY RULES" are HARD constraints, not suggestions:
  • SCALAR (single-row) views → design as kpi_card or gauge_chart ONLY. Never a
    line/bar/area chart — there is no series to plot, the chart will be empty.
  • DEGENERATE breakdowns → if the profile says a measure does NOT vary across a
    dimension, you may NOT chart that measure broken down by that dimension. Pick a
    dimension the profile shows real variation on, or omit the breakdown.
  • NEAR-UNIFORM categories → do NOT design a KPI that headlines a "largest" /
    "top" segment for these — the distribution is noise/likely synthetic.
  • CANONICAL labels → when the profile lists normalized aliases, use the canonical
    form, never the raw variant.
  • EXACT column names → use the profile's column names verbatim. Never invent,
    prettify, or underscore them.
  • VERIFIED RELATIONSHIPS → you may rely on these in KPI definitions. CANDIDATE
    relationships must be treated as unconfirmed.
  • UNRECONCILED rates → present them plainly; do not assert they equal a specific
    ratio of other measures.
If the profile and a view name disagree, the profile wins.

Step 1 — UNDERSTAND THE BUSINESS
  - Read the VERIFIED_DATA_PROFILE first, then field names, formulas, reachable_fields
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

- VIEW COVERAGE — mandatory, but coverage means USED, not standalone KPI:
  Every view in `available_api_views` must be USED — but "used" can mean either
  (a) it powers its own KPI, OR (b) it is attached to another KPI as a supporting band.
  Before calling generate_chart_spec, walk through available_api_views and confirm each
  view is accounted for one of these two ways.
  Exception: a view that is purely administrative (no measurable business metric, e.g.
  a filter panel or a dashboard container) — exclude it with a one-line note.

- CONFIDENCE / BOUND VIEWS — do NOT make these standalone KPIs:
  Views named like "Upper Confidence", "Lower Confidence", "Upper Gap Confidence",
  "Lower Gap Confidence", "p10", "p90", "interval", "bound" are the confidence BANDS
  for a forecast — they are not decisions on their own. A standalone KPI that just says
  "Lower Confidence Limit = 69.7" is meaningless to a user.
  Instead: attach them to the forecast KPI they belong to. The forecast KPI (e.g.
  "Forecasted Occupancy") should reference the upper/lower views as its confidence band
  (the frontend renders them automatically as a shaded band on the line chart).
  NEVER create a persona whose KPIs are only confidence bounds — that is a junk persona.
  Count these bound views as "used" via their parent forecast KPI for coverage purposes.

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
        view_cache: dict[str, list[dict]] | None = None,
        profile = None,    # pipeline.profiler.WorkbookProfile | None
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
        self._manifest        = manifest
        self._view_cache      = view_cache or {}
        self._profile         = profile    # WorkbookProfile — source for ViewProfile slices

        # ── Run metrics: accumulated throughout the pipeline, saved at the end ──
        self._run_metrics: dict[str, Any] = {}

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
        profile_text: str = "",
    ) -> IntelligenceConfig:
        """
        Run the full orchestration pipeline.

        Args:
            filtered_inventory: output of semantic_filter.filter_inventory()
            eda               : optional EDA pre-analysis dict (legacy structural feed)
            profile_text      : verified data-profile fact sheet from pipeline.profiler.
                                When present, this is the PRIMARY ground truth the
                                orchestrator must design from (replaces the thin EDA).

        Returns:
            IntelligenceConfig — the complete, assembled config
        """
        from pipeline.eda import format_eda_for_agent  # late import

        # Prefer the verified profile; fall back to structural EDA only if absent.
        eda_text = profile_text or (format_eda_for_agent(eda) if eda else "")

        def _json_default(obj: Any) -> Any:
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

        # ── Build a compact "reachable fields" summary from the manifest ──────
        # Change 2: use profiler knowledge to prune noise fields before the
        # orchestrator sees them. A field is excluded if:
        #   - unreachable (not in any view CSV)
        #   - constant in the profiler (carries no information)
        #   - flagged as high_null (>30% null, unreliable)
        # This reduces cognitive load without hardcoding any field names.
        profiler_noise_fields: set[str] = set()
        if self._profile is not None:
            for col in self._profile.columns:
                if col.constant:
                    profiler_noise_fields.add(col.name)
            for flag in self._profile.flags:
                if flag.code == "high_null" and "::" in flag.where:
                    profiler_noise_fields.add(flag.where.split("::", 1)[1])

        reachable_fields_summary: list[dict[str, Any]] = []
        fields_before = 0
        if self._manifest is not None:
            for f in self._manifest.all_fields():
                fields_before += 1
                if f.reachable_via == "unreachable":
                    continue
                if f.real_name in profiler_noise_fields:
                    continue  # constant or high-null — no value to the orchestrator
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
        fields_pruned = fields_before - n_fields
        if fields_pruned > 0:
            log.info(
                "Semantic filter (Change 2): pruned %d noise/constant/unreachable fields "
                "→ %d fields reaching orchestrator (was %d)",
                fields_pruned, n_fields, fields_before,
            )
        self._run_metrics["reachable_fields_before_prune"] = fields_before
        self._run_metrics["reachable_fields_after_prune"]  = n_fields
        self._run_metrics["fields_pruned_by_profiler"]     = fields_pruned

        # ── View quality map: compact per-view facts for domain planning ───────
        # Gives the orchestrator grain, entity dims, and quality flags for every
        # view BEFORE it designs KPI assignments — so bad designs don't enter.
        view_quality: dict = {}
        if self._profile is not None:
            try:
                from pipeline.profiler import get_view_quality_map
                view_quality = get_view_quality_map(self._profile)
            except Exception as exc:
                log.debug("Could not build view_quality_map: %s", exc)

        # ── Thin metric: did orchestrator receive view quality? ────────────────
        self._run_metrics["orchestrator_had_view_quality"] = bool(view_quality)
        self._run_metrics["view_quality_views_covered"]    = len(view_quality)
        scalar_views = sum(1 for v in view_quality.values() if v.get("is_scalar"))
        degen_views  = sum(1 for v in view_quality.values() if v.get("degenerate_breakdowns"))
        self._run_metrics["scalar_views_in_quality_map"]     = scalar_views
        self._run_metrics["degenerate_views_in_quality_map"] = degen_views
        log.info(
            "View quality map: %d views (%d scalar, %d with degenerate breakdowns)",
            len(view_quality), scalar_views, degen_views,
        )

        user_msg = json.dumps({
            "VERIFIED_DATA_PROFILE": eda_text or None,
            "VIEW_QUALITY":          view_quality or None,   # ← new: per-view planning facts
            "workbook_inventory":  filtered_inventory,
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
                "Analyze this Tableau workbook. The VERIFIED_DATA_PROFILE is computed ground "
                "truth — design from it, and OBEY its MANDATORY DATA-QUALITY RULES exactly "
                "(scalar views -> kpi_card; never break a measure down by a degenerate dimension; "
                "never headline a near-uniform segment; use canonical labels; use EXACT column names). "
                "Identify the single business objective and design intelligence that covers every "
                "available view — as many domains/personas as the data warrants, across as many "
                "Phase-A analyze_domain turns as needed, then chart specs (Phase B), then emit (Phase C). "
                "Use EXACT column names from the profile / reachable_fields — never invent or reformat them."
            ),
        }, indent=2, default=_json_default)

        outcome = self.run(user_msg)

        if self._config_emit is None:
            log.error("Orchestrator did not call emit_intelligence_config")
            raise RuntimeError("Orchestrator failed to emit config — check logs")

        # Finalise run metrics before returning
        self._run_metrics["kpis_assembled"] = len(self._domain_results)
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

        # Seed each domain agent with the profiler's pre-fetched data for its views
        domain_cache = {v: self._view_cache[v] for v in relevant_views if v in self._view_cache}

        with self._domain_sem:   # max 5 domain agents run concurrently
            agent  = DomainAgent(self._connector, self._workbook_luid, view_cache=domain_cache)
            result = agent.analyze(domain_name, relevant_fields, relevant_views, kpi_designs)

        # Retry once if the agent finished without emitting (Gemini returned text
        # instead of calling the tool — happens when API is slow / overloaded)
        kpis = result.get("kpis", [])
        if not kpis:
            log.warning(
                "Domain '%s' returned 0 KPIs on first attempt — retrying once",
                domain_name,
            )
            agent2  = DomainAgent(self._connector, self._workbook_luid, view_cache=domain_cache)
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
        kpi_domain_data: dict = {}
        for domain_result in self._domain_results.values():
            for k in domain_result.get("kpis", []):
                if k.get("id") == kpi_id:
                    kpi_domain_data = k
                    break

        # ── Build ViewProfile slice from the profiler ─────────────────────────
        # This is the core of Sprint 1: give the chart agent structural truth
        # about the view — grain, field types, cardinality, entity dims, flags —
        # instead of just a flat list of column name strings.
        view_profile: dict | None = None
        if self._profile is not None and view_name:
            try:
                from pipeline.profiler import get_view_profile
                view_profile = get_view_profile(view_name, self._profile)
            except Exception as exc:
                log.debug("Could not build view_profile for %r: %s", view_name, exc)

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
            view_profile         = view_profile,   # ← structured truth from profiler
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

                # Resolve unit first — used by both L1 and chart aggregation.
                # Strip units that are plain English words the format system can't render.
                # Generic rule (no hardcoded word list):
                #   pure alphabetic word  +  _infer_format maps it to "number"
                #   → the frontend has no rendering strategy for it → strip it.
                # Keeps: %, $, days, hours (map to percentage/currency/,.1f).
                # Strips: staff, patients, vehicles, transactions — any workbook.
                raw_unit = kpi_raw.get("l1_unit", "") or ""
                if (raw_unit
                        and raw_unit.strip().replace(" ", "").isalpha()
                        and _infer_format(raw_unit) == "number"):
                    raw_unit = ""
                raw_value = kpi_raw.get("l1_value")
                kpi_unit  = raw_unit or _infer_unit(
                    kpi_raw.get("name", ""),
                    kpi_raw.get("l1_field_name", ""),
                    raw_value,
                )

                # L1
                # Format: trust agent's format only when it's specific (not the
                # generic "number" fallback). If agent said "number" but the unit
                # is %, override with "percentage" so the frontend renders correctly.
                raw_fmt   = kpi_raw.get("l1_format") or ""
                # "%" / "pct" / "ratio" are not frontend-renderable format strings — normalize.
                # Also treat generic "number" as unset and let _infer_format decide from unit.
                _GENERIC_FMTS = {"number", "", None, "%", "pct", "ratio", "rate", "percent"}
                l1_format = raw_fmt if raw_fmt not in _GENERIC_FMTS else _infer_format(kpi_unit)

                l1 = None
                if kpi_raw.get("l1_view_name"):
                    l1 = L1Data(
                        value      = raw_value,
                        unit       = kpi_unit,
                        format     = l1_format,
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
                    priority        = int(kpi_raw.get("priority", 50)),
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

                # Rule 5: duplicate value from a dashboard/container view.
                # Dashboard sheets (Navigator_*, *Dashboard*) aggregate the same
                # numbers as dedicated KPI views → produce identical values.
                # Keep the one from the dedicated view; drop the dashboard duplicate.
                _CONTAINER_HINTS = ("navigator", "dashboard", "analytics")
                view_nm = (kpi_raw.get("l1_view_name") or "").lower()
                is_container = any(h in view_nm for h in _CONTAINER_HINTS)
                if is_container:
                    # Check if an existing KPI already has the same value
                    same_val = any(
                        k.l1 and k.l1.value == raw_value
                        for k in all_kpis.values()
                    )
                    if same_val:
                        log.info(
                            "Dropping KPI '%s' — duplicate value %.4s from dashboard "
                            "container view '%s'.",
                            kpi_id, str(raw_value), kpi_raw.get("l1_view_name"),
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
        # Each entry: (role, focus_areas, objective, kpi_data) — for parallel summary run
        _pending_summaries: list[tuple] = []

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

            # Collect KPI data — summary agents run in parallel after all personas assembled
            persona_kpi_data: list[dict] = []
            for sec in sections:
                for kpi in sec.kpis:
                    domain_findings: dict = {}
                    for dr in self._domain_results.values():
                        for dk in dr.get("kpis", []):
                            if dk.get("id") == kpi.id:
                                domain_findings = dk
                                break
                    persona_kpi_data.append({
                        "name":              kpi.name,
                        "description":       kpi.description,
                        "layer":             kpi.layer,
                        "value":             kpi.l1.value if kpi.l1 else None,
                        "unit":              kpi.l1.unit  if kpi.l1 else "",
                        "trend_direction":   kpi.trend_direction,
                        "trend_pct":         kpi.trend_pct,
                        "trend_description": domain_findings.get("trend_description"),
                        "anomaly":           domain_findings.get("anomaly"),
                        "key_insight":       kpi.explanation.key_insight if kpi.explanation else None,
                        "risk":              kpi.explanation.risk        if kpi.explanation else None,
                    })

            # Stash kpi_data alongside the persona for the parallel summary pass
            _pending_summaries.append((
                p_raw["role"],
                p_raw.get("focus_areas", []),
                emit.get("objective", ""),
                persona_kpi_data,
            ))

            persona_views.append(PersonaView(
                persona            = persona,
                summary_cards      = [],   # filled after parallel summary run below
                dashboard_sections = sections,
            ))

        if not persona_views:
            log.error("No valid persona views assembled — check orchestrator emit")
            raise RuntimeError("Orchestrator produced no valid persona views")

        # ── Run all summary agents in parallel ────────────────────────────────
        # Previously sequential (one per persona). Now all fire at once.
        from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed

        def _run_summary(idx: int, role: str, focus_areas: list, objective: str, kpis: list) -> tuple[int, dict]:
            log.info("Running SummaryAgent for persona '%s' with %d KPIs", role, len(kpis))
            try:
                result = SummaryAgent().generate(
                    persona_role       = role,
                    focus_areas        = focus_areas,
                    business_objective = objective,
                    kpis               = kpis,
                )
            except Exception as exc:
                log.warning("SummaryAgent failed for '%s': %s — using empty cards", role, exc)
                result = {"cards": [], "action_items": []}
            return idx, result

        with ThreadPoolExecutor(max_workers=len(_pending_summaries) or 1) as _pool:
            _futures = {
                _pool.submit(_run_summary, i, role, fa, obj, kd): i
                for i, (role, fa, obj, kd) in enumerate(_pending_summaries)
            }
            for _fut in _as_completed(_futures):
                idx, summary_result = _fut.result()
                from schemas.config import ActionItem, KpiDrivers
                persona_views[idx].summary_cards = [
                    SummaryCard(
                        title  = c.get("title", "Summary"),
                        body   = c.get("body", ""),
                        signal = c.get("signal", "neutral"),
                    )
                    for c in summary_result.get("cards", [])
                ]
                persona_views[idx].action_items = [
                    ActionItem(
                        kpi_name = a.get("kpi_name", ""),
                        action   = a.get("action", ""),
                        signal   = a.get("signal", "stable"),
                    )
                    for a in summary_result.get("action_items", [])
                ]
                persona_views[idx].kpi_drivers = [
                    KpiDrivers(
                        kpi_name = d.get("kpi_name", ""),
                        drivers  = d.get("drivers", []),
                    )
                    for d in summary_result.get("kpi_drivers", [])
                ]

        return IntelligenceConfig(
            generated_at = now,
            workbook     = wb_meta,
            objective    = emit["objective"],
            personas     = persona_views,
        )
