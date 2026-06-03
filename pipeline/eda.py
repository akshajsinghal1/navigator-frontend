"""
pipeline/eda.py
───────────────
Pre-orchestrator Exploratory Data Analysis (EDA) — pure Python, zero AI cost.

Takes the semantic-filtered inventory and produces a structured analysis that
gets injected into the orchestrator's context. This gives the orchestrator
pre-computed insight rather than making it parse raw JSON from scratch.

What it produces
────────────────
  measures         : MEASURE fields — highest-priority KPI candidates
  calculated_kpis  : CalculatedField entries, ranked by KPI likelihood
  dimensions       : DIMENSION fields available for chart axes / breakdowns
  time_fields      : DATE/DATETIME fields (signals line/area chart suitability)
  geographic_fields: State/Country/City/Region fields (signals map chart)
  parameters       : parameters + what calculations they feed (L2 signal)
  views            : available Tableau sheets for data fetching
  domain_clusters  : fields pre-grouped by likely business domain

None of this requires an LLM — it's deterministic analysis of metadata.
"""

from __future__ import annotations

import re
from typing import Any


# ── Domain keyword vocabulary ─────────────────────────────────────────────────
# Used to assign fields to domain clusters.
# Covers retail, SaaS, healthcare, finance, manufacturing, logistics, HR, etc.
_DOMAIN_VOCAB: list[tuple[str, list[str]]] = [
    # Universal business
    ("Sales & Revenue",         ["sales", "revenue", "order", "quantity", "amount",
                                  "discount", "booking", "invoice", "billing", "deal",
                                  "opportunity", "pipeline", "contract", "mrr", "arr",
                                  "subscription", "recurring"]),
    ("Profit & Margin",         ["profit", "margin", "cost", "loss", "income", "gross",
                                  "net", "ebitda", "ebit", "operating income", "cogs",
                                  "expense", "overhead", "budget variance"]),
    ("Customer & Segment",      ["customer", "segment", "client", "account", "consumer",
                                  "churn", "retention", "ltv", "cac", "nps", "csat",
                                  "satisfaction", "loyalty", "acquisition"]),
    ("Shipping & Fulfillment",  ["ship", "delivery", "freight", "logistic", "transit",
                                  "days to", "lead time", "fulfillment", "dispatch",
                                  "carrier", "warehouse", "inventory", "stock", "sku"]),
    ("Product & Category",      ["product", "category", "sub-category", "sku", "item",
                                  "brand", "catalog", "listing", "variant", "model",
                                  "part", "component"]),
    ("Finance & Accounting",    ["asset", "liability", "equity", "interest", "loan",
                                  "portfolio", "fund", "investment", "return", "yield",
                                  "risk", "exposure", "capital", "aum", "nav", "balance",
                                  "cash flow", "debt", "credit", "accounts receivable",
                                  "accounts payable", "dso", "dpo", "working capital"]),
    ("Healthcare & Clinical",   ["patient", "admission", "discharge", "clinical",
                                  "diagnosis", "treatment", "readmission", "hospital",
                                  "medical", "visit", "encounter", "procedure", "bed",
                                  "census", "length of stay", "los", "mortality",
                                  "complication", "provider", "payer", "claim"]),
    ("Manufacturing & Operations", ["production", "defect", "yield", "downtime",
                                  "throughput", "oee", "cycle time", "quality",
                                  "machine", "equipment", "scrap", "rework", "capacity",
                                  "utilization", "efficiency", "shift", "line",
                                  "unit produced", "batch"]),
    ("Human Resources",         ["employee", "headcount", "turnover", "hire", "attrition",
                                  "engagement", "tenure", "absence", "absenteeism",
                                  "commission", "salary", "rep", "quota", "bonus", "hr",
                                  "recruitment", "onboarding", "performance review"]),
    ("Marketing & Acquisition", ["marketing", "campaign", "lead", "conversion",
                                  "impression", "click", "ctr", "cpm", "roas", "spend",
                                  "channel", "attribution", "funnel", "awareness",
                                  "engagement rate", "reach", "open rate"]),
    ("Forecast & Planning",     ["forecast", "estimate", "budget", "target", "plan",
                                  "growth", "projection", "outlook", "guidance"]),
    ("Geographic",              ["region", "state", "country", "city", "territory",
                                  "market", "geo", "district", "zone", "branch"]),
    ("Time & Period",           ["date", "year", "month", "quarter", "period",
                                  "week", "day", "fiscal", "ytd", "mtd", "qtd"]),
]

