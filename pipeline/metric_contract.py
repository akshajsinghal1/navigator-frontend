"""
pipeline/metric_contract.py
────────────────────────────
Single source of truth for how each KPI's numbers are aggregated.

The domain agent sets l2_projection.method; this module derives consistent
chart / L1 / L2 / L3 rules so pipeline and frontend stay aligned.

Metric kinds (from KPI shape + l2_projection when present):
  snapshot    — stock at a point in time (beds, queue depth); no l2_projection
  rate        — ratio method: percentages and rates (occupancy %, margin)
  accumulator — daily_rate / growth_rate: totals that grow over time
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from schemas.config import IntelligenceConfig, KPI

log = logging.getLogger(__name__)

MetricKind = Literal["snapshot", "rate", "accumulator"]
MAX_BREAKDOWN_SERIES = 8
MIN_L3_CONTEXT = 64

_DATE_KWS = ("date", "time", "timestamp", "day", "hour", "month", "created", "period")


def metric_kind(method: str | None) -> MetricKind:
    """Infer kind from l2_projection.method only. Prefer resolve_metric_kind(kpi)."""
    if method == "ratio":
        return "rate"
    if method in ("daily_rate", "growth_rate"):
        return "accumulator"
    return "accumulator"


def resolve_metric_kind(kpi: KPI, rows: list[dict] | None = None) -> MetricKind:
    """Classify KPI metric kind from chart shape, name, units, and projection."""
    from pipeline.metric_classifier import classify_metric_kind

    return classify_metric_kind(kpi, rows)


def strip_legacy_stable_projection(kpi: KPI) -> bool:
    """Remove deprecated stable method — snapshot KPIs have no l2_projection."""
    l2 = kpi.l2_projection
    if l2 and l2.method == "stable":
        kpi.l2_projection = None
        return True
    return False


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def parse_numeric_value(v: Any) -> float | None:
    """Parse numbers from raw values, including '73.8%' and '(1,234)'."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    if s.startswith("(") and s.endswith(")"):
        try:
            return -float(s[1:-1])
        except ValueError:
            return None
    if s.endswith("%"):
        s = s[:-1].strip()
    try:
        return float(s)
    except ValueError:
        return None


def find_column(rows: list[dict], hint: str | None) -> str | None:
    if not rows or not hint:
        return None
    cols = list(rows[0].keys())
    h = _norm(hint)
    for c in cols:
        if _norm(c) == h:
            return c
    for c in cols:
        cn = _norm(c)
        if h in cn or cn in h:
            return c
    return None


def find_date_column(rows: list[dict], *hints: str | None) -> str | None:
    if not rows:
        return None
    for hint in hints:
        if hint and hint not in ("null", "None"):
            col = find_column(rows, hint)
            if col:
                return col
    cols = list(rows[0].keys())
    return next(
        (c for c in cols if any(kw in c.lower() for kw in _DATE_KWS)),
        None,
    )


def bucket_key(datetime_val: Any, hourly: bool) -> str:
    s = str(datetime_val)
    if hourly and len(s) > 10 and s[10] in ("T", " ", "t"):
        return s[:13]
    return s[:10]


def is_hourly_dates(rows: list[dict], date_col: str) -> bool:
    sample = next((str(r.get(date_col, "")) for r in rows if r.get(date_col)), "")
    return len(sample) > 10 and sample[10] in ("T", " ", "t")


def is_cumulative_series(vals: list[float]) -> bool:
    """True when values mostly increase — typical running-total / YTD fields."""
    if len(vals) < 3:
        return False
    increases = sum(1 for i in range(len(vals) - 1) if vals[i] <= vals[i + 1])
    return increases >= (len(vals) - 1) * 0.85


