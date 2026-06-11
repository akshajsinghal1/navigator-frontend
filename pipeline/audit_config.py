"""
pipeline/audit_config.py
────────────────────────
Generic post-pipeline KPI config audit — no workbook-specific rules.

All checks derive from the metric contract, chart schema, and row shape.
Works for any workbook that produces an IntelligenceConfig.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args

from schemas.config import CHART_TYPES, IntelligenceConfig, KPI
from pipeline.metric_contract import (
    _norm,
    column_is_numeric,
    compute_l1_value,
    find_column,
    metric_kind,
    resolve_chart_aggregation,
    resolve_l3_eligible,
)

log = logging.getLogger(__name__)

Severity = Literal["critical", "warning", "info"]

VALID_CHART_TYPES = set(get_args(CHART_TYPES))

ROW_CHART_TYPES = {
    "line_chart", "bar_chart", "stacked_bar_chart", "horizontal_bar_chart",
    "area_chart", "scatter_chart", "pie_chart", "map_chart", "waterfall_chart",
    "stacked_area_chart", "bubble_chart", "donut_chart", "funnel_chart",
    "treemap_chart", "radar_chart", "table",
}

TEMPORAL_CHART_TYPES = {
    "line_chart", "area_chart", "stacked_area_chart", "stacked_bar_chart",
}

# Issues that mean the KPI cannot render meaningfully — safe to prune after repair.
PRUNE_CODES = frozenset({
    "INVALID_CHART_TYPE",
    "BROKEN_CHART_NO_Y_AXIS",
    "BROKEN_TEMPORAL_NO_X_AXIS",
    "HEATMAP_UNRENDERABLE",
    "NO_RAW_DATA",
})


@dataclass
class AuditIssue:
    code: str
    severity: Severity
    message: str
    kpi_name: str
    persona: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class KpiAuditResult:
    persona: str
    name: str
    chart: str
    l1: str
    field: str
    view: str
    x_axis: str | None
    y_axis: str | None
    aggregation: str | None
    breakdown_by: str | None
    raw_rows: int
    has_l3: bool
    layer: str
    issues: list[AuditIssue] = field(default_factory=list)

    @property
    def critical(self) -> list[AuditIssue]:
        return [i for i in self.issues if i.severity == "critical"]

    @property
    def warnings(self) -> list[AuditIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["issues"] = [i.to_dict() for i in self.issues]
        d["issue_codes"] = [i.code for i in self.issues]
        return d


@dataclass
class AuditReport:
    total_kpis: int = 0
    clean: int = 0
    with_issues: int = 0
    critical_count: int = 0
    warning_count: int = 0
    has_l3: int = 0
    results: list[KpiAuditResult] = field(default_factory=list)
    duplicate_names: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "total_kpis": self.total_kpis,
            "clean": self.clean,
            "with_issues": self.with_issues,
            "critical_count": self.critical_count,
            "warning_count": self.warning_count,
            "has_l3": self.has_l3,
            "duplicate_names": self.duplicate_names,
        }

    def to_dict(self) -> dict[str, Any]:
        bad = [r.to_dict() for r in self.results if r.issues]
        ok = [r.to_dict() for r in self.results if not r.issues]
        return {
            "summary": self.summary(),
            "issues": bad,
            "clean": ok,
        }


def _issue(
    code: str,
    severity: Severity,
    message: str,
    kpi_name: str,
    persona: str,
) -> AuditIssue:
    return AuditIssue(code=code, severity=severity, message=message, kpi_name=kpi_name, persona=persona)


def _name_implies_breakdown(name_n: str) -> bool:
    """True when KPI name suggests a dimensional breakdown (e.g. 'by Department')."""
    if " by " in name_n:
        return True
    return bool(name_n.startswith("by ") or " breakdown" in name_n)


def _name_implies_heatmap(name_n: str) -> bool:
    return "heatmap" in name_n


def _name_implies_trend(name_n: str) -> bool:
    return "trend" in name_n or "over time" in name_n or "forecast trend" in name_n


def audit_kpi(
    kpi: KPI,
    persona: str = "",
    view_cache: dict[str, list[dict]] | None = None,
) -> KpiAuditResult:
    """Run all generic audit rules on a single KPI."""
    issues: list[AuditIssue] = []
    ch = kpi.chart
    l1 = kpi.l1
    rows = kpi.raw_data if isinstance(kpi.raw_data, list) else []
    if view_cache:
        from pipeline.view_rows import rows_for_kpi
        cache_rows, source = rows_for_kpi(kpi, view_cache)
        if source == "cache" and cache_rows:
            rows = cache_rows
    ct = ch.type
    name = kpi.name
    name_n = _norm(name)
    pname = persona or ""

    def add(code: str, severity: Severity, message: str) -> None:
        issues.append(_issue(code, severity, message, name, pname))

    # ── Schema / chart type ───────────────────────────────────────────────
    if ct not in VALID_CHART_TYPES:
        add("INVALID_CHART_TYPE", "critical", f"chart.type '{ct}' is not a valid CHART_TYPES value")

    # ── L1 headline ─────────────────────────────────────────────────────────
    if l1 and l1.value is None:
        add("L1_NULL", "warning", "L1 value is null")

    if l1 and l1.unit == "%" and l1.value is not None:
        try:
            v = float(l1.value)
        except (TypeError, ValueError):
            v = None
        if v is not None:
            if v > 100 or v < -50:
                add("L1_PCT_IMPLAUSIBLE", "warning", f"L1 percentage implausible: {v}")
            elif (
                v > 10
                and l1.field_name
                and "count" in _norm(l1.field_name)
                and "conversion" not in name_n
            ):
                add("L1_COUNT_FIELD_AS_PCT", "warning", f"count field shown as % ({v}%)")

    # ── Raw data vs chart needs ─────────────────────────────────────────────
    if not rows:
        if ct in ROW_CHART_TYPES or ct == "heatmap_chart":
            add("NO_RAW_DATA", "critical", f"{ct} requires raw_data rows but none present")
    else:
        live = compute_l1_value(kpi, rows)
        if live is not None and l1 and l1.value is not None:
            try:
                cfg_v = float(l1.value)
            except (TypeError, ValueError):
                cfg_v = None
            if cfg_v is not None and abs(cfg_v) > 0.01:
                ratio = abs(live) / abs(cfg_v)
                if ratio > 3 or ratio < 0.33:
                    add(
                        "L1_SAMPLE_MISMATCH",
                        "warning",
                        f"config L1={cfg_v:.2f} vs sample recompute={live:.2f}",
                    )

    # ── Name vs chart intent ────────────────────────────────────────────────
    if _name_implies_heatmap(name_n) and ct != "heatmap_chart":
        add("NAME_SAYS_HEATMAP", "info", f"name implies heatmap but chart is {ct}")

    if _name_implies_trend(name_n) and ct in ("kpi_card", "gauge_chart"):
        add("TREND_KPI_SCALAR_CHART", "warning", f"name implies trend but chart is {ct}")

    if _name_implies_breakdown(name_n) and ct in ("kpi_card", "gauge_chart"):
        add("BREAKDOWN_NEEDED", "warning", f"name implies breakdown but chart is {ct}")

    if (
        "conversion" in name_n
        and "rate" in name_n
        and ct != "gauge_chart"
        and ch.y_axis
        and "count" in _norm(ch.y_axis)
    ):
        add("CONVERSION_NOT_RATIO", "warning", "conversion rate uses count field on y_axis, not a ratio")

    if "utilization" in name_n or (l1 and l1.unit == "%" and "rate" in name_n):
        if l1 and l1.unit != "%" and "utilization" in name_n:
            add("UTILIZATION_NOT_PCT", "warning", "utilization KPI should use % unit")

    # ── Heatmap structure ───────────────────────────────────────────────────
    if ct == "heatmap_chart":
        if not ch.y_axis and not ch.breakdown_by:
            add("HEATMAP_MISSING_Y", "critical", "heatmap missing second dimension (y_axis or breakdown_by)")
        if not rows:
            add("HEATMAP_NO_ROWS", "critical", "heatmap has zero raw_data rows")
        elif rows:
            x = find_column(rows, ch.x_axis) if ch.x_axis else None
            y = find_column(rows, ch.y_axis or ch.breakdown_by or "")
            if not x:
                add("HEATMAP_X_MISSING", "critical", f"x_axis '{ch.x_axis}' not found in data")
            if not y:
                add("HEATMAP_Y_MISSING", "critical", "heatmap has only one usable dimension in sample rows")
            if any(i.code.startswith("HEATMAP_") and i.severity == "critical" for i in issues):
                pass
            elif not x or not y:
                add("HEATMAP_UNRENDERABLE", "critical", "heatmap cannot render with current axes")

    # ── Temporal / trend charts ─────────────────────────────────────────────
    is_ts = ch.x_axis_type == "temporal" or ct in TEMPORAL_CHART_TYPES
    if is_ts:
        if not ch.x_axis:
            add("BROKEN_TEMPORAL_NO_X_AXIS", "critical", "temporal chart missing x_axis")
        if ct in TEMPORAL_CHART_TYPES and not ch.y_axis and not ch.breakdown_by:
            add("BROKEN_CHART_NO_Y_AXIS", "critical", f"{ct} missing y_axis and breakdown_by")
        if not rows and ct in TEMPORAL_CHART_TYPES:
            add("NO_ROWS_FOR_TREND", "critical", "temporal chart has no raw_data rows")

    if rows and len(rows) <= 2 and ct in TEMPORAL_CHART_TYPES and not ch.breakdown_by:
        add("TOO_FEW_ROWS", "warning", f"only {len(rows)} rows for temporal series")

    # ── Rich data on scalar chart ───────────────────────────────────────────
    if ct == "kpi_card" and rows and len(rows) > 5 and ch.x_axis:
        add("RICH_DATA_SCALAR", "info", "many rows + x_axis but chart is kpi_card only")

    # ── Metric contract (L2) ────────────────────────────────────────────────
    if kpi.l2_projection:
        df = kpi.l2_projection.date_field
        if df in ("null", "None", "NULL", ""):
            add("INVALID_DATE_FIELD", "warning", f"invalid l2_projection.date_field '{df}'")

        from pipeline.metric_contract import resolve_metric_kind

        kind = resolve_metric_kind(kpi)
        agg = (ch.aggregation or "sum").lower()
        expected = resolve_chart_aggregation(kpi)
        categorical_bar = (
            ch.type in ("horizontal_bar_chart", "bar_chart")
            and ch.x_axis_type == "categorical"
        )
        y_col = find_column(rows, ch.y_axis) if rows and ch.y_axis else None
        row_count_on_id = (
            agg == "count"
            and y_col
            and _norm(y_col) in ("referral id", "referral_id")
        )
        if agg != expected and not (categorical_bar and agg == "avg") and not row_count_on_id:
            add(
                "AGGREGATION_MISMATCH",
                "warning",
                f"chart.aggregation={agg} expected {expected} for {kind} metric",
            )

        if ch.x_axis_type == "temporal" and ch.x_axis and df and df != ch.x_axis:
            add("DATE_FIELD_X_MISMATCH", "warning", f"date_field '{df}' ≠ x_axis '{ch.x_axis}'")

    # ── KPI contract (value_source / forecast_layers) ───────────────────────
    if kpi.value_source == "agent_derived":
        if not kpi.l2_derived or not kpi.l2_derived.formula:
            add("AGENT_DERIVED_NO_FORMULA", "warning", "value_source=agent_derived but l2_derived missing")
        elif kpi.l2_derived.value is None:
            add("AGENT_DERIVED_NO_VALUE", "info", "agent_derived KPI missing l2_derived.value")
        if kpi.layer != "L2":
            add("VALUE_SOURCE_LAYER_MISMATCH", "warning", f"agent_derived but layer={kpi.layer}")

    if is_ts and resolve_l3_eligible(kpi):
        if kpi.l2_projection is None:
            add("TEMPORAL_L3_NO_L2_PROJECTION", "warning", "temporal L3-eligible KPI missing l2_projection")
        elif "l2_projection" not in kpi.forecast_layers and kpi.l2_projection:
            add("MISSING_L2_IN_FORECAST_LAYERS", "warning", "l2_projection set but not in forecast_layers")

    has_l3 = bool(kpi.l3_forecast and kpi.l3_forecast.predictions)
    has_l3_series = bool(kpi.l3_forecast_by_series)

    if has_l3 or has_l3_series:
        if "l3" not in kpi.forecast_layers:
            add("MISSING_L3_IN_FORECAST_LAYERS", "warning", "L3 forecast present but not in forecast_layers")

    if ch.breakdown_by:
        if kpi.l3_forecast and not has_l3_series:
            add(
                "L3_AGGREGATE_ON_BREAKDOWN",
                "warning",
                "aggregate L3 on breakdown chart (per-series L3 preferred)",
            )
        if not has_l3 and not has_l3_series and is_ts and resolve_l3_eligible(kpi):
            add("BREAKDOWN_MISSING_L3", "warning", "breakdown temporal KPI missing L3 forecasts")

    if is_ts and resolve_l3_eligible(kpi) and not has_l3 and not has_l3_series:
        vn = (l1.view_name if l1 else "") or ""
        if not vn.upper().startswith("[TABLE] FORECAST"):
            add("MISSING_L3", "warning", "eligible temporal KPI without L3 forecast")

    if has_l3 and kpi.l3_forecast and kpi.l3_forecast.predictions and l1 and l1.unit == "%":
        p0 = kpi.l3_forecast.predictions[0]
        if abs(p0) > 150:
            add("L3_WRONG_SCALE", "warning", f"L3 first prediction {p0:.1f} implausible for % KPI")

    # ── Field sanity ────────────────────────────────────────────────────────
    if rows and l1 and l1.field_name:
        col = find_column(rows, l1.field_name)
        if col and not column_is_numeric(rows, col):
            add("L1_FIELD_NOT_NUMERIC", "warning", f"l1.field_name '{l1.field_name}' not numeric in sample")

    l1_str = (
        f"{l1.value} {l1.unit}".strip()
        if l1 and l1.value is not None
        else "null"
    )

    return KpiAuditResult(
        persona=pname,
        name=name,
        chart=ct,
        l1=l1_str,
        field=(l1.field_name if l1 else "") or "",
        view=((l1.view_name if l1 else "") or "")[:80],
        x_axis=ch.x_axis,
        y_axis=ch.y_axis,
        aggregation=ch.aggregation,
        breakdown_by=ch.breakdown_by,
        raw_rows=len(rows),
        has_l3=has_l3 or has_l3_series,
        layer=kpi.layer or "L1",
        issues=issues,
    )


def _iter_kpis_with_persona(config: IntelligenceConfig):
    for pv in config.personas:
        role = pv.persona.role if pv.persona else ""
        for sec in pv.dashboard_sections:
            for kpi in sec.kpis:
                yield role, kpi


def audit_config(
    config: IntelligenceConfig,
    view_cache: dict[str, list[dict]] | None = None,
) -> AuditReport:
    """Audit every KPI in the config. Returns structured report."""
    results: list[KpiAuditResult] = []
    seen_names: dict[str, int] = {}

    for persona, kpi in _iter_kpis_with_persona(config):
        seen_names[kpi.name] = seen_names.get(kpi.name, 0) + 1
        results.append(audit_kpi(kpi, persona, view_cache=view_cache))

    duplicates = [name for name, count in seen_names.items() if count > 1]

    critical_count = sum(1 for r in results for i in r.issues if i.severity == "critical")
    warning_count = sum(1 for r in results for i in r.issues if i.severity == "warning")
    with_issues = sum(1 for r in results if r.issues)
    has_l3 = sum(1 for r in results if r.has_l3)

    return AuditReport(
        total_kpis=len(results),
        clean=len(results) - with_issues,
        with_issues=with_issues,
        critical_count=critical_count,
        warning_count=warning_count,
        has_l3=has_l3,
        results=results,
        duplicate_names=duplicates,
    )


def audit_config_messages(config: IntelligenceConfig) -> list[str]:
    """Flat issue strings (CLI / logs). Includes duplicate-name checks."""
    report = audit_config(config)
    messages: list[str] = []
    for dup in report.duplicate_names:
        count = sum(1 for r in report.results if r.name == dup)
        messages.append(f"Duplicate KPI name ({count}×): {dup}")
    for r in report.results:
        for i in r.issues:
            messages.append(f"{r.name}: [{i.code}] {i.message}")
    return messages


def log_audit_report(report: AuditReport, *, phase: str = "audit") -> None:
    """Log audit summary at INFO; details at DEBUG."""
    s = report.summary()
    log.info(
        "%s: %d KPIs — %d clean, %d with issues (%d critical, %d warning), %d with L3",
        phase,
        s["total_kpis"],
        s["clean"],
        s["with_issues"],
        s["critical_count"],
        s["warning_count"],
        s["has_l3"],
    )
    for r in report.results:
        for i in r.critical:
            log.warning("  CRITICAL [%s] %s — %s", i.code, r.name, i.message)
    for r in report.results:
        for i in r.warnings[:3]:  # cap per-KPI noise in INFO
            log.info("  WARN [%s] %s — %s", i.code, r.name, i.message)


def save_audit_report(
    report: AuditReport,
    path: Path | str,
    *,
    config_name: str = "",
    normalizer_fixes: list[str] | None = None,
    phase: str = "",
) -> Path:
    """Write JSON audit report for debugging / QA handoff."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    payload["config"] = config_name
    payload["phase"] = phase
    if normalizer_fixes is not None:
        payload["normalizer_fixes"] = normalizer_fixes
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def prune_critical_kpis(
    config: IntelligenceConfig,
    view_cache: dict[str, list[dict]] | None = None,
) -> list[str]:
    """Delegate to emit gate (one rejection contract for pipeline + CLI)."""
    from pipeline.config_emit import reject_invalid_kpis_at_emit
    return reject_invalid_kpis_at_emit(config, view_cache)


