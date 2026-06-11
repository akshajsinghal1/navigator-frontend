"""
schemas/config.py
─────────────────
Pydantic models for the Intelligence Config JSON — the final output of the
Navigator agent pipeline that drives the frontend dashboard.

This schema is the ONLY fixed contract between the agent pipeline and the
frontend. Everything inside is agent-derived; nothing is hardcoded.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

# ── Allowed chart types ──────────────────────────────────────────────────────
# These are the ONLY fixed boundary on chart selection.
# The agent picks from this list; the frontend renders accordingly.
CHART_TYPES = Literal[
    "kpi_card",            # single number + trend arrow
    "line_chart",          # time series / trend
    "bar_chart",           # categorical comparison
    "stacked_bar_chart",   # categorical with breakdown stacked
    "horizontal_bar_chart",# ranked list / long labels
    "area_chart",          # filled time series / cumulative
    "stacked_area_chart",  # part-of-whole over time
    "scatter_chart",       # correlation / two numeric measures
    "bubble_chart",        # correlation + size = third dimension
    "pie_chart",           # part-of-whole (≤6 slices)
    "donut_chart",         # part-of-whole with center metric
    "map_chart",           # geographic dimension
    "gauge_chart",         # single metric vs target / max
    "waterfall_chart",     # contribution / variance decomposition
    "funnel_chart",        # conversion pipeline / process stages
    "heatmap_chart",       # two categorical dimensions + intensity
    "treemap_chart",       # hierarchical part-of-whole
    "radar_chart",         # multi-dimensional comparison (spider)
    "table",               # raw tabular — last resort
]


# ── L1: Current state (fetched from Tableau) ────────────────────────────────
class L1Data(BaseModel):
    value: Optional[Union[float, int, str]] = None
    unit: Optional[str] = None         # "USD", "%", "days", "units", etc.
    format: str = "number"             # "currency" | "percentage" | "number" | "text"
    view_name: str                     # which Tableau view the value came from
    field_name: str                    # which field / measure


# ── L2: Deterministic forecast (computed from formulas + parameters) ─────────
class L2Data(BaseModel):
    formula: str                       # e.g. "[Sales]*(1-[Churn Rate])*(1+[New Business Growth])"
    parameters_used: List[str] = []    # parameter names involved
    forecast_value: Optional[float] = None
    method: Literal["formula_eval", "not_applicable"] = "formula_eval"
    error: Optional[str] = None        # if computation failed, reason here


# ── KPI value provenance (L1 headline semantics) ─────────────────────────────
# direct          — raw Hyper / Tableau column
# tableau_formula — workbook calculated field (Hyper formula or inventory eval)
# agent_derived   — agent-composed formula from 2+ fields (headline uses l2_derived)
KpiValueSource = Literal["direct", "tableau_formula", "agent_derived"]
ForecastLayer = Literal["l2_projection", "l3"]


class L2Derived(BaseModel):
    """Agent-composed deterministic KPI — headline value for 'now' when value_source=agent_derived."""
    formula: str
    input_fields: List[str] = Field(default_factory=list)
    value: Optional[float] = None
    unit: Optional[str] = None


# ── L2 Projection: how the frontend computes 7D/30D projections from rows ────
# The domain agent defines this per KPI during the pipeline.
# The frontend evaluates it on fresh Tableau rows at display time.
#
# method:
#   daily_rate   — cumulative totals (revenue, orders): sum / date_span * horizon_days
#   ratio        — percentages/rates (margin, on-time %): ratio stays constant
#   growth_rate  — trending metrics (customer count): compound growth extrapolation
# Snapshot / stock KPIs omit l2_projection entirely (L1 only — no forward rule).
class L2Projection(BaseModel):
    method: Literal["daily_rate", "ratio", "growth_rate"]
    value_field: str                   # exact column name for the metric value
    aggregation: Literal["sum", "avg", "count"] = "sum"
    date_field: Optional[str] = None   # date/time column for rate computation


# ── Chart specification ──────────────────────────────────────────────────────
class ChartSpec(BaseModel):
    type: CHART_TYPES
    x_axis: Optional[str] = None
    y_axis: Optional[str] = None
    x_axis_type: Optional[Literal["categorical", "temporal", "numeric"]] = None
    aggregation: Optional[Literal["sum", "avg", "count", "min", "max"]] = None
    sort_order: Optional[Literal["asc", "desc", "none"]] = None
    breakdown_by: Optional[str] = None              # e.g. "facility_id", "Segment"
    breakdown_labels: Optional[Dict[str, str]] = None  # maps raw key → display label
    color_by: Optional[str] = None
    sort_by: Optional[str] = None
    filters: List[str] = []
    notes: Optional[str] = None        # any rendering hint for the frontend


# ── AI-written summary card (3 per persona, shown at top of dashboard) ──────
class SummaryCard(BaseModel):
    title: str                                              # short card title, e.g. "Revenue Health"
    body: str                                               # 2-3 sentence AI-written summary
    signal: Literal["positive", "warning", "neutral"]      # drives accent colour in UI


# ── AI-generated action item (per persona, shown beside daily briefing) ──────
class ActionItem(BaseModel):
    kpi_name: str                                           # which KPI this action relates to
    action: str                                             # concrete 1-sentence action step
    signal: Literal["critical", "watch", "stable"]         # drives priority colouring in UI


# ── AI-generated KPI drivers (per persona, shown in KPI modal) ───────────────
class KpiDrivers(BaseModel):
    kpi_name: str                                           # exact KPI name
    drivers: List[str]                                      # 2-4 short data-grounded driver phrases


# ── KPI explanation (agent-generated) ───────────────────────────────────────
class Explanation(BaseModel):
    what: str                          # what this KPI measures, in plain language
    why_it_matters: str                # why it's relevant to the business objective
    trend: Optional[str] = None        # e.g. "Up 12% vs prior period"
    risk: Optional[str] = None         # risk flag / concern, if any
    key_insight: Optional[str] = None  # standout insight the agent found


# ── L3 TimesFM forecast (added post-pipeline) ───────────────────────────────
class L3Forecast(BaseModel):
    model:        str          = "timesfm-2.5-200m"
    horizon_days: int          = 30
    predictions:  List[float]  = Field(default_factory=list)   # point forecast per day
    lower_p10:    List[float]  = Field(default_factory=list)   # 10th percentile
    upper_p90:    List[float]  = Field(default_factory=list)   # 90th percentile
    generated_at: str          = ""


# ── Single KPI ───────────────────────────────────────────────────────────────
class KPI(BaseModel):
    id: str                            # snake_case slug, e.g. "total_sales"
    name: str                          # display name, e.g. "Total Sales"
    description: str                   # one-sentence description
    layer: Literal["L1", "L2", "L3"] = "L1"  # pipeline-computed headline layer tag (L3 = period overlay only)
    value_source: KpiValueSource = "direct"    # how the 'now' headline value is obtained
    l2_derived: Optional[L2Derived] = None     # set when value_source=agent_derived
    forecast_layers: List[ForecastLayer] = Field(
        default_factory=list,
        description="Which forward layers to render on 7D/30D (l2_projection + l3 together)",
    )
    priority: int = Field(default=50, ge=0, le=100)
    # 80-100 = critical for this persona today (risk, bad trend, anomaly)
    # 60-79  = important context (stable but core metric)
    # 40-59  = supplementary (useful but not primary)
    # 0-39   = background / informational
    l1: Optional[L1Data] = None
    l2: Optional[L2Data] = None
    l2_projection: Optional[L2Projection] = None  # agent-defined projection method for 7D/30D
    l3_forecast: Optional["L3Forecast"] = None    # TimesFM predictions (populated post-pipeline)
    l3_forecast_by_series: Optional[Dict[str, L3Forecast]] = None  # per breakdown value when chart.breakdown_by is set
    trend_direction: Optional[Literal["up", "down", "flat"]] = None
    trend_pct: Optional[float] = None  # % change vs prior period, e.g. 12.3
    chart: ChartSpec
    explanation: Explanation
    raw_data: List[Any] = Field(
        default_factory=list,
        description="Rows fetched from Tableau for this KPI (used by frontend to render chart)",
    )

    @model_validator(mode="before")
    @classmethod
    def _strip_deprecated_stable_projection(cls, data: Any) -> Any:
        if isinstance(data, dict):
            lp = data.get("l2_projection")
            if isinstance(lp, dict) and lp.get("method") == "stable":
                data["l2_projection"] = None
        return data


# ── Dashboard section (group of related KPIs) ────────────────────────────────
class DashboardSection(BaseModel):
    id: str                            # snake_case slug, e.g. "sales_performance"
    title: str                         # display title
    description: str                   # what this section covers
    kpis: List[KPI]


# ── Persona ──────────────────────────────────────────────────────────────────
class Persona(BaseModel):
    role: str                          # e.g. "Sales Operations Director"
    focus_areas: List[str]             # e.g. ["revenue", "commission", "forecasting"]
    rationale: str                     # why this persona was derived from the workbook
    persona_level: Literal["executive", "manager", "analyst"] = "manager"
    # executive = C-suite / VP / Director  → simplified view, fewer KPIs, big numbers
    # manager   = dept head / ops lead     → comprehensive, standard detail
    # analyst   = BI / data scientist      → full detail, all metadata


# ── Persona view — a persona + the dashboard sections relevant to it ──────────
class PersonaView(BaseModel):
    persona: Persona
    summary_cards: List[SummaryCard] = Field(   # exactly 3 AI-written summary cards
        default_factory=list,
        description="3 AI-written summary cards shown at the top of this persona's dashboard",
    )
    action_items: List[ActionItem] = Field(
        default_factory=list,
        description="Action items derived from KPI signals, shown beside the daily briefing",
    )
    kpi_drivers: List[KpiDrivers] = Field(
        default_factory=list,
        description="Per-KPI driver bullets shown in the KPI modal — refreshes with data",
    )
    dashboard_sections: List[DashboardSection]  # sections curated for this persona


# ── Workbook metadata ────────────────────────────────────────────────────────
class WorkbookMeta(BaseModel):
    name: str
    project: Optional[str] = None
    tableau_updated_at: Optional[str] = None
    data_sources: List[str] = []


# ── Root Intelligence Config ─────────────────────────────────────────────────
class IntelligenceConfig(BaseModel):
    version: str = "1.0"
    generated_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat() + "Z"
    )
    workbook: WorkbookMeta
    objective: str                     # single business objective, agent-derived
    personas: List[PersonaView]        # 2-4 personas, each with their own dashboard

    def to_json(self, **kwargs) -> str:
        """Serialize to JSON string."""
        return self.model_dump_json(indent=2, **kwargs)

    @classmethod
    def from_json(cls, json_str: str) -> "IntelligenceConfig":
        return cls.model_validate_json(json_str)
