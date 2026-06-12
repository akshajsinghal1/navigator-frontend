"""
pipeline/config_emit.py
───────────────────────
Final config assembly: trend refresh, empty-section cleanup, emit-time validation.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from pipeline.metric_contract import (
    bucket_series,
    find_column,
    find_date_column,
    is_hourly_dates,
)
from pipeline.view_rows import align_kpi_view_names, rows_for_kpi

if TYPE_CHECKING:
    from schemas.config import IntelligenceConfig, KPI

log = logging.getLogger(__name__)

_CHART_PREFIX = re.compile(
    r"^Chart:\s*[\w_]+\s*[-–—]\s*",
    re.IGNORECASE,
)


def strip_chart_prefix(text: str | None) -> str | None:
    """Remove agent-prefixed 'Chart: view_name - ' from KPI descriptions."""
    if not text:
        return text
    cleaned = _CHART_PREFIX.sub("", text.strip())
    return cleaned or text


def finalize_descriptions(config: IntelligenceConfig) -> list[str]:
    """Strip chart-agent prefix pollution from all KPI descriptions."""
    changes: list[str] = []
    for pv in config.personas:
        for sec in pv.dashboard_sections:
            for kpi in sec.kpis:
                cleaned = strip_chart_prefix(kpi.description)
                if cleaned and cleaned != kpi.description:
                    kpi.description = cleaned
                    changes.append(f"{kpi.name}: stripped chart prefix from description")
    return changes


def finalize_l3_breakdown(config: IntelligenceConfig) -> list[str]:
    """
    Post-L3 pass for breakdown KPIs:
      - drop aggregate l3_forecast when per-series forecasts exist (prevents chart spike)
      - alias series keys via breakdown_labels (id → Facility_N → display name)
    """
    changes: list[str] = []
    for pv in config.personas:
        for sec in pv.dashboard_sections:
            for kpi in sec.kpis:
                ch = kpi.chart
                by_series = kpi.l3_forecast_by_series
                if not ch.breakdown_by or not by_series:
                    continue

                if kpi.l3_forecast is not None:
                    kpi.l3_forecast = None
                    if kpi.layer == "L3":
                        kpi.layer = "L2" if kpi.l2_projection else "L1"
                    changes.append(f"{kpi.name}: drop aggregate L3 (use per-series)")

                labels = dict(ch.breakdown_labels or {})
                expanded = dict(by_series)
                for key, fc in by_series.items():
                    sk = str(key).strip()
                    if sk in labels:
                        expanded[labels[sk]] = fc
                    if sk.isdigit():
                        expanded[f"Facility_{sk}"] = fc
                    elif sk.lower().startswith("facility_"):
                        tail = sk.split("_", 1)[-1]
                        if tail in labels:
                            expanded[labels[tail]] = fc

                if expanded != by_series:
                    kpi.l3_forecast_by_series = expanded
                    changes.append(f"{kpi.name}: expanded L3 series keys ({len(expanded)} keys)")

    if changes:
        log.info("Post-L3 finalize: %d L3 adjustment(s)", len(changes))
    return changes


def apply_trend_from_cache(kpi: KPI, view_cache: dict[str, list[dict]]) -> list[str]:
    """Recompute trend_pct / trend_direction from full cache rows."""
    changes: list[str] = []
    rows, source = rows_for_kpi(kpi, view_cache)
    if source != "cache" or not rows or not kpi.l2_projection:
        return changes

    l2 = kpi.l2_projection
    x_hint = kpi.chart.x_axis if kpi.chart.x_axis_type == "temporal" else None
    date_col = find_date_column(rows, l2.date_field, x_hint)
    if not date_col:
        return changes

    value_hint = l2.value_field or (kpi.l1.field_name if kpi.l1 else None) or kpi.chart.y_axis
    value_col = find_column(rows, value_hint)
    if not value_col:
        return changes

    agg = "avg" if l2.method == "ratio" else l2.aggregation
    series = bucket_series(rows, value_col, date_col, agg, hourly=is_hourly_dates(rows, date_col))
    if len(series) < 2:
        return changes

    prev_v = series[-2][1]
    curr_v = series[-1][1]
    if abs(prev_v) < 1e-9:
        return changes

    pct = round(100.0 * (curr_v - prev_v) / abs(prev_v), 1)
    direction = "up" if curr_v > prev_v else "down" if curr_v < prev_v else "flat"

    if kpi.trend_pct != pct:
        old = kpi.trend_pct
        kpi.trend_pct = pct
        changes.append(f"{kpi.name}: trend_pct {old} → {pct} (cache)")

    if kpi.trend_direction != direction:
        kpi.trend_direction = direction  # type: ignore[assignment]

    return changes


def drop_empty_sections(config: IntelligenceConfig) -> list[str]:
    """Remove dashboard sections with zero KPIs (e.g. after QA prune)."""
    removed: list[str] = []
    for pv in config.personas:
        kept = []
        for sec in pv.dashboard_sections:
            if sec.kpis:
                kept.append(sec)
            else:
                removed.append(sec.title or sec.id)
        pv.dashboard_sections = kept
    if removed:
        log.info("Emit: dropped %d empty section(s): %s", len(removed), removed)
    return [f"section dropped (empty): {t}" for t in removed]


def prepare_config_for_emit(
    config: IntelligenceConfig,
    view_cache: dict[str, list[dict]] | None = None,
) -> list[str]:
    """
    Pre-normalize: align view names to cache keys so classifier/blueprint/L3
  use full Hyper rows (not 20-row samples).
    """
    cache = view_cache or {}
    return align_kpi_view_names(config, cache) if cache else []


def finalize_config(
    config: IntelligenceConfig,
    view_cache: dict[str, list[dict]] | None = None,
    field_resolver: dict[str, dict] | None = None,
) -> list[str]:
    """
    Post-normalize emit pass: trends from cache, contract fields, drop empty sections.
    Returns change messages.
    """
    from pipeline.kpi_layers import finalize_config_contract

    cache = view_cache or {}
    messages: list[str] = []

    messages.extend(finalize_descriptions(config))

    for pv in config.personas:
        for sec in pv.dashboard_sections:
            for kpi in sec.kpis:
                messages.extend(apply_trend_from_cache(kpi, cache))

    messages.extend(finalize_config_contract(config, cache, field_resolver))
    messages.extend(drop_empty_sections(config))
    return messages


def post_l3_finalize_config(
    config: IntelligenceConfig,
    view_cache: dict[str, list[dict]] | None = None,
    field_resolver: dict[str, dict] | None = None,
) -> list[str]:
    """
    Run after TimesFM: repair L3 shape on breakdown KPIs, refresh labels, lock contract.
    """
    from pipeline.dimension_labels import apply_dimension_labels_config
    from pipeline.kpi_layers import finalize_config_contract

    cache = view_cache or {}
    messages: list[str] = []
    messages.extend(finalize_l3_breakdown(config))
    messages.extend(apply_dimension_labels_config(config, cache))
    messages.extend(finalize_config_contract(config, cache, field_resolver))
    log.info("Post-L3 finalize: %d total adjustment(s)", len(messages))
    return messages


def l1_source_report(config: IntelligenceConfig, view_cache: dict[str, list[dict]]) -> dict[str, str]:
    """Map KPI name → row source used for pipeline compute ('cache'|'sample'|'none')."""
    report: dict[str, str] = {}
    for pv in config.personas:
        for sec in pv.dashboard_sections:
            for kpi in sec.kpis:
                _, source = rows_for_kpi(kpi, view_cache)
                report[kpi.name] = source
    return report


def count_l1_fallbacks(report: dict[str, str]) -> int:
    return sum(1 for s in report.values() if s == "sample")


def reject_invalid_kpis_at_emit(
    config: IntelligenceConfig,
    view_cache: dict[str, list[dict]] | None = None,
) -> list[str]:
    """
    Emit gate: drop KPIs that still have critical unrenderable issues after post-process.
    Same contract as audit_config.prune_critical_kpis but uses full cache rows.
    """
    from pipeline.audit_config import PRUNE_CODES, audit_kpi

    removed: list[str] = []
    cache = view_cache or {}

    for pv in config.personas:
        persona = pv.persona.role if pv.persona else ""
        for sec in pv.dashboard_sections:
            kept: list[KPI] = []
            for kpi in sec.kpis:
                result = audit_kpi(kpi, persona, view_cache=cache if cache else None)
                prune_codes = {i.code for i in result.critical} & PRUNE_CODES
                if prune_codes:
                    removed.append(
                        f"{kpi.name}: rejected at emit ({', '.join(sorted(prune_codes))})"
                    )
                    continue
                kept.append(kpi)
            sec.kpis = kept

    if removed:
        log.info("Emit gate: rejected %d KPI(s)", len(removed))
        for msg in removed:
            log.info("  • %s", msg)
    return removed


def post_process_config(
    config: IntelligenceConfig,
    view_cache: dict[str, list[dict]] | None = None,
    field_resolver: dict[str, dict] | None = None,
) -> list[str]:
    """
    Single post-orchestrator gate — all KPIs (including QA supplements) pass through:
      align views → classify → blueprint → normalize → finalize → emit reject
    """
    from pipeline.chart_blueprint import apply_chart_blueprints
    from pipeline.metric_classifier import classify_config
    from pipeline.metric_contract import normalize_config

    cache = view_cache or {}
    messages: list[str] = []

    messages.extend(prepare_config_for_emit(config, cache))
    messages.extend(classify_config(config, cache))
    messages.extend(apply_chart_blueprints(config, cache))
    from pipeline.dimension_labels import apply_dimension_labels_config
    messages.extend(apply_dimension_labels_config(config, cache))
    messages.extend(normalize_config(config, cache))
    messages.extend(finalize_config(config, cache, field_resolver))
    messages.extend(reject_invalid_kpis_at_emit(config, cache))
    messages.extend(drop_empty_sections(config))

    log.info("Post-process: %d total adjustment(s)", len(messages))
    return messages