_GEO_KEYWORDS  = {"state", "country", "city", "territory", "region", "lat", "lon", "zip", "postal"}
_TIME_KEYWORDS = {"date", "time", "year", "month", "quarter", "week", "day", "period"}

# NOTE: No domain-specific keyword lists here.
# EDA is fully generic — it presents raw field structure.
# Agents discover what's valuable by running pandas analysis on real data.


def _lower(s: str) -> str:
    return s.lower()


def _field_matches_domain(field_name: str, keywords: list[str]) -> bool:
    fl = _lower(field_name)
    return any(kw in fl for kw in keywords)


def _is_geo_field(name: str, data_type: str | None) -> bool:
    fl = _lower(name)
    return any(kw in fl for kw in _GEO_KEYWORDS)


def _is_time_field(name: str, data_type: str | None) -> bool:
    fl = _lower(name)
    dt = _lower(data_type or "")
    return "date" in dt or "datetime" in dt or any(kw in fl for kw in _TIME_KEYWORDS)


def _get_all_dimension_fields(all_fields: list[dict]) -> list[dict]:
    """
    Return all DIMENSION fields — these are potential breakdown candidates.
    The orchestrator/domain agents decide which are worth using by checking
    actual data cardinality with run_analysis (e.g. df['Field'].nunique()).
    No keyword filtering — generic for any workbook.
    """
    return [f for f in all_fields if f.get("role") == "DIMENSION"]


def _get_all_measure_fields(all_fields: list[dict]) -> list[dict]:
    """
    Return all MEASURE and CalculatedField entries — these are potential KPI values.
    No keyword filtering — agents discover what's important from real data.
    """
    return [
        f for f in all_fields
        if f.get("role") == "MEASURE" or f.get("type") == "CalculatedField"
    ]


def _score_kpi_likelihood(field: dict) -> int:
    """
    Score how likely a field is to be a meaningful KPI.
    Higher = more likely to be a KPI the orchestrator should surface.
    """
    score = 0
    name    = field.get("name", "")
    formula = field.get("formula", "")
    ftype   = field.get("type", "")
    role    = field.get("role", "")

    if role == "MEASURE":
        score += 3
    if ftype == "CalculatedField":
        score += 2
    # Has a formula with arithmetic (real computation, not just alias)
    if formula and any(op in formula for op in ["+", "-", "*", "/"]):
        score += 2
    # References a parameter (L2 eligible)
    if formula and re.search(r"\[[\w\s]+\]", formula):
        score += 1
    # Name suggests a KPI (ratio, total, rate, count, avg, sum)
    kpi_words = ["total", "rate", "ratio", "profit", "sales", "revenue",
                 "forecast", "estimate", "avg", "average", "count", "growth"]
    if any(w in _lower(name) for w in kpi_words):
        score += 1
    # Penalise utility/sort/label fields
    utility_words = ["sort", "label", "tooltip", "rank", "helper", "index"]
    if any(w in _lower(name) for w in utility_words):
        score -= 5
    return score


