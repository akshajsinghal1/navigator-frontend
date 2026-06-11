"""
run_l3.py
──────────
Standalone script that patches L3 TimesFM forecasts onto an existing
Intelligence Config — no need to re-run the full pipeline.

Steps:
  1. Load the latest (or specified) config from output/
  2. normalize_config() — metric contract fixes
  3. Fetch Hyper + sheet view data for every KPI view_name
  4. Run run_l3_forecasts() — per-series + aggregate forecasts
  5. Save patched config back to output/

Usage:
  python run_l3.py                           # uses latest config in output/
  python run_l3.py output/my_config.json     # uses a specific config file
"""

from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("run_l3")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _latest_config(output_dir: Path, workbook_hint: str | None = None) -> Path:
    from api.config_files import latest_intelligence_config_path

    if workbook_hint:
        p = latest_intelligence_config_path(output_dir, workbook_hint)
        if p:
            return p
    candidates = list(output_dir.glob("intelligence_config_*.json"))
    if not candidates:
        raise FileNotFoundError(f"No intelligence_config_*.json found in {output_dir}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def main() -> None:
    output_dir = Path("output")

    if len(sys.argv) > 1:
        config_path = Path(sys.argv[1])
    else:
        config_path = _latest_config(output_dir)
    log.info("Loading config: %s", config_path)

    from schemas.config import IntelligenceConfig
    from pipeline.config_emit import post_process_config

    config = IntelligenceConfig.from_json(config_path.read_text(encoding="utf-8"))

    from tableau.connector import TableauConnector
    from pipeline.l3_cache import populate_l3_view_cache

    content_url = config.workbook.name
    with TableauConnector(
        server_url=os.environ["TABLEAU_SERVER_URL"],
        site_name=os.environ.get("TABLEAU_SITE_NAME", ""),
        pat_name=os.environ["TABLEAU_PAT_NAME"],
        pat_secret=os.environ["TABLEAU_PAT_SECRET"],
    ) as conn:
        wb_meta = conn.get_workbook_by_content_url(content_url)
        workbook_luid = wb_meta["luid"]
        log.info("Workbook: %s  (luid=%s)", wb_meta["name"], workbook_luid)
        view_data_cache = populate_l3_view_cache(config, workbook_luid, connector=conn)

    changes = post_process_config(config, view_data_cache)
    if changes:
        log.info("Post-process applied %d fix(es) before L3", len(changes))

    from pipeline.l3_forecaster import run_l3_forecasts
    l3_count = run_l3_forecasts(config, view_data_cache)
    log.info("L3 complete: %d KPIs got forecasts", l3_count)

    if l3_count == 0:
        log.warning("No L3 forecasts generated — check logs above for reasons")

    ts   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = config_path.stem.replace("intelligence_config_", "")
    stem = re.sub(r"(_\d{8}T\d{6}Z)+$", "", stem)
    stem = re.sub(r"_l3$", "", stem)

    out_ts = output_dir / f"intelligence_config_{stem}_{ts}.json"
    out_l3 = output_dir / f"intelligence_config_{stem}_l3.json"

    out_ts.write_text(config.to_json(), encoding="utf-8")
    out_l3.write_text(config.to_json(), encoding="utf-8")
    log.info("Patched config saved → %s", out_ts)
    log.info("Canonical L3 config  → %s", out_l3)

    total_kpis = sum(len(sec.kpis) for pv in config.personas for sec in pv.dashboard_sections)
    l3_kpis = sum(
        1 for pv in config.personas for sec in pv.dashboard_sections for kpi in sec.kpis
        if kpi.l3_forecast is not None or kpi.l3_forecast_by_series
    )
    series_kpis = sum(
        1 for pv in config.personas for sec in pv.dashboard_sections for kpi in sec.kpis
        if kpi.l3_forecast_by_series
    )
    log.info("Summary: %d/%d KPIs have L3 (%d with per-series)", l3_kpis, total_kpis, series_kpis)


if __name__ == "__main__":
    main()
