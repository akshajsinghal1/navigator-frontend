"""Audit all KPIs in the latest intelligence config (CLI wrapper)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo.hardcoded.hyper_cache import load_hyper_view_cache
from pipeline.audit_config import audit_config, log_audit_report, save_audit_report
from pipeline.config_emit import post_process_config
from schemas.config import IntelligenceConfig


def _latest_config(output: Path) -> Path | None:
    candidates = list(output.glob("intelligence_config_*.json"))
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def main() -> None:
    output = ROOT / "output"
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _latest_config(output)
    if not path or not path.exists():
        print("No config found")
        sys.exit(1)

    print(f"Auditing: {path.name}\n")
    cfg = IntelligenceConfig.model_validate_json(path.read_text(encoding="utf-8"))
    hyper_cache = load_hyper_view_cache()
    fixes = post_process_config(cfg, hyper_cache)
    report = audit_config(cfg, hyper_cache)
    log_audit_report(report, phase="CLI audit")

    s = report.summary()
    print(f"Total KPIs: {s['total_kpis']}")
    print(f"Clean: {s['clean']} | With issues: {s['with_issues']} | L3: {s['has_l3']}")
    print(f"Critical: {s['critical_count']} | Warnings: {s['warning_count']}")
    if fixes:
        print(f"Post-process applied {len(fixes)} fix(es)")

    bad = [r for r in report.results if r.issues]
    if bad:
        print("\n" + "=" * 80)
        print("KPIs WITH ISSUES")
        print("=" * 80)
        for k in bad:
            print(f"\n{k.persona} > {k.name}")
            print(f"  Chart: {k.chart} | x={k.x_axis} | y={k.y_axis} | rows={k.raw_rows}")
            for i in k.issues:
                print(f"  ! [{i.severity}] {i.code}: {i.message}")

    save_audit_report(
        report,
        output / "kpi_audit_report.json",
        config_name=path.name,
        normalizer_fixes=fixes,
        phase="cli",
    )
    print(f"\nFull report: output/kpi_audit_report.json")
    sys.exit(1 if s["critical_count"] else 0)


if __name__ == "__main__":
    main()