def increments_from_cumulative(series: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Convert monotonic cumulative buckets to per-period increments."""
    if len(series) < 2:
        return series
    out: list[tuple[str, float]] = []
    for i, (k, v) in enumerate(series):
        if i == 0:
            out.append((k, v))
        else:
            out.append((k, v - series[i - 1][1]))
    return out


def aggregate_values(vals: list[float], agg: str) -> float:
    if not vals:
        return 0.0
    if agg == "count":
        return float(len(vals))
    if agg == "avg":
        return sum(vals) / len(vals)
    if agg == "max":
        return max(vals)
    if agg == "min":
        return min(vals)
    return sum(vals)


def bucket_series(
    rows: list[dict],
    value_col: str,
    date_col: str,
    per_bucket_agg: str,
    *,
    hourly: bool | None = None,
) -> list[tuple[str, float]]:
    """Group rows by time bucket; return sorted (bucket, value) pairs."""
    if hourly is None:
        hourly = is_hourly_dates(rows, date_col)
    buckets: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        dv = r.get(date_col)
        if dv is None:
            continue
        bk = bucket_key(dv, hourly)
        if per_bucket_agg == "count":
            buckets[bk].append(1.0)
            continue
        v = r.get(value_col)
        if v is None:
            continue
        parsed = parse_numeric_value(v)
        if parsed is not None:
            buckets[bk].append(parsed)
    if not buckets:
        return []
    return [
        (k, aggregate_values(buckets[k], per_bucket_agg))
        for k in sorted(buckets.keys())
    ]


def resolve_chart_aggregation(kpi: KPI, rows: list[dict] | None = None) -> str:
    """Aggregation applied WITHIN each time bucket for charts and L3 series."""
    l2 = kpi.l2_projection
    kind = resolve_metric_kind(kpi, rows)
    if kind == "snapshot":
        # Hourly point-in-time metrics: max/avg within a day — never sum hours.
        ca = (kpi.chart.aggregation or "").lower()
        if ca in ("sum", "avg", "count", "min", "max"):
            return ca
        if l2 and l2.aggregation:
            return l2.aggregation
        return "avg"
    if kind == "rate":
        return "avg"
    if l2 and l2.aggregation:
        return l2.aggregation
    return (kpi.chart.aggregation or "sum").lower()


def top_breakdown_keys(
    rows: list[dict],
    breakdown_col: str,
    *,
    max_series: int = MAX_BREAKDOWN_SERIES,
) -> list[str]:
    """Return the top-N breakdown dimension values by row count."""
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        k = str(r.get(breakdown_col, ""))
        if k and k != "(null)":
            counts[k] += 1
    ranked = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return [k for k, _ in ranked[:max_series]]


def column_is_numeric(rows: list[dict], col: str) -> bool:
    for r in rows[: min(20, len(rows))]:
        if parse_numeric_value(r.get(col)) is not None:
            return True
    return False


def _distinct_count(rows: list[dict], col: str, *, limit: int = 100) -> int:
    return len({
        str(r.get(col, ""))
        for r in rows[:limit]
        if r.get(col) is not None and str(r.get(col, "")) not in ("", "(null)")
    })


def _categorical_columns(
    rows: list[dict],
    *,
    min_distinct: int = 2,
    max_distinct: int = 50,
) -> list[tuple[str, int]]:
    """Non-numeric columns with a useful cardinality for breakdown charts."""
    if not rows:
        return []
    out: list[tuple[str, int]] = []
    for c in rows[0].keys():
        if column_is_numeric(rows, c):
            continue
        dc = _distinct_count(rows, c)
        if min_distinct <= dc <= max_distinct:
            out.append((c, dc))
    return sorted(out, key=lambda x: (-x[1], x[0]))


def _numeric_columns(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    return [c for c in rows[0].keys() if column_is_numeric(rows, c)]


def _name_implies_breakdown(name_n: str) -> bool:
    return " by " in name_n or name_n.startswith("by ") or " breakdown" in name_n


def _breakdown_hint_from_name(name_n: str) -> str | None:
    m = re.search(r"\bby\s+(\w+)", name_n)
    return m.group(1) if m else None


def _compute_conversion_rate(rows: list[dict]) -> float | None:
    """referral_count + converted_count → conversion %."""
    conv = find_column(rows, "converted_count") or find_column(rows, "converted")
    total = find_column(rows, "referral_count") or find_column(rows, "referrals")
    if not conv or not total:
        return None
    c = sum(parse_numeric_value(r.get(conv)) or 0 for r in rows)
    t = sum(parse_numeric_value(r.get(total)) or 0 for r in rows)
    if t <= 0:
        return None
    return 100.0 * c / t


def _compute_isolation_utilization(rows: list[dict]) -> float | None:
    iso = find_column(rows, "isolation_beds_used")
    staffed = find_column(rows, "staffed_beds")
    if not iso or not staffed:
        return None
    ratios: list[float] = []
    for r in rows:
        i = parse_numeric_value(r.get(iso))
        s = parse_numeric_value(r.get(staffed))
        if i is not None and s and s > 0:
            ratios.append(100.0 * i / s)
    return sum(ratios) / len(ratios) if ratios else None


def compute_count_breakdown_headline(kpi: KPI, rows: list[dict]) -> float | None:
    """
    Headline for count KPIs with a breakdown dimension and numeric y-axis.
    Counts breakdown entities in the latest period above the p75 of the metric
  (data-driven 'high' threshold — no workbook-specific rules).
    """
    chart_agg = (kpi.chart.aggregation or "").lower()
    l2_agg = (kpi.l2_projection.aggregation if kpi.l2_projection else "").lower()
    is_count = chart_agg == "count" or l2_agg == "count"
    if not is_count or not kpi.chart.breakdown_by:
        return None
    y_col = find_column(rows, kpi.chart.y_axis) if kpi.chart.y_axis else None
    breakdown_col = find_column(rows, kpi.chart.breakdown_by)
    if not y_col or not breakdown_col or not column_is_numeric(rows, y_col):
        return None

    ys: list[float] = []
    for r in rows:
        try:
            ys.append(float(r[y_col]))
        except (TypeError, ValueError):
            pass
    if not ys:
        return None
    ys_sorted = sorted(ys)
    threshold = ys_sorted[max(int(len(ys_sorted) * 0.75) - 1, 0)]

    _x_hint = kpi.chart.x_axis if kpi.chart.x_axis_type == "temporal" else None
    date_col = find_date_column(
        rows,
        kpi.l2_projection.date_field if kpi.l2_projection else None,
        _x_hint,
    )
    if date_col:
        keys = sorted(
            {bucket_key(r[date_col], is_hourly_dates(rows, date_col)) for r in rows if r.get(date_col)}
        )
        if not keys:
            return None
        latest = keys[-1]
        hourly = is_hourly_dates(rows, date_col)
        sub = [
            r for r in rows
            if r.get(date_col) and bucket_key(r[date_col], hourly) == latest
        ]
    else:
        sub = rows

    by_entity: dict[str, float] = {}
    for r in sub:
        bk = str(r.get(breakdown_col, ""))
        if not bk or bk == "(null)":
            continue
        try:
            y = float(r[y_col])
        except (TypeError, ValueError):
            continue
        prev = by_entity.get(bk)
        by_entity[bk] = y if prev is None else max(prev, y)

    return float(sum(1 for v in by_entity.values() if v >= threshold))


def resolve_l3_breakdown_aggregate(kpi: KPI) -> str:
    """How to combine per-series L3 predictions into one headline."""
    unit = (kpi.l1.unit if kpi.l1 else "") or ""
    is_percent = unit == "%" or (
        kpi.l2_projection and kpi.l2_projection.method == "ratio" and unit != "hours"
    )
    kind = resolve_metric_kind(kpi)
    if is_percent or kind == "rate":
        return "avg"
    if kind == "snapshot":
        agg = resolve_chart_aggregation(kpi).lower()
        if agg == "avg":
            return "avg"
        if agg == "max":
            return "max"
        name_n = _norm(kpi.name)
        if any(kw in name_n for kw in ("hold", "transfer", "referral", "volume")):
            return "sum"
        return "avg"
    return "sum"


def resolve_l3_eligible(kpi: KPI) -> bool:
    """Whether this KPI should receive L3 forecast(s)."""
    vn = (kpi.l1.view_name if kpi.l1 else "") or ""
    if vn.upper().startswith("[TABLE] FORECAST"):
        return False

    name_n = _norm(kpi.name)
    if "snapshot" in name_n or "heatmap" in name_n:
        return False

    ch = kpi.chart
    if ch.type in ("horizontal_bar_chart", "gauge_chart", "kpi_card", "heatmap_chart"):
        if ch.x_axis_type != "temporal":
            return False

    if resolve_metric_kind(kpi) == "snapshot":
        if "trend" not in name_n and "over time" not in name_n:
            return False

    field_n = _norm(kpi.l1.field_name if kpi.l1 else "")
    y_n = _norm(ch.y_axis or "")
    if "overtime hours" in field_n or "agency hours" in field_n:
        return False  # TimesFM returns NaN on staffing hour series in this workbook
    if "overtime hours" in y_n or "agency hours" in y_n:
        return False

    return True


def compute_l1_value(kpi: KPI, rows: list[dict]) -> float | None:
    """
    Compute the L1 headline from live rows using the metric contract.
  Falls back to None when rows/columns are missing.
    """
    if kpi.value_source == "agent_derived" and kpi.l2_derived and kpi.l2_derived.value is not None:
        return float(kpi.l2_derived.value)
    if not rows or not kpi.l1:
        return None
    l2 = kpi.l2_projection
    value_col = find_column(rows, l2.value_field if l2 else kpi.l1.field_name)
    if not value_col:
        value_col = find_column(rows, kpi.l1.field_name)
    if not value_col and kpi.chart.y_axis:
        value_col = find_column(rows, kpi.chart.y_axis)
    if not value_col:
        return None

    count_headline = compute_count_breakdown_headline(kpi, rows)
    if count_headline is not None:
        return count_headline

    name_n = _norm(kpi.name)
    if "conversion" in name_n and "rate" in name_n:
        conv_rate = _compute_conversion_rate(rows)
        if conv_rate is not None:
            return conv_rate

    if "isolation" in name_n and "utilization" in name_n:
        iso_rate = _compute_isolation_utilization(rows)
        if iso_rate is not None:
            return iso_rate

    if not column_is_numeric(rows, value_col):
        return None

    kind = resolve_metric_kind(kpi, rows)
    _x_hint = kpi.chart.x_axis if kpi.chart.x_axis_type == "temporal" else None
    date_col = find_date_column(
        rows,
        l2.date_field if l2 else None,
        _x_hint,
    )

    if kind == "snapshot" and date_col:
        if "available beds" in name_n:
            bucket_agg = resolve_chart_aggregation(kpi)
            series = bucket_series(rows, value_col, date_col, bucket_agg)
            return series[-1][1] if series else None
        if is_hourly_dates(rows, date_col):
            # Queue depth / census: sum across entities in the latest hour bucket.
            keys = sorted({
                bucket_key(r[date_col], True) for r in rows if r.get(date_col)
            })
            if not keys:
                return None
            latest = keys[-1]
            sub = [r for r in rows if bucket_key(r[date_col], True) == latest]
            vals = [parse_numeric_value(r[value_col]) for r in sub]
            vals = [v for v in vals if v is not None]
            return sum(vals) if vals else None
        bucket_agg = resolve_chart_aggregation(kpi)
        series = bucket_series(rows, value_col, date_col, bucket_agg)
        if not series:
            return None
        return series[-1][1]

    if kind == "rate":
        if date_col:
            series = bucket_series(rows, value_col, date_col, "avg")
            if series:
                return series[-1][1]
        vals = [parse_numeric_value(r[value_col]) for r in rows if r.get(value_col) is not None]
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    method = l2.method if l2 else None
    if method == "daily_rate" and date_col:
        series = bucket_series(rows, value_col, date_col, "sum")
        if series:
            vals = [v for _, v in series]
            if is_cumulative_series(vals) and len(series) >= 2:
                return series[-1][1] - series[-2][1]
            return series[-1][1]

    agg = (l2.aggregation if l2 else None) or (kpi.chart.aggregation or "sum")
    # Temporal flow metrics: L1 = latest period bucket, not lifetime sum of all rows.
    if date_col and agg == "sum" and kind == "accumulator":
        series = bucket_series(rows, value_col, date_col, "sum")
        if series:
            vals = [v for _, v in series]
            if is_cumulative_series(vals) and len(series) >= 2:
                return series[-1][1] - series[-2][1]
            return series[-1][1]

    vals = []
    for r in rows:
        v = r.get(value_col)
        if v is None:
            continue
        parsed = parse_numeric_value(v)
        if parsed is not None:
            vals.append(parsed)
    if not vals:
        return None
    return aggregate_values(vals, agg.lower())


def compute_l2_projection_value(
    kpi: KPI,
    rows: list[dict],
    horizon_days: int,
) -> float | None:
    """7D/30D L2 value from live rows."""
    if not rows or not kpi.l2_projection:
        return None
    l2 = kpi.l2_projection
    value_col = find_column(rows, l2.value_field)
    if not value_col:
        return None
    x_hint = kpi.chart.x_axis if kpi.chart.x_axis_type == "temporal" else None
    date_col = find_date_column(rows, l2.date_field, x_hint)

    if l2.method == "ratio":
        return compute_l1_value(kpi, rows)

    if l2.method == "daily_rate":
        if not date_col:
            return compute_l1_value(kpi, rows)
        series = bucket_series(rows, value_col, date_col, "sum")
        if len(series) < 2:
            return compute_l1_value(kpi, rows)
        vals = [v for _, v in series]
        if is_cumulative_series(vals):
            inc_series = increments_from_cumulative(series)
            avg_daily = sum(v for _, v in inc_series) / len(inc_series)
            return avg_daily * horizon_days
        total = sum(v for _, v in series)
        keys = sorted({bucket_key(r[date_col], is_hourly_dates(rows, date_col)) for r in rows if r.get(date_col)})
        span_days = max(len(keys), 1)
        return (total / span_days) * horizon_days

    if l2.method == "growth_rate" and date_col:
        series = bucket_series(rows, value_col, date_col, l2.aggregation)
        if len(series) < 2:
            return compute_l1_value(kpi, rows)
        first, last = series[0][1], series[-1][1]
        if not first:
            return last
        growth = (last / first) ** (1 / (len(series) - 1)) - 1
        periods_ahead = horizon_days / max(len(series), 1)
        return last * ((1 + growth) ** periods_ahead)

    return compute_l1_value(kpi, rows)


def _rows_for_normalize(
    kpi: KPI,
    view_cache: dict[str, list[dict]] | None,
) -> list[dict] | None:
    """Prefer full cache rows; fall back to embedded raw_data sample."""
    if view_cache:
        from pipeline.view_rows import rows_for_kpi
        rows, _ = rows_for_kpi(kpi, view_cache)
        if rows:
            return rows
    raw = kpi.raw_data
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return raw
    return None


def normalize_kpi(
    kpi: KPI,
    *,
    kpi_name: str = "",
    view_cache: dict[str, list[dict]] | None = None,
) -> list[str]:
    """
    Fix common agent inconsistencies in-place.
    Returns a list of human-readable changes made.
    """
    changes: list[str] = []
    name = kpi_name or kpi.name

    if strip_legacy_stable_projection(kpi):
        changes.append(f"{name}: removed deprecated stable l2_projection")

    if kpi.l2_projection:
        df = kpi.l2_projection.date_field
        if df in ("null", "None", "", "NULL"):
            kpi.l2_projection.date_field = None
            if kpi.chart.x_axis_type == "temporal" and kpi.chart.x_axis:
                kpi.l2_projection.date_field = kpi.chart.x_axis
                changes.append(f"{name}: date_field 'null' → chart.x_axis '{kpi.chart.x_axis}'")
            else:
                changes.append(f"{name}: cleared invalid date_field 'null'")

    if kpi.chart.x_axis_type == "temporal" and kpi.chart.x_axis and kpi.l2_projection:
        df = kpi.l2_projection.date_field
        if df and df != kpi.chart.x_axis:
            kpi.l2_projection.date_field = kpi.chart.x_axis
            changes.append(f"{name}: date_field '{df}' → chart.x_axis '{kpi.chart.x_axis}'")
        elif not df:
            kpi.l2_projection.date_field = kpi.chart.x_axis
            changes.append(f"{name}: date_field set from chart.x_axis '{kpi.chart.x_axis}'")

    if kpi.l2_projection and kpi.l1:
        rows_hint = _rows_for_normalize(kpi, view_cache)
        if rows_hint and len(rows_hint) and isinstance(rows_hint[0], dict):
            vf = kpi.l2_projection.value_field
            if not find_column(rows_hint, vf) and find_column(rows_hint, kpi.l1.field_name):
                kpi.l2_projection.value_field = kpi.l1.field_name
                changes.append(f"{name}: value_field → l1.field_name '{kpi.l1.field_name}'")

    if (kpi.chart.aggregation or "").lower() == "count" and kpi.chart.y_axis:
        rows_hint = _rows_for_normalize(kpi, view_cache)
        if rows_hint and isinstance(rows_hint[0], dict):
            vf_col = find_column(rows_hint, kpi.l2_projection.value_field) if kpi.l2_projection else None
            y_col = find_column(rows_hint, kpi.chart.y_axis)
            if y_col and vf_col and not column_is_numeric(rows_hint, vf_col):
                kpi.l2_projection = None
                kpi.l1.field_name = kpi.chart.y_axis
                if (kpi.l1.unit or "").strip() == "%":
                    kpi.l1.unit = ""
                changes.append(f"{name}: count KPI — value_field → y_axis '{kpi.chart.y_axis}'")

    is_count_breakdown = (
        (kpi.chart.aggregation or "").lower() == "count" and kpi.chart.breakdown_by
    )
    if not is_count_breakdown:
        correct_agg = resolve_chart_aggregation(kpi)
        current = (kpi.chart.aggregation or "sum").lower()
        if current != correct_agg:
            kpi.chart.aggregation = correct_agg
            changes.append(f"{name}: chart.aggregation {current} → {correct_agg}")

    name_n = _norm(name)
    rows_hint = _rows_for_normalize(kpi, view_cache)

    # Broken heatmap with no rows — downgrade before other row-based fixes
    if kpi.chart.type == "heatmap_chart" and (not rows_hint or not len(rows_hint)):
        kpi.chart.type = "kpi_card"
        changes.append(f"{name}: heatmap with no rows → kpi_card")

    if rows_hint and len(rows_hint) and isinstance(rows_hint[0], dict):
        # Conversion rate — must be converted/referrals, not avg(converted_count)
        if "conversion" in name_n and "rate" in name_n:
            if kpi.l2_projection:
                kpi.l2_projection.method = "ratio"
                kpi.l2_projection.aggregation = "avg"
            kpi.chart.type = "gauge_chart"
            kpi.chart.aggregation = "avg"
            kpi.chart.x_axis = None
            kpi.chart.y_axis = None
            if kpi.l1:
                kpi.l1.unit = "%"
                kpi.l1.format = "percentage"
                if kpi.l1.field_name and "count" in _norm(kpi.l1.field_name):
                    kpi.l1.field_name = "conversion_rate"
                conv_live = _compute_conversion_rate(rows_hint)
                if conv_live is not None:
                    kpi.l1.value = round(conv_live, 2)
            changes.append(f"{name}: conversion rate → gauge + ratio method")

        # Isolation utilization — rate % not sum of bed-hours
        if "isolation" in name_n and "utilization" in name_n:
            if kpi.l2_projection:
                kpi.l2_projection.method = "ratio"
                kpi.l2_projection.aggregation = "avg"
                kpi.l2_projection.date_field = kpi.l2_projection.date_field or kpi.chart.x_axis
            kpi.chart.type = "line_chart"
            kpi.chart.aggregation = "avg"
            if kpi.l1:
                kpi.l1.unit = "%"
                kpi.l1.format = "percentage"
                iso_live = _compute_isolation_utilization(rows_hint)
                if iso_live is not None:
                    kpi.l1.value = round(iso_live, 2)
            changes.append(f"{name}: isolation utilization → rate % line chart")

        # Categorical bar: sum across all rows inflates L1 — use per-entity avg
        if (
            kpi.chart.type in ("horizontal_bar_chart", "bar_chart")
            and kpi.chart.x_axis_type == "categorical"
            and kpi.chart.x_axis
            and kpi.chart.y_axis
            and (kpi.chart.aggregation or "").lower() == "sum"
        ):
            kpi.chart.aggregation = "avg"
            if resolve_metric_kind(kpi, rows_hint) == "snapshot":
                kpi.l2_projection = None
            changes.append(f"{name}: categorical breakdown sum → avg per entity")

        # Temporal chart missing y_axis — infer from l1.field_name
        if (
            kpi.chart.type in ("line_chart", "area_chart", "stacked_area_chart")
            and not kpi.chart.y_axis
            and not kpi.chart.breakdown_by
            and kpi.l1
        ):
            y_col = find_column(rows_hint, kpi.l1.field_name)
            if y_col and column_is_numeric(rows_hint, y_col):
                kpi.chart.y_axis = y_col
                if kpi.l2_projection and not find_column(rows_hint, kpi.l2_projection.value_field):
                    kpi.l2_projection.value_field = y_col
                changes.append(f"{name}: y_axis ← l1.field_name '{y_col}'")

    # Remove stale aggregate-only L3 on breakdown KPIs (per-series L3 is written by pipeline)
    if kpi.chart.breakdown_by and kpi.l3_forecast is not None and not kpi.l3_forecast_by_series:
        kpi.l3_forecast = None
        if kpi.layer == "L3":
            kpi.layer = "L2" if kpi.l2_projection else "L1"
        changes.append(f"{name}: cleared stale aggregate L3 (awaiting per-series L3)")

    return changes


def normalize_config(
    config: IntelligenceConfig,
    view_cache: dict[str, list[dict]] | None = None,
) -> list[str]:
    """Normalize all KPIs in the config. Returns all change messages."""
    all_changes: list[str] = []
    for pv in config.personas:
        for sec in pv.dashboard_sections:
            for kpi in sec.kpis:
                all_changes.extend(
                    normalize_kpi(kpi, kpi_name=kpi.name, view_cache=view_cache)
                )
    if all_changes:
        log.info("Metric contract: %d normalization(s)", len(all_changes))
        for msg in all_changes:
            log.info("  • %s", msg)
    return all_changes
