"""
pipeline/kpi_layers.py
──────────────────────
Finalize KPI contract fields the frontend should not infer:
  value_source, l2_derived, forecast_layers, layer (headline tag).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from schemas.config import ForecastLayer, IntelligenceConfig, KPI, KpiValueSource, L2Derived

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


def _has_l3(kpi: KPI) -> bool:
    if kpi.l3_forecast and kpi.l3_forecast.predictions:
        return True
    if kpi.l3_forecast_by_series:
        return any(
            fc.predictions for fc in kpi.l3_forecast_by_series.values()
        )
    return False


def _is_temporal(kpi: KPI, rows: list[dict] | None) -> bool:
    if kpi.chart.x_axis_type == "temporal":
        return True
    if kpi.chart.type in (
        "line_chart", "area_chart", "stacked_area_chart", "stacked_bar_chart",
    ):
        return True
    if rows and kpi.chart.x_axis_type == "temporal" and kpi.chart.x_axis:
        from pipeline.metric_contract import find_date_column
        return bool(find_date_column(rows, kpi.chart.x_axis))
    return False


def infer_value_source(
    kpi: KPI,
    field_resolver: dict[str, dict] | None = None,
) -> KpiValueSource:
    """Derive value_source when the agent did not set it explicitly."""
    if kpi.l2_derived and kpi.l2_derived.formula:
        return "agent_derived"

    field = (kpi.l1.field_name if kpi.l1 else "") or ""
    resolver = field_resolver or {}
    info = resolver.get(field)
    if not info and field:
        norm = field.lower().replace(" ", "").replace("_", "").replace("%", "pct")
        for k, v in resolver.items():
            if k.lower().replace(" ", "").replace("_", "").replace("%", "pct") == norm:
                info = v
                break

    if info and info.get("source") == "formula":
        return "tableau_formula"

    if kpi.l2 and kpi.l2.formula and not kpi.l2.error:
        return "tableau_formula"

    return "direct"


def _populate_l2_derived(
    kpi: KPI,
    rows: list[dict] | None,
    field_resolver: dict[str, dict] | None,
) -> L2Derived | None:
    if kpi.l2_derived and kpi.l2_derived.formula:
        derived = kpi.l2_derived
    else:
        return None

    if derived.value is not None:
        return derived

    from pipeline.metric_contract import compute_l1_value

    live = compute_l1_value(kpi, rows) if rows else None
    if live is None and kpi.l1 and kpi.l1.value is not None:
        try:
            live = float(kpi.l1.value)
        except (TypeError, ValueError):
            live = None

    if live is not None:
        derived.value = live
    if not derived.unit and kpi.l1:
        derived.unit = kpi.l1.unit
    if not derived.input_fields:
        field = (kpi.l1.field_name if kpi.l1 else "") or ""
        info = (field_resolver or {}).get(field, {})
        derived.input_fields = list(info.get("refs") or [])

    return derived


def finalize_kpi_contract(
    kpi: KPI,
    rows: list[dict] | None = None,
    field_resolver: dict[str, dict] | None = None,
) -> list[str]:
    """
    Lock value_source, l2_derived, forecast_layers, and layer on one KPI.
    Returns human-readable change messages.
    """
    changes: list[str] = []
    name = kpi.name

    old_vs = kpi.value_source
    inferred = infer_value_source(kpi, field_resolver)
    if kpi.value_source == "direct" and inferred != "direct":
        kpi.value_source = inferred
        changes.append(f"{name}: value_source {old_vs} → {inferred}")

    if kpi.value_source == "agent_derived":
        populated = _populate_l2_derived(kpi, rows, field_resolver)
        if populated and not kpi.l2_derived:
            kpi.l2_derived = populated
            changes.append(f"{name}: populated l2_derived")
        elif populated and kpi.l2_derived and populated.value is not None and kpi.l2_derived.value is None:
            kpi.l2_derived.value = populated.value
            changes.append(f"{name}: l2_derived.value from rows")

    # forecast_layers — declarative list for the frontend
    layers: list[ForecastLayer] = []
    if kpi.l2_projection is not None:
        layers.append("l2_projection")
    if _has_l3(kpi):
        layers.append("l3")

    if layers != kpi.forecast_layers:
        old = kpi.forecast_layers
        kpi.forecast_layers = layers
        changes.append(f"{name}: forecast_layers {old or '[]'} → {layers}")

    # Headline layer tag (static config field — L3 is period-specific in the UI)
    target_layer: str = "L2" if kpi.value_source == "agent_derived" else "L1"
    if kpi.layer != target_layer:
        old_layer = kpi.layer
        kpi.layer = target_layer  # type: ignore[assignment]
        changes.append(f"{name}: layer {old_layer} → {target_layer}")

    return changes


def finalize_config_contract(
    config: IntelligenceConfig,
    view_cache: dict[str, list[dict]] | None = None,
    field_resolver: dict[str, dict] | None = None,
) -> list[str]:
    """Finalize contract fields on every KPI in the config."""
    from pipeline.view_rows import rows_for_kpi

    cache = view_cache or {}
    messages: list[str] = []

    for pv in config.personas:
        for sec in pv.dashboard_sections:
            for kpi in sec.kpis:
                rows, _ = rows_for_kpi(kpi, cache)
                messages.extend(
                    finalize_kpi_contract(kpi, rows or None, field_resolver)
                )

    if messages:
        log.info("KPI contract finalize: %d adjustment(s)", len(messages))
    return messages
