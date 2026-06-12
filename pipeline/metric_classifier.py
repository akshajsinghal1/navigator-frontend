"""
pipeline/metric_classifier.py
──────────────────────────────
Lock each KPI's metric kind (l2_projection.method + aggregation) from data
signals before normalize_config() repairs symptoms.

Runs immediately after the orchestrator assembles the config, using full
view_data_cache rows when available (not the 20-row raw_data sample).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pipeline.metric_contract import (
    MetricKind,
    _norm,
    bucket_series,
    column_is_numeric,
    compute_l1_value,
    find_column,
    find_date_column,
    is_cumulative_series,
    is_hourly_dates,
)
from pipeline.view_rows import rows_for_kpi

if TYPE_CHECKING:
    from schemas.config import IntelligenceConfig, KPI

log = logging.getLogger(__name__)

_RATE_KWS = (
    "rate", "ratio", "percent", "pct", "%", "utilization", "utilisation",
    "occupancy", "productivity", "margin", "yield", "efficiency",
)
_SNAPSHOT_KWS = (
    "current", "now", "on hand", "beds available", "queue depth", "backlog",
    "headcount", "staff count", "rn count", "census", "capacity",
)
_FLOW_KWS = (
    "volume", "referral", "admission", "discharge", "throughput", "turnaround",
    "hours", "wait time",
)
_COUNT_KWS = ("count", "total", "number of")


def _unit_implies_rate(unit: str | None) -> bool:
    u = (unit or "").strip()
    return u == "%" or "percent" in u.lower()


def _name_has_any(name_n: str, kws: tuple[str, ...]) -> bool:
    return any(kw in name_n for kw in kws)


_TEMPORAL_NAME_KWS = ("date", "time", "timestamp", "day", "month", "hour", "created", "period")


def _is_temporal_kpi(kpi: KPI, rows: list[dict] | None) -> bool:
    if kpi.chart.x_axis_type == "temporal":
        return True
    # Only count x_axis as temporal when the column name itself looks like a date field.
    # find_date_column with an arbitrary hint just checks column existence, so passing
    # a categorical field like "Region" or "Category" would incorrectly return True.
    if rows and kpi.chart.x_axis:
        col = find_column(rows, kpi.chart.x_axis)
        if col and any(kw in col.lower() for kw in _TEMPORAL_NAME_KWS):
            return True
    if kpi.l2_projection and kpi.l2_projection.date_field:
        if rows and find_date_column(rows, kpi.l2_projection.date_field):
            return True
        if kpi.chart.x_axis_type == "temporal":
            return True
    return False


def _detect_cumulative_flow(rows: list[dict], kpi: KPI) -> bool:
    l2 = kpi.l2_projection
    value_col = find_column(
        rows,
        (l2.value_field if l2 else None) or (kpi.l1.field_name if kpi.l1 else None) or kpi.chart.y_axis,
    )
    date_col = find_date_column(
        rows,
        l2.date_field if l2 else None,
        kpi.chart.x_axis,
    )
    if not value_col or not date_col:
        return False
    series = bucket_series(rows, value_col, date_col, "sum")
    if len(series) < 3:
        return False
    return is_cumulative_series([v for _, v in series])


def classify_metric_kind(kpi: KPI, rows: list[dict] | None) -> MetricKind:
    """
    Derive metric kind from KPI name, units, chart shape, and row shape.
    Does not mutate the KPI.
    """
    name_n = _norm(kpi.name)
    l2 = kpi.l2_projection
    agent_method = l2.method if l2 else None
    chart_agg = (kpi.chart.aggregation or "").lower()

    if chart_agg == "count":
        return "snapshot"
    if " vs " in name_n and _name_has_any(name_n, ("hour", "overtime", "agency")):
        return "snapshot"

    if _unit_implies_rate(kpi.l1.unit if kpi.l1 else None):
        return "rate"
    if _name_has_any(name_n, _RATE_KWS) and "trend" not in name_n:
        return "rate"
    if agent_method == "ratio":
        return "rate"
    if agent_method == "stable":
        return "snapshot"  # legacy configs — stripped before emit

    # Headcount / staffing counts over time are snapshots (latest period), not daily flows
    if _name_has_any(name_n, _SNAPSHOT_KWS):
        return "snapshot"
    if "count" in name_n and ("rn" in name_n or "staff" in name_n or "nurse" in name_n):
        return "snapshot"
    # Queue depth / holds are point-in-time levels, not daily flow volumes
    if _name_has_any(name_n, ("hold", "pending transfer", "queue depth", "backlog")):
        return "snapshot"

    if rows and _is_temporal_kpi(kpi, rows):
        if _detect_cumulative_flow(rows, kpi):
            return "accumulator"
        if _name_has_any(name_n, _FLOW_KWS):
            return "accumulator"
        if agent_method in ("daily_rate", "growth_rate"):
            return "accumulator"
        # Generic temporal line/area without flow keywords → snapshot (latest bucket)
        if kpi.chart.type in ("line_chart", "area_chart", "stacked_area_chart"):
            y_col = find_column(rows, kpi.chart.y_axis) if kpi.chart.y_axis else None
            if y_col and column_is_numeric(rows, y_col):
                return "snapshot"

    if agent_method == "daily_rate":
        return "accumulator"
    if agent_method == "growth_rate":
        return "accumulator"

    if agent_method in ("daily_rate", "growth_rate"):
        return "accumulator"
    if agent_method == "ratio":
        return "rate"
    return "accumulator"


def _method_for_kind(kind: MetricKind, *, cumulative: bool = False) -> str:
    if kind == "rate":
        return "ratio"
    if kind == "snapshot":
        raise ValueError("snapshot KPIs have no l2_projection method")
    if cumulative:
        return "daily_rate"
    return "daily_rate"


def _aggregation_for_kind(kind: MetricKind, kpi: KPI) -> str:
    if kind == "rate":
        return "avg"
    if kind == "snapshot":
        name_n = _norm(kpi.name)
        field_n = _norm(kpi.l1.field_name if kpi.l1 else "")
        if "count" in name_n or "count" in field_n or "headcount" in name_n:
            return "sum"
        chart_agg = (kpi.chart.aggregation or "").lower()
        if chart_agg in ("count", "max", "min", "sum"):
            return chart_agg
        return "avg"
    # accumulator / flow
    return "sum"


def classify_kpi(kpi: KPI, view_cache: dict[str, list[dict]]) -> list[str]:
    """
    Lock l2_projection.method and aggregation from classifier rules.
    Returns human-readable change messages.
    """
    changes: list[str] = []
    rows, _ = rows_for_kpi(kpi, view_cache)
    rows = rows or None
    name = kpi.name
    name_n = _norm(name)

    from pipeline.metric_contract import strip_legacy_stable_projection

    if strip_legacy_stable_projection(kpi):
        changes.append(f"{name}: removed deprecated stable l2_projection")

    ch = kpi.chart
    kind = classify_metric_kind(kpi, rows)

    if kind == "snapshot":
        if kpi.l2_projection is not None:
            kpi.l2_projection = None
            changes.append(f"{name}: cleared l2_projection (snapshot KPI)")
    else:
        if not kpi.l2_projection:
            from schemas.config import L2Projection
            vf = (kpi.l1.field_name if kpi.l1 else "") or kpi.chart.y_axis or ""
            kpi.l2_projection = L2Projection(
                method="ratio",
                value_field=vf,
                aggregation="sum",
                date_field=ch.x_axis if ch.x_axis_type == "temporal" else None,
            )
            changes.append(f"{name}: created missing l2_projection")

        l2 = kpi.l2_projection
        assert l2 is not None
        cumulative = bool(rows and kind == "accumulator" and _detect_cumulative_flow(rows, kpi))
        target_method = _method_for_kind(kind, cumulative=cumulative)
        target_agg = _aggregation_for_kind(kind, kpi)

        if (ch.aggregation or "").lower() == "count" and kind == "accumulator":
            target_agg = "count"

        if l2.method != target_method:
            old = l2.method
            l2.method = target_method  # type: ignore[assignment]
            changes.append(f"{name}: method {old} → {target_method} ({kind})")

        if l2.aggregation != target_agg:
            old = l2.aggregation
            l2.aggregation = target_agg  # type: ignore[assignment]
            changes.append(f"{name}: aggregation {old} → {target_agg}")

    target_agg = _aggregation_for_kind(kind, kpi)
    if (ch.aggregation or "").lower() == "count" and kind == "accumulator":
        target_agg = "count"

    l2 = kpi.l2_projection

    categorical_bar = (
        ch.type in ("horizontal_bar_chart", "bar_chart")
        and ch.x_axis_type == "categorical"
    )
    chart_agg = (ch.aggregation or "").lower()
    y_col = find_column(rows, ch.y_axis) if rows and ch.y_axis else None
    y_n = _norm(y_col) if y_col else ""
    row_count_field = y_n in ("referral id", "referral_id") and chart_agg == "count"
    if not categorical_bar and chart_agg != target_agg and not row_count_field:
        old = ch.aggregation
        ch.aggregation = target_agg  # type: ignore[assignment]
        changes.append(f"{name}: chart.aggregation {old} → {target_agg}")

    # Snapshot queue metrics over time: max per bucket, not sum
    if (
        rows
        and _is_temporal_kpi(kpi, rows)
        and _name_has_any(name_n, ("hold", "pending transfer", "queue depth"))
        and (ch.aggregation or "").lower() == "sum"
    ):
        ch.aggregation = "max"
        changes.append(f"{name}: chart.aggregation sum → max (snapshot level)")

    if l2:
        chart_agg = (ch.aggregation or "").lower()
        if chart_agg == "max" and l2.aggregation != "max":
            l2.aggregation = "max"  # type: ignore[assignment]
            changes.append(f"{name}: l2.aggregation → max (sync chart)")

        if not _is_temporal_kpi(kpi, rows) and l2.date_field:
            l2.date_field = None
            changes.append(f"{name}: cleared l2 date_field (non-temporal chart)")

        # value_field from data when agent picked a non-existent column
        if rows:
            vf = find_column(rows, l2.value_field)
            if not vf:
                for hint in (kpi.l1.field_name if kpi.l1 else None, kpi.chart.y_axis):
                    col = find_column(rows, hint)
                    if col and column_is_numeric(rows, col):
                        l2.value_field = col
                        changes.append(f"{name}: value_field → '{col}'")
                        break

        # date_field for temporal charts — only pass x_axis as a hint when it's
        # actually temporal; categorical fields (Region, Category) would otherwise
        # be picked up by find_date_column's hint-existence check.
        if rows and _is_temporal_kpi(kpi, rows):
            x_hint = ch.x_axis if ch.x_axis_type == "temporal" else None
            dc = find_date_column(rows, l2.date_field, x_hint)
            if dc and l2.date_field != dc:
                l2.date_field = dc
                changes.append(f"{name}: date_field → '{dc}'")

    return changes


def apply_l1_from_cache(kpi: KPI, view_cache: dict[str, list[dict]]) -> list[str]:
    """Recompute L1 from full cache rows when possible."""
    changes: list[str] = []
    rows, source = rows_for_kpi(kpi, view_cache)
    if not rows or not kpi.l1:
        return changes

    if source == "sample":
        log.warning(
            "%s: L1 compute using 20-row sample — view_name %r not in cache",
            kpi.name,
            kpi.l1.view_name,
        )

    live = compute_l1_value(kpi, rows)
    if live is None:
        return changes

    try:
        cfg_v = float(kpi.l1.value) if kpi.l1.value is not None else None
    except (TypeError, ValueError):
        cfg_v = None

    precision = 2 if (kpi.l1.unit or "").strip() != "%" else 4
    new_v = round(live, precision)
    src_tag = "cache" if source == "cache" else "sample"

    if cfg_v is None or abs(cfg_v - new_v) > max(abs(cfg_v) * 0.02, 0.5):
        old = kpi.l1.value
        kpi.l1.value = new_v
        changes.append(f"{kpi.name}: L1 {old} → {new_v} ({src_tag})")

    return changes


def classify_config(
    config: IntelligenceConfig,
    view_cache: dict[str, list[dict]] | None = None,
) -> list[str]:
    """
    Classify all KPIs and refresh L1 from view_data_cache.
    Returns all change messages.
    """
    cache = view_cache or {}
    all_changes: list[str] = []

    for pv in config.personas:
        for sec in pv.dashboard_sections:
            for kpi in sec.kpis:
                all_changes.extend(classify_kpi(kpi, cache))
                all_changes.extend(apply_l1_from_cache(kpi, cache))

    if all_changes:
        log.info("Metric classifier: %d adjustment(s)", len(all_changes))
        for msg in all_changes:
            log.info("  • %s", msg)

    return all_changes
