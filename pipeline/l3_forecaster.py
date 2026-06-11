"""
pipeline/l3_forecaster.py
──────────────────────────
L3 TimesFM forecasting step.

Runs after the QA agent. For every time-series KPI that has enough history
(64+ context points), runs TimesFM to generate 30-day forward predictions
with p10/p90 confidence bands.

For KPIs with chart.breakdown_by, forecasts each top breakdown series in one
batch TimesFM call and stores results in l3_forecast_by_series. A combined
aggregate series (all rows) is also stored in l3_forecast for tile headlines.

Data source: Hyper raw tables (already in view_data_cache as [TABLE] entries)
Model:       google/timesfm-2.5-200m (zero-shot, no training required)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np

from pipeline.metric_contract import (
    MIN_L3_CONTEXT,
    _norm,
    bucket_series,
    find_column,
    find_date_column,
    increments_from_cumulative,
    is_cumulative_series,
    resolve_chart_aggregation,
    resolve_l3_eligible,
    top_breakdown_keys,
)
from pipeline.view_rows import rows_for_kpi

log = logging.getLogger(__name__)

HORIZON_DAYS = 30
MAX_CONTEXT  = 512


def _load_model():
    """Load and compile TimesFM model. Returns None if not available."""
    try:
        import timesfm
        import torch
        torch.set_float32_matmul_precision("high")

        tfm = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
            "google/timesfm-2.5-200m-pytorch",
        )
        tfm.compile(timesfm.ForecastConfig(
            max_context=MAX_CONTEXT,
            max_horizon=HORIZON_DAYS,
            normalize_inputs=False,
        ))
        log.info("L3: TimesFM model loaded and compiled")
        return tfm
    except ImportError:
        log.warning("L3: timesfm not installed — skipping L3 forecasts")
        return None
    except Exception as exc:
        log.warning("L3: model load failed: %s", exc)
        return None


def _date_hint(kpi) -> str | None:
    if kpi.l2_projection and kpi.l2_projection.date_field:
        return kpi.l2_projection.date_field
    if kpi.chart.x_axis:
        return kpi.chart.x_axis
    return None


def _extract_series(
    rows: list[dict],
    field_name: str,
    aggregation: str = "avg",
    date_hint: str | None = None,
) -> np.ndarray | None:
    """
    Build a time series using the shared metric_contract bucketing rules.
    Returns None if fewer than MIN_L3_CONTEXT points result.
    """
    if not rows:
        return None

    col = find_column(rows, field_name)
    if not col:
        return None

    date_col = find_date_column(rows, date_hint)
    if date_col:
        series = bucket_series(rows, col, date_col, aggregation.lower())
        raw_vals = [v for _, v in series]
        if is_cumulative_series(raw_vals):
            series = increments_from_cumulative(series)
        vals = [v for _, v in series]
    else:
        vals = []
        for r in rows:
            v = r.get(col)
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                pass

    if len(vals) < MIN_L3_CONTEXT:
        return None

    return np.array(vals[-MAX_CONTEXT:], dtype=np.float32)


def _build_l3_forecast(point_row, quantile_row, now: str):
    from schemas.config import L3Forecast

    predictions = [round(float(v), 4) for v in point_row.tolist()]
    lower_p10   = [round(float(v), 4) for v in quantile_row[:, 0].tolist()]
    upper_p90   = [round(float(v), 4) for v in quantile_row[:, -1].tolist()]

    if any(np.isnan(v) for v in predictions):
        return None

    return L3Forecast(
        model        = "timesfm-2.5-200m",
        horizon_days = HORIZON_DAYS,
        predictions  = predictions,
        lower_p10    = lower_p10,
        upper_p90    = upper_p90,
        generated_at = now,
    )


def _forecast_breakdown_kpi(
    kpi, rows, tfm, agg: str, date_hint: str | None, value_field: str, now: str,
) -> bool:
    """Forecast per breakdown series (no aggregate — avoids chart spike). Returns True if any written."""
    from schemas.config import L3Forecast

    breakdown_col = find_column(rows, kpi.chart.breakdown_by)
    if not breakdown_col:
        log.info("L3: '%s' — breakdown column '%s' not found", kpi.name, kpi.chart.breakdown_by)
        return False

    keys = top_breakdown_keys(rows, breakdown_col)
    if not keys:
        return False

    series_arrays: list[np.ndarray] = []
    key_order: list[str] = []
    for bk in keys:
        sub = [r for r in rows if str(r.get(breakdown_col, "")) == bk]
        s = _extract_series(sub, value_field, aggregation=agg, date_hint=date_hint)
        if s is not None:
            series_arrays.append(s)
            key_order.append(bk)

    by_series: dict[str, L3Forecast] = {}
    if series_arrays:
        log.info(
            "L3: '%s' — batch forecasting %d breakdown series",
            kpi.name, len(series_arrays),
        )
        point_fc, quantile_fc = tfm.forecast(HORIZON_DAYS, series_arrays)
        for i, bk in enumerate(key_order):
            fc = _build_l3_forecast(point_fc[i], quantile_fc[i], now)
            if fc:
                by_series[bk] = fc

    if by_series:
        kpi.l3_forecast_by_series = by_series
        # Per-series only — aggregate L3 sums breakdowns and spikes multi-line charts.
        kpi.l3_forecast = None
        kpi.layer = "L3"
        return True

    # Per-series failed — fallback to aggregate headline only
    agg_series = _extract_series(rows, value_field, aggregation=agg, date_hint=date_hint)
    if agg_series is not None:
        point_fc, quantile_fc = tfm.forecast(HORIZON_DAYS, [agg_series])
        fc = _build_l3_forecast(point_fc[0], quantile_fc[0], now)
        if fc:
            kpi.l3_forecast = fc
            kpi.layer = "L3"
            return True
    return False


def _historical_rows_for_forecast_kpi(
    kpi,
    view_cache: dict[str, list[dict]],
) -> tuple[list[dict], str, dict[str, str]] | None:
    """
    FORECAST_* views often have only a few future rows — train L3 on the matching
    historical Hyper table (e.g. bed_utilization_hourly for occupancy trends).
    """
    vn = ((kpi.l1.view_name if kpi.l1 else "") or "").upper()
    if "FORECAST" not in vn:
        return None

    name_n = _norm(kpi.name)
    field_n = _norm(kpi.l1.field_name if kpi.l1 else "")
    hints = f"{name_n} {field_n} {_norm(kpi.chart.y_axis or '')}"
    if "occupancy" not in hints and "utilization" not in hints:
        return None

    best: tuple[str, list[dict]] | None = None
    for table, rows in view_cache.items():
        tn = table.upper()
        if "FORECAST" in tn or len(rows) < MIN_L3_CONTEXT:
            continue
        if "BED_UTILIZATION" not in tn and "UTILIZATION" not in tn:
            continue
        if not find_column(rows, "occupancy_percent"):
            continue
        if best is None or len(rows) > len(best[1]):
            best = (table, rows)

    if not best:
        return None

    table, rows = best
    overrides: dict[str, str] = {}
    occ = find_column(rows, "occupancy_percent")
    dt = find_column(rows, "utilization_datetime")
    if occ:
        overrides["value_field"] = occ
    if dt:
        overrides["date_hint"] = dt
    log.info(
        "L3: '%s' — FORECAST view has few rows; using %s (%d rows)",
        kpi.name, table, len(rows),
    )
    return rows, "cache_alt", overrides


def _resolve_l3_training_rows(
    kpi,
    view_cache: dict[str, list[dict]],
) -> tuple[list[dict], str, dict[str, str]]:
    vn = ((kpi.l1.view_name if kpi.l1 else "") or "").upper()
    if "FORECAST" in vn:
        alt = _historical_rows_for_forecast_kpi(kpi, view_cache)
        if alt:
            return alt

    rows, source = rows_for_kpi(kpi, view_cache)
    if source != "none" and len(rows) >= MIN_L3_CONTEXT:
        return rows, source, {}

    alt = _historical_rows_for_forecast_kpi(kpi, view_cache)
    if alt:
        return alt
    return rows, source, {}


def run_l3_forecasts(
    config,
    view_data_cache: dict[str, list[dict]],
) -> int:
    """
    Runs TimesFM on all eligible time-series KPIs in config.
    Mutates config in-place — sets l3_forecast / l3_forecast_by_series where successful.
    Returns count of KPIs that got L3 forecasts.
    """
    eligible = []
    for pv in config.personas:
        for sec in pv.dashboard_sections:
            for kpi in sec.kpis:
                chart = kpi.chart
                l1    = kpi.l1
                if not l1 or not l1.view_name:
                    continue
                is_ts = (
                    chart.x_axis_type == "temporal"
                    or chart.type in (
                        "line_chart", "area_chart", "stacked_area_chart", "stacked_bar_chart",
                    )
                )
                if not is_ts:
                    continue
                rows, source, overrides = _resolve_l3_training_rows(kpi, view_data_cache)
                if source == "none" or len(rows) < MIN_L3_CONTEXT:
                    if source == "sample":
                        log.debug(
                            "L3: '%s' skipped — only sample rows (cache miss for %r)",
                            kpi.name, l1.view_name,
                        )
                    continue
                if not resolve_l3_eligible(kpi) and source != "cache_alt":
                    continue
                eligible.append((kpi, rows, overrides))

    if not eligible:
        log.info("L3: no eligible time-series KPIs found")
        return 0

    log.info("L3: %d eligible KPIs for TimesFM", len(eligible))

    tfm = _load_model()
    if tfm is None:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    count = 0

    for kpi, rows, overrides in eligible:
        try:
            agg = resolve_chart_aggregation(kpi)
            date_hint = overrides.get("date_hint") or _date_hint(kpi)
            from pipeline.chart_blueprint import _resolve_measure
            value_field = overrides.get("value_field") or _resolve_measure(kpi, rows) or kpi.l1.field_name
            if not overrides.get("value_field") and kpi.l2_projection and kpi.l2_projection.value_field:
                col = find_column(rows, kpi.l2_projection.value_field)
                if col:
                    value_field = col

            if kpi.chart.breakdown_by:
                ok = _forecast_breakdown_kpi(kpi, rows, tfm, agg, date_hint, value_field, now)
                if ok:
                    n = len(kpi.l3_forecast_by_series or {})
                    log.info("L3: '%s' → %d series + aggregate=%s", kpi.name, n, bool(kpi.l3_forecast))
                    count += 1
                continue

            series = _extract_series(rows, value_field, aggregation=agg, date_hint=date_hint)
            if series is None:
                log.info(
                    "L3: '%s' — could not extract series (agg=%s, rows=%d)",
                    kpi.name, agg, len(rows),
                )
                continue

            log.info("L3: forecasting '%s' (%d context points)", kpi.name, len(series))
            point_fc, quantile_fc = tfm.forecast(HORIZON_DAYS, [series])
            fc = _build_l3_forecast(point_fc[0], quantile_fc[0], now)
            if not fc:
                log.warning("L3: '%s' returned NaN predictions — skipping", kpi.name)
                continue

            kpi.l3_forecast = fc
            kpi.layer = "L3"
            count += 1
            log.info(
                "L3: '%s' → %d-day forecast generated (day1=%.3f)",
                kpi.name, HORIZON_DAYS, fc.predictions[0],
            )

        except Exception as exc:
            log.warning("L3: '%s' failed: %s", kpi.name, exc)

    log.info("L3: %d/%d KPIs got forecasts", count, len(eligible))
    return count
