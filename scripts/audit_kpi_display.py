"""
Per-KPI display audit — simulates frontend headline + chart checks for Now / 7D / 30D.

Usage:
  py -3.12 scripts/audit_kpi_display.py
  py -3.12 scripts/audit_kpi_display.py output/demo/intelligence_config_NAVIGATOR_DEMO_20260610.json
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo.hardcoded.hyper_cache import load_hyper_view_cache
from pipeline.metric_contract import (
    compute_l1_value,
    resolve_l3_breakdown_aggregate,
    compute_l2_projection_value,
    find_column,
    find_date_column,
    resolve_chart_aggregation,
    resolve_l3_eligible,
)
from pipeline.view_rows import rows_for_kpi
from schemas.config import IntelligenceConfig

PERIODS = ("now", "7d", "30d")


@dataclass
class PeriodCheck:
    period: str
    headline: float | None
    expected_layer: str
    chart_ok: bool
    chart_reason: str
    row_count: int
    issues: list[str] = field(default_factory=list)


@dataclass
class KpiDisplayAudit:
    persona: str
    name: str
    chart_type: str
    view: str
    config_l1: str
    has_l3: bool
    l2_method: str | None
    periods: list[PeriodCheck] = field(default_factory=list)
    tile_issues: list[str] = field(default_factory=list)
    modal_issues: list[str] = field(default_factory=list)


def _filter_period(rows: list[dict], kpi, period: str) -> list[dict]:
    if period == "now" or not rows:
        return rows
    days = 7 if period == "7d" else 30
    l2 = kpi.l2_projection
    dc = find_date_column(rows, l2.date_field if l2 else None, kpi.chart.x_axis)
    if not dc:
        return rows
    from datetime import datetime

    dates = []
    for r in rows:
        v = r.get(dc)
        if v is None:
            continue
        try:
            s = str(v)[:10]
            dates.append(datetime.fromisoformat(s))
        except ValueError:
            pass
    if not dates:
        return rows
    cutoff = max(dates).toordinal() - days
    out = []
    for r in rows:
        v = r.get(dc)
        if v is None:
            continue
        try:
            if datetime.fromisoformat(str(v)[:10]).toordinal() >= cutoff:
                out.append(r)
        except ValueError:
            out.append(r)
    return out or rows


def _has_l3_data(kpi) -> bool:
    if kpi.l3_forecast_by_series:
        return True
    return bool(kpi.l3_forecast and kpi.l3_forecast.predictions)


def _l3_value(kpi, period: str) -> float | None:
    if not _has_l3_data(kpi):
        return None
    idx = 6 if period == "7d" else 29
    if kpi.l3_forecast and kpi.l3_forecast.predictions:
        p = kpi.l3_forecast.predictions
        return p[min(idx, len(p) - 1)]
    if kpi.l3_forecast_by_series:
        vals = []
        for fc in kpi.l3_forecast_by_series.values():
            if fc.predictions:
                vals.append(fc.predictions[min(idx, len(fc.predictions) - 1)])
        if not vals:
            return None
        agg = resolve_l3_breakdown_aggregate(kpi)
        if agg == "avg":
            return sum(vals) / len(vals)
        if agg == "max":
            return max(vals)
        return sum(vals)
    return None


def _resolve_headline(kpi, rows: list[dict], period: str) -> tuple[float | None, str]:
    cfg = kpi.l1.value if kpi.l1 else None
    if period == "now":
        v = compute_l1_value(kpi, rows)
        return (v if v is not None else cfg), "L1"

    l3 = _l3_value(kpi, period)
    if l3 is not None and _has_l3_data(kpi):
        return l3, "L3"

    l2 = kpi.l2_projection
    is_temporal = kpi.chart.x_axis_type == "temporal" or kpi.chart.type in (
        "line_chart", "area_chart", "stacked_area_chart", "stacked_bar_chart",
    )
    windowed = _filter_period(rows, kpi, period) if is_temporal else rows

    if is_temporal and l2 and l2.method in ("daily_rate", "growth_rate"):
        proj = compute_l2_projection_value(kpi, rows, 7 if period == "7d" else 30)
        base = compute_l1_value(kpi, rows) or cfg
        if proj is not None and base is not None and abs(base) > 0.01:
            ratio = abs(proj) / abs(base)
            if ratio <= 5 and ratio >= 0.2:
                return proj, "L2"

    v = compute_l1_value(kpi, windowed)
    return (v if v is not None else cfg), "L1"


def _chart_renderable(kpi, rows: list[dict], period: str) -> tuple[bool, str]:
    ch = kpi.chart
    ctype = (ch.type or "kpi_card").lower()
    if ctype in ("kpi_card", "scorecard"):
        return True, "kpi_card — no chart"
    if not rows:
        return False, "no rows"

    chart_rows = _filter_period(rows, kpi, period)
    if not chart_rows:
        return False, "no rows after period filter"

    x_col = find_column(chart_rows, ch.x_axis)
    y_col = find_column(chart_rows, ch.y_axis or (kpi.l1.field_name if kpi.l1 else None))
    by_col = find_column(chart_rows, ch.breakdown_by) if ch.breakdown_by else None

    if ctype == "gauge_chart":
        return True, "gauge"
    if ctype == "heatmap_chart":
        if not x_col:
            return False, f"heatmap missing x ({ch.x_axis})"
        y2 = find_column(chart_rows, ch.y_axis or ch.breakdown_by or "")
        if not y2 or y2 == x_col:
            return False, "heatmap missing second dimension"
        return True, "heatmap ok"

    if ctype == "horizontal_bar_chart":
        if not x_col and not y_col:
            return False, "bar missing axes"
        return True, "horizontal bar ok"

    if ctype in ("line_chart", "area_chart", "stacked_area_chart", "stacked_bar_chart"):
        if not x_col:
            return False, f"temporal missing x ({ch.x_axis})"
        if not y_col and not by_col:
            return False, f"temporal missing y/breakdown"
        dc = find_date_column(chart_rows, ch.x_axis, kpi.l2_projection.date_field if kpi.l2_projection else None)
        if ch.x_axis_type == "temporal" or dc:
            keys = {str(r.get(x_col or dc, ""))[:10] for r in chart_rows if r.get(x_col or dc) is not None}
            if len(keys) <= 1 and not by_col:
                return False, f"degenerate temporal — only {len(keys)} bucket(s)"
        return True, f"series ok ({len(chart_rows)} rows)"

    if not x_col or not y_col:
        return False, f"missing axes x={ch.x_axis} y={ch.y_axis}"
    return True, "chart ok"


def audit_kpi(kpi, persona: str, cache: dict) -> KpiDisplayAudit:
    rows, src = rows_for_kpi(kpi, cache)
    l1s = f"{kpi.l1.value} {kpi.l1.unit}".strip() if kpi.l1 and kpi.l1.value is not None else "null"
    audit = KpiDisplayAudit(
        persona=persona,
        name=kpi.name,
        chart_type=kpi.chart.type,
        view=(kpi.l1.view_name if kpi.l1 else "")[:60],
        config_l1=l1s,
        has_l3=_has_l3_data(kpi),
        l2_method=kpi.l2_projection.method if kpi.l2_projection else None,
    )

    if src == "none" or not rows:
        audit.tile_issues.append("TILE: no viewdata rows — chart/headline empty")
        audit.modal_issues.append("MODAL: viewdata fetch would fail")
        return audit

    if src == "sample":
        audit.tile_issues.append("TILE: using 20-row sample — headline/chart may drift")

    live_l1 = compute_l1_value(kpi, rows)
    if kpi.l1 and kpi.l1.value is not None and live_l1 is not None:
        cfg_v = float(kpi.l1.value)
        if abs(cfg_v) > 0.01:
            ratio = abs(live_l1) / abs(cfg_v)
            if ratio > 10 or ratio < 0.1:
                audit.tile_issues.append(
                    f"TILE: live L1 {live_l1:.2g} vs config {cfg_v:.2g} — headline mismatch"
                )

    eligible_l3 = resolve_l3_eligible(kpi)
    if eligible_l3 and not audit.has_l3 and kpi.chart.x_axis_type == "temporal":
        audit.tile_issues.append("TILE: temporal KPI eligible for L3 but no forecast in config")

    for period in PERIODS:
        headline, layer = _resolve_headline(kpi, rows, period)
        chart_ok, chart_reason = _chart_renderable(kpi, rows, period)
        pc = PeriodCheck(
            period=period,
            headline=headline,
            expected_layer=layer,
            chart_ok=chart_ok,
            chart_reason=chart_reason,
            row_count=len(_filter_period(rows, kpi, period)),
        )
        if period != "now" and not chart_ok and kpi.chart.type not in ("gauge_chart", "kpi_card"):
            pc.issues.append(f"{period}: chart would not render — {chart_reason}")
        if period != "now" and headline is not None and live_l1 is not None:
            if layer == "L1" and period in ("7d", "30d") and kpi.chart.x_axis_type == "temporal":
                if abs(headline - live_l1) < 0.01 * max(abs(live_l1), 1) and audit.has_l3:
                    pc.issues.append(f"{period}: shows L1 but has L3 data — wrong layer?")
        audit.periods.append(pc)

    # Modal-specific
    now_ok, _ = _chart_renderable(kpi, rows, "now")
    d30_ok, d30_reason = _chart_renderable(kpi, rows, "30d")
    if now_ok and not d30_ok and kpi.chart.x_axis_type == "temporal":
        audit.modal_issues.append(f"MODAL 30D: chart breaks — {d30_reason}")
    if kpi.chart.breakdown_by and audit.has_l3 and not kpi.l3_forecast_by_series:
        audit.modal_issues.append("MODAL: breakdown chart but only aggregate L3 — spike risk")
    if kpi.chart.breakdown_by:
        labels = kpi.chart.breakdown_labels or {}
        if labels and any(str(v).startswith("Facility_") for v in labels.values()):
            audit.modal_issues.append("MODAL: breakdown labels still Facility_N placeholders")

    return audit


def main() -> None:
    cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "output/demo/intelligence_config_NAVIGATOR_DEMO_20260610.json"
    cfg = IntelligenceConfig.model_validate(json.loads(cfg_path.read_text(encoding="utf-8")))
    cache = load_hyper_view_cache()

    audits: list[KpiDisplayAudit] = []
    for pv in cfg.personas:
        role = pv.persona.role if pv.persona else ""
        for sec in pv.dashboard_sections:
            for kpi in sec.kpis:
                audits.append(audit_kpi(kpi, role, cache))

    out_path = ROOT / "output/demo/kpi_display_audit.json"
    report = []
    problem_count = 0
    for a in audits:
        issues = a.tile_issues + a.modal_issues
        for p in a.periods:
            issues.extend(p.issues)
        if issues:
            problem_count += 1
        report.append({
            "persona": a.persona,
            "name": a.name,
            "chart": a.chart_type,
            "view": a.view,
            "config_l1": a.config_l1,
            "has_l3": a.has_l3,
            "l2": a.l2_method,
            "tile_issues": a.tile_issues,
            "modal_issues": a.modal_issues,
            "periods": [
                {
                    "period": p.period,
                    "headline": p.headline,
                    "layer": p.expected_layer,
                    "chart_ok": p.chart_ok,
                    "chart_reason": p.chart_reason,
                    "rows": p.row_count,
                    "issues": p.issues,
                }
                for p in a.periods
            ],
            "issue_count": len(issues),
        })

    out_path.write_text(json.dumps({"kpis": report, "total": len(audits), "with_issues": problem_count}, indent=2), encoding="utf-8")

    print(f"Audited {len(audits)} KPIs — {problem_count} with display issues")
    print(f"Report: {out_path}\n")
    for a in audits:
        issues = a.tile_issues + a.modal_issues + [i for p in a.periods for i in p.issues]
        if not issues:
            continue
        print(f"## {a.name} ({a.persona})")
        print(f"   chart={a.chart_type} | L1={a.config_l1} | l3={a.has_l3} | l2={a.l2_method}")
        for i in issues:
            print(f"   - {i}")
        print()


if __name__ == "__main__":
    main()