def validate_supplementary_kpi(kpi_raw: dict) -> list[str]:
    """
    Pre-inject validation for QA supplementary KPIs (generic rules).
    Returns rejection reasons; empty list = OK to inject.
    """
    reasons: list[str] = []
    name = kpi_raw.get("name", "unnamed")

    raw_type = (kpi_raw.get("chart_type") or "kpi_card").lower()
    _TYPE_NORM = {
        "line": "line_chart", "bar": "bar_chart", "area": "area_chart",
        "gauge": "gauge_chart", "heatmap": "heatmap_chart",
        "scatter": "scatter_chart", "pie": "pie_chart",
        "horizontal_bar": "horizontal_bar_chart",
        "stacked_bar": "stacked_bar_chart", "metric": "kpi_card",
    }
    chart_type = _TYPE_NORM.get(raw_type, raw_type)

    if chart_type not in VALID_CHART_TYPES:
        reasons.append(f"invalid chart_type '{kpi_raw.get('chart_type')}'")

    l1_value = kpi_raw.get("l1_value")
    if l1_value is None:
        reasons.append("l1_value is null")

    view = kpi_raw.get("l1_view_name", "")
    field = kpi_raw.get("l1_field_name", "")
    if not view or not field:
        reasons.append("missing l1_view_name or l1_field_name")

    x_axis = kpi_raw.get("x_axis")
    y_axis = kpi_raw.get("y_axis")
    axis_type = (kpi_raw.get("x_axis_type") or "").lower()
    if axis_type in ("date", "time", "datetime"):
        axis_type = "temporal"

    name_n = _norm(name)
    if chart_type in TEMPORAL_CHART_TYPES or axis_type == "temporal" or _name_implies_trend(name_n):
        if not x_axis:
            reasons.append("temporal/trend KPI missing x_axis")
        if not y_axis and chart_type in TEMPORAL_CHART_TYPES:
            reasons.append(f"{chart_type} missing y_axis")

    if chart_type == "heatmap_chart" and not y_axis:
        reasons.append("heatmap missing y_axis (second dimension)")

    if _name_implies_breakdown(name_n) and chart_type in ("kpi_card", "gauge_chart"):
        reasons.append("breakdown KPI must not be kpi_card/gauge")

    if chart_type in TEMPORAL_CHART_TYPES and not l1_value:
        reasons.append("temporal KPI requires l1_value")

    return reasons


