"""
Build the pinned NAVIGATOR_DEMO config from the latest pipeline run + hardcoded patches.

Usage:
  python scripts/build_demo_snapshot.py
  python scripts/build_demo_snapshot.py output/intelligence_config_*.json
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo.hardcoded.hyper_cache import load_hyper_view_cache
from demo.hardcoded.patches import (
    apply_demo_patches,
    finalize_demo_l3,
    force_facility_labels,
    load_facility_labels,
)
from pipeline.audit_config import audit_config, save_audit_report
from pipeline.config_emit import post_l3_finalize_config, post_process_config
from pipeline.metric_contract import compute_l1_value
from pipeline.view_rows import rows_for_kpi
from schemas.config import IntelligenceConfig, L3Forecast

OUTPUT = ROOT / "output"
DEMO_DIR = OUTPUT / "demo"
DEMO_BASENAME = "intelligence_config_NAVIGATOR_DEMO_20260610.json"


def _kpi_map_by_name(config: dict | IntelligenceConfig) -> dict[str, dict]:
    """Build name → KPI dict from raw JSON or model."""
    if isinstance(config, IntelligenceConfig):
        raw = json.loads(config.model_dump_json())
    else:
        raw = config
    out: dict[str, dict] = {}
    for pv in raw.get("personas", []):
        for sec in pv.get("dashboard_sections", []):
            for kpi in sec.get("kpis", []):
                out[kpi["name"]] = kpi
    return out


def restore_l3_from_source(cfg: IntelligenceConfig, source: dict) -> int:
    """Copy L3 forecasts from pipeline source when TimesFM refresh did not run."""
    src = _kpi_map_by_name(source)
    restored = 0
    for pv in cfg.personas:
        for sec in pv.dashboard_sections:
            for kpi in sec.kpis:
                sk = src.get(kpi.name)
                if not sk:
                    continue
                has = sk.get("l3_forecast") or sk.get("l3_forecast_by_series")
                if not has:
                    continue
                if sk.get("l3_forecast"):
                    kpi.l3_forecast = L3Forecast.model_validate(sk["l3_forecast"])
                if sk.get("l3_forecast_by_series"):
                    kpi.l3_forecast_by_series = {
                        k: L3Forecast.model_validate(v)
                        for k, v in sk["l3_forecast_by_series"].items()
                    }
                kpi.layer = "L3"
                restored += 1
    return restored


def _latest_config() -> Path:
    candidates = [
        p for p in OUTPUT.glob("intelligence_config_*.json")
        if "NAVIGATOR_DEMO" not in p.name.upper()
    ]
    if not candidates:
        raise FileNotFoundError("No intelligence_config_*.json in output/")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else _latest_config()
    if not src.is_file():
        print(f"Not found: {src}")
        sys.exit(1)

    raw = json.loads(src.read_text(encoding="utf-8"))
    patched, changes = apply_demo_patches(raw)

    hyper_cache = load_hyper_view_cache()
    cfg = IntelligenceConfig.model_validate(patched)
    if hyper_cache:
        pp = post_process_config(cfg, hyper_cache)
        changes.extend(pp)
        l3_n = 0
        try:
            from pipeline.l3_forecaster import run_l3_forecasts
            l3_n = run_l3_forecasts(cfg, hyper_cache)
            if l3_n:
                changes.append(f"L3: refreshed {l3_n} KPI(s) from Hyper data")
        except Exception as exc:
            changes.append(f"L3: skipped ({exc})")
        if l3_n == 0:
            restored = restore_l3_from_source(cfg, raw)
            if restored:
                changes.append(
                    f"L3: kept/restored {restored} forecast(s) from source "
                    "(TimesFM refresh unavailable in this Python env)"
                )
        l1_fixed = 0
        for pv in cfg.personas:
            for sec in pv.dashboard_sections:
                for kpi in sec.kpis:
                    if not kpi.l1:
                        continue
                    rows, row_src = rows_for_kpi(kpi, hyper_cache)
                    if not rows or row_src == "sample":
                        continue
                    v = compute_l1_value(kpi, rows)
                    if v is not None and kpi.l1.value != v:
                        kpi.l1.value = round(v, 4)
                        l1_fixed += 1
        if l1_fixed:
            changes.append(f"L1: recomputed {l1_fixed} value(s) from Hyper cache")
        changes.extend(post_l3_finalize_config(cfg, hyper_cache))
        fl = force_facility_labels(cfg)
        if fl:
            changes.extend(fl)
    else:
        changes.append("Hyper cache: unavailable — skipped post-process / L3 refresh")

    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    dest = DEMO_DIR / DEMO_BASENAME
    out = json.loads(cfg.model_dump_json())
    changes.extend(finalize_demo_l3(out))
    # IntelligenceConfig model does not include demo metadata — preserve for the UI.
    out["demo"] = {
        "snapshot": "hardcoded_20260610",
        "facility_labels": load_facility_labels(),
    }
    dest.write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    report = audit_config(cfg, hyper_cache)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    save_audit_report(
        report,
        DEMO_DIR / f"kpi_audit_report_DEMO_{ts}.json",
        config_name=dest.name,
        phase="demo_snapshot",
    )

    kpis = sum(len(s.kpis) for p in cfg.personas for s in p.dashboard_sections)
    s = report.summary()
    readme = DEMO_DIR / "DEMO_README.txt"
    readme.write_text(
        f"""Navigator demo config — hardcoded snapshot
============================================

Pinned file:
  output/demo/{DEMO_BASENAME}

Source run:
  {src.name}

Built: {datetime.now(timezone.utc).isoformat()}
KPIs: {kpis} | L3: {s['has_l3']} | audit clean: {s['clean']}/{s['total_kpis']}

Load demo:
  http://localhost:5173/?workbook=NAVIGATOR_DEMO

Rebuild after a new pipeline run:
  python scripts/build_demo_snapshot.py

Hardcoded patches live in demo/hardcoded/ (not used by production pipeline).
""",
        encoding="utf-8",
    )

    print(f"Demo snapshot -> {dest}")
    print(f"  source: {src.name}")
    print(f"  KPIs: {kpis}")
    print(f"  patches: {len(changes)}")
    for line in changes[:20]:
        print(f"    - {line}")
    if len(changes) > 20:
        print(f"    ... and {len(changes) - 20} more")
    print(f"  audit: {s['clean']} clean, {s['with_issues']} with issues")
    print(f"  Load: http://localhost:5173/?workbook=NAVIGATOR_DEMO")


if __name__ == "__main__":
    main()
