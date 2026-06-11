"""
pipeline/chart_blueprint.py
──────────────────────────
Derive chart shape from KPI name + row schema + metric kind — not chart-agent prose.

Runs after metric_classifier, before normalize_config().
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from pipeline.metric_contract import (
    _categorical_columns,
    _name_implies_breakdown,
    _norm,
    _numeric_columns,
    column_is_numeric,
    find_column,
    find_date_column,
    metric_kind,
)
from pipeline.view_rows import rows_for_kpi

if TYPE_CHECKING:
    from schemas.config import IntelligenceConfig, KPI

log = logging.getLogger(__name__)

_NAME_DIM_PATTERNS = (
    r"\bby\s+([a-z][a-z0-9\s]{0,24}?)(?:\s+and\s+([a-z][a-z0-9\s]{0,24}?))?(?:\s*$|\s+and\b)",
)


def _breakdown_hints_from_name(name: str) -> list[str]:
    name_n = _norm(name)
    hints: list[str] = []
    m = re.search(r"\bby\s+([a-z][a-z0-9\s]{0,24}?)(?:\s+and\s+([a-z][a-z0-9\s]{0,24}?))?(?:\s*$)", name_n)
    if m:
        if m.group(1):
            hints.append(m.group(1).strip())
        if m.group(2):
            hints.append(m.group(2).strip())
    return hints


def _is_id_column(col: str) -> bool:
    cn = _norm(col)
    return cn.endswith(" id") or cn.endswith("_id") or cn in ("facility_id", "department_id")


def _name_column_for_id(rows: list[dict], id_col: str) -> str | None:
    """Map facility_id → Facility Name, department_id → Department Name, etc."""
    if not rows:
        return None
    base = re.sub(r"(_id| id)$", "", _norm(id_col)).strip()
    if not base:
        return None
    candidates: list[tuple[str, int]] = []
    for c in rows[0].keys():
        cn = _norm(c)
        if cn == id_col or column_is_numeric(rows, c):
            continue
        score = 0
        if base in cn and "name" in cn:
            score = 3
        elif cn == f"{base} name":
            score = 3
        elif cn.endswith(" name") and base in cn:
            score = 2
        elif base in cn:
            score = 1
        if score:
            dc = len({str(r.get(c, "")) for r in rows[:200] if r.get(c) not in (None, "", "(null)")})
            if dc >= 2:
                candidates.append((c, score * 1000 + dc))
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[1])
    return candidates[0][0]


def _resolve_measure(kpi: KPI, rows: list[dict]) -> str | None:
    """Pick the numeric measure column — l1.field_name wins over stale chart.y_axis."""
    name_n = _norm(kpi.name)
    hints: list[str] = []
    if kpi.l1 and kpi.l1.field_name:
        hints.append(kpi.l1.field_name)
    if kpi.l2_projection and kpi.l2_projection.value_field:
        hints.append(kpi.l2_projection.value_field)
    if kpi.chart.y_axis:
        hints.append(kpi.chart.y_axis)

    if "rn count" in name_n or "actual rn" in name_n:
        hints.extend(["actual_rn_count", "rn_count", "actual rn count"])
    if "referral" in name_n and "volume" in name_n:
        hints.extend(["referral_count", "referrals"])
    if "turnaround" in name_n:
        hints.extend(["turnaround_time", "avg_turnaround", "turnaround_hours"])
    if "occupancy" in name_n and "%" in (kpi.l1.unit if kpi.l1 else ""):
        hints.extend(["occupancy_pct", "occupancy %", "occupancy"])

    seen: set[str] = set()
    for hint in hints:
        if not hint or hint in seen:
            continue
        seen.add(hint)
        col = find_column(rows, hint)
        if col and column_is_numeric(rows, col):
            return col

    nums = _numeric_columns(rows)
    return nums[0] if nums else None


def prefer_label_column(rows: list[dict], hint: str | None) -> str | None:
    """Resolve a column hint; prefer human-readable labels over raw IDs."""
    if not rows or not hint:
        return None
    col = find_column(rows, hint)
    if not col:
        return None
    if _is_id_column(col):
        named = _name_column_for_id(rows, col)
        if named:
            return named
    return col


def _resolve_axis_labels(rows: list[dict], kpi: KPI) -> list[str]:
    changes: list[str] = []
    ch = kpi.chart
    name = kpi.name

    for attr in ("x_axis", "y_axis", "breakdown_by"):
        hint = getattr(ch, attr, None)
        if not hint:
            continue
        col = find_column(rows, hint)
        if not col:
            continue
        if _is_id_column(col):
            named = _name_column_for_id(rows, col)
            if named and named != col:
                setattr(ch, attr, named)
                changes.append(f"{name}: {attr} '{col}' → '{named}'")

    return changes


def _apply_heatmap_blueprint(kpi: KPI, rows: list[dict]) -> list[str]:
    changes: list[str] = []
    name_n = _norm(kpi.name)
    if "heatmap" not in name_n:
        return changes

    cats = _categorical_columns(rows, min_distinct=2, max_distinct=40)
    nums = _numeric_columns(rows)
    if len(cats) < 2:
        return changes

    hints = _breakdown_hints_from_name(kpi.name)
    x_col: str | None = None
    y_col: str | None = None

    for hint in hints:
        col = prefer_label_column(rows, hint)
        if col and not x_col:
            x_col = col
        elif col and col != x_col:
            y_col = col

    if not x_col:
        x_col = cats[0][0]
    if not y_col:
        y_col = next((c for c, _ in cats if c != x_col), cats[1][0] if len(cats) > 1 else None)

    if not x_col or not y_col:
        return changes

    ch = kpi.chart
    if ch.type != "heatmap_chart":
        ch.type = "heatmap_chart"
        changes.append(f"{kpi.name}: chart → heatmap_chart (name blueprint)")
    if ch.x_axis != x_col:
        ch.x_axis = x_col
        changes.append(f"{kpi.name}: heatmap x_axis → '{x_col}'")
    if ch.y_axis != y_col:
        ch.y_axis = y_col
        changes.append(f"{kpi.name}: heatmap y_axis → '{y_col}'")
    ch.x_axis_type = "categorical"
    ch.breakdown_by = None
    if nums and not ch.aggregation:
        ch.aggregation = "avg"
    return changes


def _apply_trend_blueprint(kpi: KPI, rows: list[dict]) -> list[str]:
    changes: list[str] = []
    name_n = _norm(kpi.name)
    ch = kpi.chart
    is_trend = (
        ch.x_axis_type == "temporal"
        or ch.type in ("line_chart", "area_chart", "stacked_area_chart")
        or "trend" in name_n
        or "over time" in name_n
    )
    if not is_trend or not rows:
        return changes

    date_col = find_date_column(
        rows,
        kpi.l2_projection.date_field if kpi.l2_projection else None,
        ch.x_axis,
    )
    if not date_col:
        return changes

    measure = _resolve_measure(kpi, rows)
    if not measure:
        return changes

    hints = _breakdown_hints_from_name(kpi.name)
    breakdown_col: str | None = None
    for hint in hints:
        col = prefer_label_column(rows, hint)
        if col and col not in (date_col, measure):
            breakdown_col = col
            break

    if ch.type in ("kpi_card", "gauge_chart", "bar_chart"):
        ch.type = "line_chart"
        changes.append(f"{kpi.name}: trend blueprint → line_chart")

    if ch.x_axis != date_col:
        ch.x_axis = date_col
        changes.append(f"{kpi.name}: x_axis → '{date_col}'")
    ch.x_axis_type = "temporal"

    if ch.y_axis != measure:
        ch.y_axis = measure
        changes.append(f"{kpi.name}: y_axis → '{measure}'")

    if breakdown_col and ch.breakdown_by != breakdown_col:
        ch.breakdown_by = breakdown_col
        changes.append(f"{kpi.name}: breakdown_by → '{breakdown_col}'")

    if kpi.l2_projection and kpi.l2_projection.date_field != date_col:
        kpi.l2_projection.date_field = date_col

    from pipeline.metric_contract import resolve_metric_kind

    kind = resolve_metric_kind(kpi, rows)
    if kind == "rate" and (ch.aggregation or "").lower() != "avg":
        ch.aggregation = "avg"
    elif kind == "snapshot" and not ch.aggregation:
        ch.aggregation = "sum"

    return changes


def _apply_breakdown_bar_blueprint(kpi: KPI, rows: list[dict]) -> list[str]:
    """KPIs named 'X by Department/Facility' on a scalar chart → ranked bar."""
    changes: list[str] = []
    if not _name_implies_breakdown(_norm(kpi.name)) or not rows:
        return changes

    ch = kpi.chart
    if ch.type in ("line_chart", "area_chart", "stacked_area_chart", "heatmap_chart"):
        return changes  # trend/heatmap blueprints own these

    hints = _breakdown_hints_from_name(kpi.name)
    dim_col: str | None = None
    for hint in hints:
        dim_col = prefer_label_column(rows, hint)
        if dim_col:
            break
    if not dim_col:
        cats = _categorical_columns(rows)
        dim_col = cats[0][0] if cats else None
    if not dim_col:
        return changes

    measure = _resolve_measure(kpi, rows)

    if ch.type in ("kpi_card", "gauge_chart") and measure:
        ch.type = "horizontal_bar_chart"
        ch.x_axis = dim_col
        ch.y_axis = measure
        ch.x_axis_type = "categorical"
        ch.aggregation = ch.aggregation or "avg"
        ch.sort_order = ch.sort_order or "desc"
        changes.append(f"{kpi.name}: breakdown blueprint → horizontal_bar_chart ({dim_col})")
        return changes

    # Categorical y + numeric-ish x (e.g. risk category on y, facility_id on x) → horizontal bar
    y_col = find_column(rows, ch.y_axis) if ch.y_axis else None
    x_col = find_column(rows, ch.x_axis) if ch.x_axis else None
    if (
        ch.type == "bar_chart"
        and y_col
        and x_col
        and not column_is_numeric(rows, y_col)
        and (column_is_numeric(rows, x_col) or _is_id_column(x_col))
    ):
        ch.type = "horizontal_bar_chart"
        ch.x_axis = prefer_label_column(rows, dim_col) or dim_col
        ch.y_axis = y_col
        ch.x_axis_type = "categorical"
        ch.aggregation = "count" if not column_is_numeric(rows, y_col) else (ch.aggregation or "avg")
        changes.append(f"{kpi.name}: categorical-y bar → horizontal_bar_chart")
        return changes

    if not ch.breakdown_by and dim_col and ch.type in ("line_chart", "bar_chart"):
        ch.breakdown_by = dim_col
        changes.append(f"{kpi.name}: breakdown_by → '{dim_col}'")

    return changes


def _apply_distribution_blueprint(kpi: KPI, rows: list[dict]) -> list[str]:
    """Risk/category distributions — categorical y + id x → horizontal count bar."""
    changes: list[str] = []
    name_n = _norm(kpi.name)
    if "distribution" not in name_n and "risk" not in name_n:
        return changes

    ch = kpi.chart
    if ch.type not in ("bar_chart", "kpi_card"):
        return changes

    y_col = find_column(rows, ch.y_axis) if ch.y_axis else None
    if not y_col or column_is_numeric(rows, y_col):
        return changes

    fac = prefer_label_column(rows, "facility") or find_column(rows, ch.x_axis)
    if not fac:
        return changes

    ch.type = "horizontal_bar_chart"
    ch.x_axis = fac
    ch.y_axis = y_col
    ch.x_axis_type = "categorical"
    ch.aggregation = "count"
    ch.sort_order = ch.sort_order or "desc"
    changes.append(f"{kpi.name}: distribution → horizontal_bar_chart (count by {fac})")
    return changes


def _apply_referral_volume_blueprint(kpi: KPI, rows: list[dict]) -> list[str]:
    """Referral volume trends: use referral_count + sum; keep row-count on referral_id."""
    changes: list[str] = []
    name_n = _norm(kpi.name)
    if "referral" not in name_n:
        return changes

    ch = kpi.chart
    if (ch.aggregation or "").lower() == "count":
        id_col = find_column(rows, "referral_id")
        if id_col and ch.y_axis != id_col:
            ch.y_axis = id_col
            if kpi.l2_projection:
                kpi.l2_projection.value_field = id_col
            changes.append(f"{kpi.name}: count chart y_axis → '{id_col}'")
        return changes  # row-count semantics — e.g. Escalated Referrals

    y_col = find_column(rows, ch.y_axis) if ch.y_axis else None
    y_n = _norm(y_col) if y_col else ""
    if y_n not in ("referral id", "referral_id"):
        return changes

    count_col = find_column(rows, "referral_count") or find_column(rows, "referrals")
    if not count_col:
        return changes

    ch.y_axis = count_col
    ch.aggregation = "sum"
    if kpi.l2_projection:
        kpi.l2_projection.value_field = count_col
        kpi.l2_projection.aggregation = "sum"
    changes.append(f"{kpi.name}: referral volume → '{count_col}' (sum)")
    return changes


def _apply_comparison_blueprint(kpi: KPI, rows: list[dict]) -> list[str]:
    """Side-by-side comparisons (staffed vs licensed) → ranked horizontal bar."""
    changes: list[str] = []
    name_n = _norm(kpi.name)
    if " vs " not in name_n and "versus" not in name_n:
        return changes

    ch = kpi.chart
    if ch.x_axis_type == "temporal" or "trend" in name_n or "hours" in name_n:
        return changes  # time-series (e.g. Agency vs Overtime Hours), not side-by-side bar
    if ch.breakdown_by:
        return changes
    if ch.type in ("line_chart", "area_chart", "stacked_area_chart"):
        return changes

    fac = prefer_label_column(rows, "facility") or find_column(rows, "facility_name")
    measure = _resolve_measure(kpi, rows)
    if not fac or not measure:
        return changes

    ch.type = "horizontal_bar_chart"
    ch.x_axis = fac
    ch.y_axis = measure
    ch.x_axis_type = "categorical"
    ch.breakdown_by = None
    ch.aggregation = ch.aggregation or "sum"
    ch.sort_order = ch.sort_order or "desc"
    changes.append(f"{kpi.name}: comparison → horizontal_bar_chart ({fac})")
    return changes


def _apply_risk_ranking_blueprint(kpi: KPI, rows: list[dict]) -> list[str]:
    """Risk / threshold facility lists → horizontal bar ranked by facility name."""
    changes: list[str] = []
    name_n = _norm(kpi.name)
    is_facility_risk = "facilities at" in name_n or (
        "risk" in name_n and ("facility" in name_n or "occupancy" in name_n)
    )
    if not is_facility_risk and "high occupancy" not in name_n:
        return changes

    ch = kpi.chart
    if ch.type == "horizontal_bar_chart" and ch.x_axis_type == "categorical":
        return changes

    fac = (
        prefer_label_column(rows, "facility")
        or find_column(rows, "FACILITY_NAME")
        or find_column(rows, "facility_name")
    )
    measure = _resolve_measure(kpi, rows) or find_column(rows, "PREDICTED_OCCUPANCY")
    if not fac or not measure:
        return changes

    ch.type = "horizontal_bar_chart"
    ch.x_axis = fac
    ch.y_axis = measure
    ch.x_axis_type = "categorical"
    ch.breakdown_by = None
    ch.aggregation = ch.aggregation or "avg"
    ch.sort_order = ch.sort_order or "desc"
    changes.append(f"{kpi.name}: risk ranking → horizontal_bar_chart")
    return changes


def _apply_composition_blueprint(kpi: KPI, rows: list[dict]) -> list[str]:
    """
    Composition / mix KPIs: each category on its own scale (e.g. 20–30 per status).
    Stacked area/bar sums categories and inflates the Y-axis misleadingly.
    """
    changes: list[str] = []
    name_n = _norm(kpi.name)
    ch = kpi.chart

    # "Total referrals trend" → one aggregate line, not stacked status layers
    if "total" in name_n and "trend" in name_n and ch.breakdown_by:
        ch.breakdown_by = None
        if ch.type in ("stacked_area_chart", "stacked_bar_chart"):
            ch.type = "line_chart"
        changes.append(f"{kpi.name}: total trend → single line (drop breakdown stack)")
        return changes

    if not any(kw in name_n for kw in ("mix", "composition", "share", "split", "by status")):
        return changes

    if ch.type in ("stacked_area_chart", "stacked_bar_chart") and ch.breakdown_by:
        ch.type = "line_chart"
        changes.append(f"{kpi.name}: composition → line_chart (unstacked breakdown)")

    return changes


def _apply_scalar_blueprint(kpi: KPI, rows: list[dict]) -> list[str]:
    """Single-number KPIs without temporal/breakdown intent → kpi_card or gauge."""
    changes: list[str] = []
    name_n = _norm(kpi.name)
    ch = kpi.chart

    if "facilities at" in name_n or ("risk" in name_n and "facility" in name_n):
        return changes
    if _name_implies_breakdown(name_n) or "trend" in name_n or "heatmap" in name_n:
        return changes
    if ch.x_axis_type == "temporal":
        return changes
    if ch.breakdown_by:
        return changes

    from pipeline.metric_contract import resolve_metric_kind

    kind = resolve_metric_kind(kpi, rows)
    if kind == "rate" or (kpi.l1 and kpi.l1.unit == "%"):
        if ch.type != "gauge_chart":
            ch.type = "gauge_chart"
            changes.append(f"{kpi.name}: rate scalar → gauge_chart")
        return changes

    if ch.type in ("bar_chart", "line_chart") and rows:
        date_col = find_date_column(rows, ch.x_axis)
        if not date_col and len(rows) <= 3:
            ch.type = "kpi_card"
            changes.append(f"{kpi.name}: sparse non-temporal → kpi_card")

    return changes


def apply_blueprint_kpi(kpi: KPI, view_cache: dict[str, list[dict]]) -> list[str]:
    """Apply all blueprint rules to one KPI. Returns change messages."""
    rows, source = rows_for_kpi(kpi, view_cache)
    if not rows:
        return []

    changes: list[str] = []
    changes.extend(_resolve_axis_labels(rows, kpi))
    changes.extend(_apply_heatmap_blueprint(kpi, rows))
    changes.extend(_apply_trend_blueprint(kpi, rows))
    changes.extend(_apply_breakdown_bar_blueprint(kpi, rows))
    changes.extend(_apply_distribution_blueprint(kpi, rows))
    changes.extend(_apply_referral_volume_blueprint(kpi, rows))
    changes.extend(_apply_comparison_blueprint(kpi, rows))
    changes.extend(_apply_risk_ranking_blueprint(kpi, rows))
    changes.extend(_apply_composition_blueprint(kpi, rows))
    changes.extend(_apply_scalar_blueprint(kpi, rows))

    if source == "sample" and changes:
        log.debug("%s: blueprint used 20-row sample (cache miss)", kpi.name)

    return changes


def apply_chart_blueprints(
    config: IntelligenceConfig,
    view_cache: dict[str, list[dict]] | None = None,
) -> list[str]:
    """Apply chart blueprints to every KPI in the config."""
    cache = view_cache or {}
    all_changes: list[str] = []

    for pv in config.personas:
        for sec in pv.dashboard_sections:
            for kpi in sec.kpis:
                all_changes.extend(apply_blueprint_kpi(kpi, cache))

    if all_changes:
        log.info("Chart blueprint: %d adjustment(s)", len(all_changes))
        for msg in all_changes:
            log.info("  • %s", msg)

    return all_changes