def run_eda(filtered_inventory: dict[str, Any]) -> dict[str, Any]:
    """
    Analyse the filtered inventory and return a structured EDA dict.

    Args:
        filtered_inventory: output of semantic_filter.filter_inventory()

    Returns:
        EDA dict — injected into the orchestrator's user message.
    """
    measures:          list[dict] = []
    calculated_fields: list[dict] = []
    dimensions:        list[dict] = []
    time_fields:       list[str]  = []
    geo_fields:        list[str]  = []
    all_fields:        list[dict] = []

    for ds in filtered_inventory.get("embedded_datasources", []):
        ds_name = ds.get("name", "?")
        for f in ds.get("fields", []):
            fname    = f.get("name", "")
            ftype    = f.get("type", "ColumnField")
            role     = f.get("role", "")
            data_type= f.get("dataType", "")
            formula  = f.get("formula")
            desc     = f.get("description", "")

            entry = {
                "name":        fname,
                "type":        ftype,
                "dataType":    data_type,
                "role":        role,
                "datasource":  ds_name,
                "formula":     formula,
                "description": desc,
                "kpi_score":   _score_kpi_likelihood(f),
            }
            all_fields.append(entry)

            if _is_time_field(fname, data_type) and fname not in time_fields:
                time_fields.append(fname)
            if _is_geo_field(fname, data_type) and fname not in geo_fields:
                geo_fields.append(fname)

            if ftype == "CalculatedField":
                calculated_fields.append(entry)
            elif role == "MEASURE":
                measures.append(entry)
            elif role == "DIMENSION":
                dimensions.append(entry)

    # Sort calculated fields by KPI score descending
    calculated_fields.sort(key=lambda x: x["kpi_score"], reverse=True)
    measures.sort(key=lambda x: x["kpi_score"], reverse=True)

    # ── Parameters ────────────────────────────────────────────────────────────
    parameters = []
    for p in filtered_inventory.get("parameters", []):
        name      = p.get("name", "")
        used_in   = p.get("used_in_calculations", [])
        # Find which calculated KPIs reference this parameter
        affected_kpis = [
            f["name"] for f in calculated_fields
            if f.get("formula") and f"[{name}]" in f["formula"]
        ]
        parameters.append({
            "name":          name,
            "used_in":       used_in,
            "affects_kpis":  affected_kpis,
            "l2_eligible":   bool(affected_kpis),
        })

    # ── Domain clustering ─────────────────────────────────────────────────────
    domain_clusters: list[dict] = []
    assigned: set[str] = set()

    for domain_name, keywords in _DOMAIN_VOCAB:
        members = [
            f["name"] for f in all_fields
            if _field_matches_domain(f["name"], keywords) and f["name"] not in assigned
        ]
        if members:
            # Mark as assigned so fields don't appear in multiple clusters
            assigned.update(members)
            # KPI candidates = calculated or measure fields in this cluster
            kpi_candidates = [
                f["name"] for f in all_fields
                if f["name"] in members and (f["type"] == "CalculatedField" or f["role"] == "MEASURE")
            ]
            # Relevant views: sheet names whose name contains any cluster keyword
            relevant_sheets = [
                s for s in filtered_inventory.get("sheets", [])
                if _field_matches_domain(s, keywords)
            ]
            domain_clusters.append({
                "domain":          domain_name,
                "fields":          members,
                "kpi_candidates":  kpi_candidates,
                "relevant_sheets": relevant_sheets,
            })

    # Anything unassigned goes to "Other"
    unassigned = [f["name"] for f in all_fields if f["name"] not in assigned]
    if unassigned:
        domain_clusters.append({
            "domain":         "Other",
            "fields":         unassigned,
            "kpi_candidates": [],
            "relevant_sheets": [],
        })

    # ── Sheets (views) available ──────────────────────────────────────────────
    sheets = filtered_inventory.get("sheets", [])

    # ── Top KPI candidates (global ranking) ──────────────────────────────────
    top_kpi_candidates = sorted(
        [f for f in all_fields if f["kpi_score"] >= 3],
        key=lambda x: x["kpi_score"],
        reverse=True,
    )[:20]

    # ── L2 summary ───────────────────────────────────────────────────────────
    l2_eligible = [p for p in parameters if p["l2_eligible"]]

    # ── Raw structural inventory (generic, no domain assumptions) ────────────
    # Agents use run_analysis to discover what's actually important in the data.
    # We present ALL fields and let real data tell the story.
    all_dim_fields     = _get_all_dimension_fields(all_fields)
    all_measure_fields = _get_all_measure_fields(all_fields)

    return {
        "summary": {
            "total_fields":       len(all_fields),
            "measures":           len(measures),
            "calculated_fields":  len(calculated_fields),
            "dimensions":         len(dimensions),
            "time_fields":        time_fields,
            "geographic_fields":  geo_fields,
            "parameters":         len(parameters),
            "views_available":    len(sheets),
        },
        "top_kpi_candidates": [
            {
                "name":      f["name"],
                "type":      f["type"],
                "role":      f["role"],
                "formula":   f["formula"],
                "kpi_score": f["kpi_score"],
            }
            for f in top_kpi_candidates
        ],
        "parameters": parameters,
        "l2_eligible_params": l2_eligible,
        "domain_clusters": domain_clusters,
        # Full dimension list — agents check cardinality with run_analysis
        "all_dimensions":  [{"name": f["name"], "dataType": f["dataType"]}
                            for f in all_dim_fields],
        # Full measure list — agents verify values with run_analysis
        "all_measures":    [{"name": f["name"], "type": f["type"], "formula": f.get("formula")}
                            for f in all_measure_fields],
        "time_fields":      time_fields,
        "geographic_fields": geo_fields,
        "sheets":           sheets,
    }