def main() -> None:
    import sys
    from pipeline.metric_contract import normalize_config

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if not path:
        candidates = list(Path("output").glob("intelligence_config_*.json"))
        path = max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None
    if not path or not path.exists():
        print("No config file found")
        sys.exit(1)

    config = IntelligenceConfig.from_json(path.read_text(encoding="utf-8"))
    fixes = normalize_config(config)
    report = audit_config(config)

    print(f"Config: {path}")
    if fixes:
        print(f"\nNormalizer applied {len(fixes)} fix(es):")
        for f in fixes:
            print(f"  • {f}")

    s = report.summary()
    print(f"\n{s['total_kpis']} KPIs — {s['clean']} clean, {s['with_issues']} with issues")
    print(f"  critical: {s['critical_count']}, warnings: {s['warning_count']}, L3: {s['has_l3']}")

    if report.duplicate_names:
        print("\nDuplicates:")
        for d in report.duplicate_names:
            print(f"  • {d}")

    bad = [r for r in report.results if r.issues]
    if bad:
        print(f"\n{len(bad)} KPI(s) with issues:")
        for r in bad:
            print(f"\n  [{r.persona}] {r.name} ({r.chart}, {r.raw_rows} rows)")
            for i in r.issues:
                print(f"    {i.severity.upper()} [{i.code}] {i.message}")
    else:
        print("\nNo issues found.")

    sys.exit(1 if s["critical_count"] else 0)


if __name__ == "__main__":
    main()
