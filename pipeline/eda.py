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

# ── Operational dimension keywords ────────────────────────────────────────────
# These fields should ALWAYS be considered as breakdown dimensions.
# If they exist in a workbook, at least one KPI per persona should use them.
_OPERATIONAL_DIM_GROUPS: dict[str, list[str]] = {
    "facility":    ["facility", "site", "location", "building", "campus", "branch"],
    "department":  ["department", "dept", "unit", "ward", "floor", "division",
                    "team", "cost center", "service line"],
    "shift":       ["shift", "shift_name", "schedule", "slot"],
    "region":      ["region", "zone", "territory", "district", "area", "geography",
                    "market", "sector"],
    "role":        ["role", "position", "job_title", "staff_type", "employee_type",
                    "classification", "rn", "cna", "therapist"],
    "status":      ["status", "state", "stage", "flag", "category", "type",
                    "priority", "risk"],
}

# ── Financial field keywords ──────────────────────────────────────────────────
# Fields containing these words are FINANCIALLY MATERIAL —
# must surface in at least one KPI, especially for CFO/executive personas.
_FINANCIAL_KEYWORDS = [
    "cost", "labor_cost", "labour", "revenue", "income", "budget",
    "spend", "expense", "wage", "salary", "pay", "compensation",
    "agency", "contract", "overtime", "premium", "billing", "charge",
    "reimbursement", "copay", "premium", "profit", "loss", "margin",
]

# ── View classification keywords ─────────────────────────────────────────────
_VIEW_TYPE_HINTS: dict[str, list[str]] = {
    "kpi_tile":      ["kpi", "tile", "card", "snapshot", "current", "today",
                      "now", "summary", "overview"],
    "trend_chart":   ["trend", "over time", "history", "timeline", "monthly",
                      "weekly", "daily", "forecast", "projection"],
    "breakdown":     ["by ", "breakdown", "distribution", "comparison",
                      "department", "facility", "region", "status", "category"],
    "heatmap":       ["heatmap", "heat map", "matrix", "grid", "calendar"],
    "risk_matrix":   ["risk", "matrix", "scatter", "bubble", "quadrant"],
}


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


def _detect_operational_dimensions(all_fields: list[dict]) -> dict[str, list[str]]:
    """
    Find fields that should be used as breakdown dimensions.
    Groups them by type: facility, department, shift, region, role, status.
    These should ALWAYS appear in at least one KPI per persona.
    """
    result: dict[str, list[str]] = {}
    for group, keywords in _OPERATIONAL_DIM_GROUPS.items():
        found = [
            f["name"] for f in all_fields
            if f.get("role") == "DIMENSION" and
               any(kw in _lower(f["name"]) for kw in keywords)
        ]
        if found:
            result[group] = found
    return result


def _detect_financial_fields(all_fields: list[dict]) -> list[str]:
    """
    Find fields that represent money/cost/revenue — must surface in at least
    one KPI, especially for finance/executive personas.
    """
    return [
        f["name"] for f in all_fields
        if any(kw in _lower(f["name"]) for kw in _FINANCIAL_KEYWORDS)
        and f.get("role") in ("MEASURE", "DIMENSION") or f.get("type") == "CalculatedField"
    ]


def _classify_views(sheets: list[str]) -> dict[str, list[str]]:
    """
    Classify Tableau views by purpose from their names.
    Helps orchestrator pick the right view for each KPI type.
    """
    classified: dict[str, list[str]] = {k: [] for k in _VIEW_TYPE_HINTS}
    classified["other"] = []

    for sheet in sheets:
        sl = _lower(sheet)
        assigned = False
        for view_type, hints in _VIEW_TYPE_HINTS.items():
            if any(h in sl for h in hints):
                classified[view_type].append(sheet)
                assigned = True
                break
        if not assigned:
            classified["other"].append(sheet)

    return {k: v for k, v in classified.items() if v}  # drop empty


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

    # ── Enhanced structural discovery ────────────────────────────────────────
    # These are passed to orchestrator so agents know WHAT exists before designing KPIs
    operational_dimensions = _detect_operational_dimensions(all_fields)
    financial_fields        = _detect_financial_fields(all_fields)
    view_classification     = _classify_views(sheets)

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
        "dimensions": [f["name"] for f in dimensions],
        "time_fields": time_fields,
        "geographic_fields": geo_fields,
        "sheets": sheets,
        # ── Enhanced structural maps ─────────────────────────────────────────
        "operational_dimensions": operational_dimensions,
        "financial_fields":       financial_fields,
        "view_classification":    view_classification,
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

    # Dimensions for breakdowns
    if eda["dimensions"]:
        lines.append(f"\nDIMENSIONS (available for chart breakdowns): {', '.join(eda['dimensions'][:15])}")

    # ── Enhanced structural maps ──────────────────────────────────────────────

    op_dims = eda.get("operational_dimensions", {})
    if op_dims:
        lines.append("\nOPERATIONAL DIMENSIONS — always use as breakdown candidates:")
        lines.append("  At least ONE KPI per persona MUST use these as x-axis or breakdown_by.")
        for dim_type, fields in op_dims.items():
            lines.append(f"  {dim_type.upper()}: {', '.join(fields[:5])}")

    fin_fields = eda.get("financial_fields", [])
    if fin_fields:
        lines.append("\nFINANCIAL FIELDS — must surface in at least one persona (especially CFO/executive):")
        lines.append(f"  {', '.join(fin_fields[:10])}")

    view_class = eda.get("view_classification", {})
    if view_class:
        lines.append("\nVIEW CLASSIFICATION — use the right view type for each KPI design:")
        if view_class.get("kpi_tile"):
            lines.append(f"  KPI tiles (single values):  {', '.join(view_class['kpi_tile'][:5])}")
        if view_class.get("trend_chart"):
            lines.append(f"  Trend charts (time-series): {', '.join(view_class['trend_chart'][:5])}")
        if view_class.get("breakdown"):
            lines.append(f"  Breakdown views (use for 'by X' KPIs): {', '.join(view_class['breakdown'][:5])}")
        if view_class.get("heatmap"):
            lines.append(f"  Heatmaps (dept × time grids): {', '.join(view_class['heatmap'][:5])}")
        if view_class.get("risk_matrix"):
            lines.append(f"  Risk matrices (scatter/rank): {', '.join(view_class['risk_matrix'][:5])}")

    lines.append("\n=== END PRE-ANALYSIS ===")
    return "\n".join(lines)