def format_eda_for_agent(eda: dict[str, Any]) -> str:
    """
    Render the EDA dict as a compact, readable text block for the agent prompt.
    This is injected into the orchestrator's user message to give it pre-computed context.
    """
    lines: list[str] = ["=== PRE-ANALYSIS (EDA) ==="]

    s = eda["summary"]
    lines.append(
        f"\nWorkbook contains {s['total_fields']} fields: "
        f"{s['measures']} measures, {s['calculated_fields']} calculated, "
        f"{s['dimensions']} dimensions. "
        f"{s['views_available']} Tableau sheets available for data fetching."
    )

    if s["time_fields"]:
        lines.append(f"Time fields (for trend analysis): {', '.join(s['time_fields'])}")
    if s["geographic_fields"]:
        lines.append(f"Geographic fields (for map charts): {', '.join(s['geographic_fields'])}")

    # Top KPI candidates
    lines.append("\nTOP KPI CANDIDATES (by score):")
    for f in eda["top_kpi_candidates"]:
        formula_hint = f" | formula: {f['formula'][:80]}{'…' if f['formula'] and len(f['formula']) > 80 else ''}" if f["formula"] else ""
        lines.append(f"  [{f['kpi_score']:+d}] {f['name']} ({f['type']}, {f['role']}){formula_hint}")

    # Parameters + L2
    if eda["parameters"]:
        lines.append("\nPARAMETERS (L2 forecast levers):")
        for p in eda["parameters"]:
            l2_tag = " [L2 ELIGIBLE]" if p["l2_eligible"] else ""
            affects = f" -> affects: {', '.join(p['affects_kpis'])}" if p["affects_kpis"] else ""
            lines.append(f"  {p['name']}{affects}{l2_tag}")

    # Domain clusters
    lines.append("\nSUGGESTED DOMAIN CLUSTERS (from field name analysis):")
    for dc in eda["domain_clusters"]:
        if dc["domain"] == "Other" and not dc["kpi_candidates"]:
            continue
        kpis_str = ", ".join(dc["kpi_candidates"][:5]) if dc["kpi_candidates"] else "—"
        sheets_str = ", ".join(dc["relevant_sheets"][:4]) if dc["relevant_sheets"] else "—"
        lines.append(
            f"  {dc['domain']}: "
            f"KPIs → {kpis_str} | "
            f"sheets → {sheets_str}"
        )

    # All dimensions — agents check cardinality with run_analysis to find breakdowns
    all_dims = eda.get("all_dimensions", [])
    if all_dims:
        dim_names = [d["name"] for d in all_dims[:20]]
        lines.append(f"\nALL DIMENSIONS ({len(all_dims)} total — check cardinality with run_analysis):")
        lines.append(f"  {', '.join(dim_names)}")
        lines.append("  → Use: df['Field'].nunique() to find low-cardinality breakdowns")
        lines.append("  → Dimensions with 2-20 distinct values = strong breakdown candidates")

    # All measures — agents verify values with run_analysis
    all_meas = eda.get("all_measures", [])
    if all_meas:
        meas_names = [m["name"] for m in all_meas[:20]]
        lines.append(f"\nALL MEASURES ({len(all_meas)} total — verify with run_analysis):")
        lines.append(f"  {', '.join(meas_names)}")
        lines.append("  → Use: df['Field'].describe() to understand scale and distribution")

    lines.append("\n=== END PRE-ANALYSIS ===")
    return "\n".join(lines)
