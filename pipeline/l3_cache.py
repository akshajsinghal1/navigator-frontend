"""
pipeline/l3_cache.py
────────────────────
Build view_data_cache for L3 forecasting — Hyper tables + live sheet views.
"""

from __future__ import annotations

import logging
import os
import tempfile
import zipfile
from pathlib import Path

from schemas.config import IntelligenceConfig

log = logging.getLogger(__name__)


def collect_kpi_view_names(config: IntelligenceConfig) -> set[str]:
    names: set[str] = set()
    for pv in config.personas:
        for sec in pv.dashboard_sections:
            for kpi in sec.kpis:
                vn = kpi.l1.view_name if kpi.l1 else None
                if vn:
                    names.add(vn)
    return names


def populate_l3_view_cache(
    config: IntelligenceConfig,
    workbook_luid: str,
    *,
    connector=None,
) -> dict[str, list[dict]]:
    """
    Fetch rows for every KPI view_name into a cache dict.

    Uses Hyper extract for ``[TABLE] …`` keys and VDS/connector for sheet views.
    """
    from pipeline.hyper_extractor import _read_hyper

    view_names = collect_kpi_view_names(config)
    table_views = {v for v in view_names if v.startswith("[TABLE]")}
    sheet_views = view_names - table_views
    cache: dict[str, list[dict]] = {}

    if table_views:
        from tableau.connector import TableauConnector

        conn = connector or TableauConnector(
            server_url=os.environ["TABLEAU_SERVER_URL"],
            site_name=os.environ.get("TABLEAU_SITE_NAME", ""),
            pat_name=os.environ["TABLEAU_PAT_NAME"],
            pat_secret=os.environ["TABLEAU_PAT_SECRET"],
        )
        close_conn = connector is None
        try:
            if close_conn:
                conn.__enter__()
            with tempfile.TemporaryDirectory() as tmpdir:
                dl_path = conn.server.workbooks.download(
                    workbook_luid,
                    filepath=os.path.join(tmpdir, "wb"),
                    include_extract=True,
                )
                with zipfile.ZipFile(dl_path) as z:
                    hyper_files = [f for f in z.namelist() if f.endswith(".hyper")]
                    if hyper_files:
                        z.extract(hyper_files[0], tmpdir)
                        hyper_path = os.path.join(tmpdir, hyper_files[0])
                        tables = _read_hyper(hyper_path, sample_rows=1, max_full_rows=0)
                        for t in tables:
                            key = f"[TABLE] {t.table_name}"
                            if key in table_views:
                                cache[key] = t.full_rows
                                log.info("  Hyper %-40s %d rows", key, len(t.full_rows))
                    else:
                        log.warning("No .hyper in workbook — [TABLE] views unavailable")
        finally:
            if close_conn:
                conn.__exit__(None, None, None)

    if sheet_views:
        try:
            from tableau.vds import VdsClient
            vds = VdsClient.from_env()
            vds.sign_in()
            for vn in sorted(sheet_views):
                try:
                    rows = vds.get_view_data_by_name(workbook_luid, vn, max_rows=100_000) or []
                    cache[vn] = rows
                    log.info("  Sheet %-40s %d rows", vn, len(rows))
                except Exception as exc:
                    log.warning("  Sheet '%s' fetch failed: %s", vn, exc)
                    cache[vn] = []
        except Exception as exc:
            log.warning("VDS unavailable for sheet views: %s", exc)

    log.info(
        "L3 cache: %d/%d views (%d rows total)",
        len(cache), len(view_names),
        sum(len(v) for v in cache.values()),
    )
    return cache
