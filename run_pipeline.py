"""
run_pipeline.py
───────────────
CLI entry point — run the full Navigator pipeline end-to-end.

Usage
─────
  # Run with live Tableau extraction:
  python run_pipeline.py

  # Run with existing inventory JSON (skip extraction):
  python run_pipeline.py --inventory output/inventory_Superstore_*.json

  # Specify a workbook:
  python run_pipeline.py --workbook MyWorkbook

Requires
────────
  ANTHROPIC_API_KEY  in .env
  TABLEAU_*          in .env
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Run the Navigator intelligence pipeline")
    parser.add_argument(
        "--workbook", "-w",
        default=os.environ.get("TARGET_WORKBOOK_CONTENT_URL", "Superstore"),
        help="Workbook content URL (default: from .env)",
    )
    parser.add_argument(
        "--inventory", "-i",
        default=None,
        help="Path to existing inventory JSON (skips extraction)",
    )
    parser.add_argument(
        "--output-dir", "-o",
        default="output",
        help="Directory to save Intelligence Config JSON",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip Tableau auth — use cached inventory only (Gemini still runs)",
    )
    args = parser.parse_args()

    # Validate required env vars
    if not os.environ.get("GEMINI_API_KEY"):
        sys.stderr.write(
            "Missing required env var: GEMINI_API_KEY\n"
            "Add it to .env: GEMINI_API_KEY=AIza...\n"
        )
        return 2

    tableau_vars = ["TABLEAU_PAT_NAME", "TABLEAU_PAT_SECRET",
                    "TABLEAU_SERVER_URL", "TABLEAU_SITE_NAME"]
    missing = [v for v in tableau_vars if not os.environ.get(v)]
    if missing:
        sys.stderr.write(f"Missing required env vars: {', '.join(missing)}\n")
        return 2

    logging.basicConfig(
        level  = logging.INFO,
        format = "%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger(__name__)

    # If inventory path not provided, check for latest in output/
    inventory_path = args.inventory
    if inventory_path is None:
        # Only reuse a cached inventory if it belongs to the SAME workbook.
        # Match by checking the workbook.content_url field inside the JSON,
        # falling back to a filename substring match for speed.
        workbook_key = args.workbook.lower()
        candidates = sorted(
            Path(args.output_dir).glob("inventory_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for candidate in candidates:
            # Quick filename check first (avoids reading every file)
            if workbook_key not in candidate.name.lower():
                continue
            # Confirm by reading the content_url from the JSON
            try:
                wb_meta = json.loads(candidate.read_text(encoding="utf-8")).get("workbook", {})
                stored_url = (wb_meta.get("content_url") or "").lower()
                stored_name = (wb_meta.get("name") or "").lower()
                if workbook_key in stored_url or workbook_key in stored_name:
                    inventory_path = str(candidate)
                    log.info("Using cached inventory for '%s': %s", args.workbook, candidate)
                    break
            except Exception:
                continue
        if inventory_path is None:
            log.info("No matching cached inventory for '%s' — will extract from Tableau", args.workbook)

    from pipeline.runner import PipelineRunner

    runner = PipelineRunner.from_env()
    config, path = runner.run_and_save(
        workbook_content_url    = args.workbook,
        output_dir              = args.output_dir,
        existing_inventory_path = inventory_path,
        offline                 = args.offline,
    )

    print(f"\n{'='*60}")
    print(f"  Intelligence Config saved to: {path}")
    print(f"{'='*60}")
    print(f"  Workbook  : {config.workbook.name}")
    print(f"  Objective : {config.objective}")
    print(f"  Personas  : {len(config.personas)}")
    print()
    for pv in config.personas:
        total_kpis = sum(len(s.kpis) for s in pv.dashboard_sections)
        print(f"  Persona: {pv.persona.role}")
        print(f"    Focus: {', '.join(pv.persona.focus_areas)}")
        for sec in pv.dashboard_sections:
            print(f"    [{sec.title}]")
            for kpi in sec.kpis:
                l1_str = ""
                if kpi.l1 and kpi.l1.value is not None:
                    l1_str = f" = {kpi.l1.value:,.2f} {kpi.l1.unit}"
                trend_str = ""
                if kpi.trend_direction and kpi.trend_pct is not None:
                    arrow = "up" if kpi.trend_direction == "up" else ("down" if kpi.trend_direction == "down" else "flat")
                    trend_str = f" [{arrow} {kpi.trend_pct:+.1f}%]"
                chart_str = f" ({kpi.chart.type}"
                if kpi.chart.x_axis_type:
                    chart_str += f", {kpi.chart.x_axis_type}"
                if kpi.chart.aggregation:
                    chart_str += f", {kpi.chart.aggregation}"
                chart_str += ")"
                l2_str = " [L2: formula captured]" if kpi.l2 and kpi.l2.formula else ""
                print(f"      - {kpi.name}{l1_str}{trend_str}{chart_str}{l2_str}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
