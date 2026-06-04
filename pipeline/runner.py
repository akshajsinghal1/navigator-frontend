"""
pipeline/runner.py
───────────────────
End-to-end pipeline runner.

Takes Tableau credentials + workbook content URL → returns IntelligenceConfig.

Flow:
  1. Connect to Tableau (PAT auth)
  2. Extract inventory (via tableau_inventory_extractor)
  3. Filter inventory (via semantic_filter)
  4. Run orchestrator agent
  5. Return Intelligence Config

This module is the single entry point used by:
  - run_pipeline.py  (CLI)
  - api/routes/onboard.py  (HTTP POST /onboard)
  - scheduler/watcher.py  (auto re-run on data change)
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import re

from schemas.config import IntelligenceConfig, KPI, L1Data, ChartSpec, Explanation, DashboardSection
from tableau.connector import StubConnector, TableauConnector
from tableau.semantic_filter import filter_inventory
from tableau.vds       import VdsClient
from pipeline.manifest import build_manifest, WorkbookManifest

log = logging.getLogger(__name__)


def _inject_supplementary_kpis(
    config: IntelligenceConfig,
    supplementary: list[dict],
) -> IntelligenceConfig:
    """
    Add QA agent's supplementary KPIs into the appropriate persona sections.
    Each supplementary KPI specifies a target_persona role.
    """
    if not supplementary:
        return config

    for kpi_raw in supplementary:
        target_role = kpi_raw.get("target_persona", "")
        if not target_role:
            continue

        # Build a KPI object from the QA result
        kpi_id = kpi_raw.get("id") or re.sub(r"[^a-z0-9]+", "_", kpi_raw.get("name", "qa_kpi").lower()).strip("_")
        try:
            l1 = L1Data(
                value      = kpi_raw.get("l1_value"),
                unit       = kpi_raw.get("l1_unit", ""),
                format     = "number",
                view_name  = kpi_raw.get("l1_view_name", ""),
                field_name = kpi_raw.get("l1_field_name", ""),
            )
            # Normalize QA agent output to valid schema values
            _AGG_NORM = {"mean": "avg", "average": "avg", "total": "sum", "cnt": "count"}
            raw_agg = kpi_raw.get("aggregation", "sum") or "sum"
            agg = _AGG_NORM.get(raw_agg.lower(), raw_agg)

            # chart_type: model sometimes emits shorthand like "line", "bar", "area"
            _TYPE_NORM = {
                "line": "line_chart", "bar": "bar_chart", "area": "area_chart",
                "gauge": "gauge_chart", "heatmap": "heatmap_chart",
                "scatter": "scatter_chart", "pie": "pie_chart",
                "horizontal_bar": "horizontal_bar_chart",
                "stacked_bar": "stacked_bar_chart",
            }
            raw_type = kpi_raw.get("chart_type", "kpi_card") or "kpi_card"
            chart_type = _TYPE_NORM.get(raw_type.lower(), raw_type)

            # x_axis_type: "date", "time", "datetime" → "temporal"
            _AXIS_NORM = {"date": "temporal", "time": "temporal", "datetime": "temporal",
                          "string": "categorical", "number": "numeric", "integer": "numeric"}
            raw_axis_type = kpi_raw.get("x_axis_type")
            axis_type = _AXIS_NORM.get((raw_axis_type or "").lower(), raw_axis_type)

            chart = ChartSpec(
                type         = chart_type,
                x_axis       = kpi_raw.get("x_axis"),
                x_axis_type  = axis_type,
                aggregation  = agg,
            )
            explanation = Explanation(
                what           = kpi_raw.get("description", ""),
                why_it_matters = kpi_raw.get("gap_filled", "QA-identified gap"),
            )
            kpi_obj = KPI(
                id          = kpi_id,
                name        = kpi_raw.get("name", kpi_id),
                description = kpi_raw.get("description", ""),
                layer       = "L1",
                l1          = l1,
                chart       = chart,
                explanation = explanation,
            )
        except Exception as exc:
            log.warning("QA KPI build failed for %r: %s", kpi_raw.get("name"), exc)
            continue

        # Find the target persona and inject into a "QA Additions" section
        injected = False
        for pv in config.personas:
            if target_role.lower() in pv.persona.role.lower() or pv.persona.role.lower() in target_role.lower():
                # Add to existing "QA Additions" section or create one
                qa_section = next(
                    (s for s in pv.dashboard_sections if s.id == "qa_additions"),
                    None
                )
                if qa_section is None:
                    qa_section = DashboardSection(
                        id          = "qa_additions",
                        title       = "Additional Insights",
                        description = "KPIs discovered by QA analysis",
                        kpis        = [],
                    )
                    pv.dashboard_sections.append(qa_section)
                qa_section.kpis.append(kpi_obj)
                log.info("QA: added KPI '%s' to persona '%s'", kpi_obj.name, pv.persona.role)
                injected = True
                break

        if not injected:
            # Assign to the first persona if target not found
            if config.personas:
                pv = config.personas[0]
                qa_section = next(
                    (s for s in pv.dashboard_sections if s.id == "qa_additions"),
                    None
                )
                if qa_section is None:
                    qa_section = DashboardSection(
                        id="qa_additions", title="Additional Insights",
                        description="QA-identified gaps", kpis=[],
                    )
                    pv.dashboard_sections.append(qa_section)
                qa_section.kpis.append(kpi_obj)
                log.warning("QA: no persona matched '%s', assigned to '%s'", target_role, pv.persona.role)

    return config


class PipelineRunner:
    """
    Runs the full Navigator pipeline for a single workbook.

    Usage:
        runner = PipelineRunner(creds)
        config = runner.run("Superstore")
        print(config.to_json())
    """

    def __init__(self, creds: dict[str, str]) -> None:
        """
        Args:
            creds: dict with keys:
                tableau_server_url, tableau_site_name,
                tableau_pat_name, tableau_pat_secret
        """
        self._creds = creds

    # ── main entry point ──────────────────────────────────────────────────────

    def run(
        self,
        workbook_content_url: str,
        existing_inventory_path: str | Path | None = None,
        offline: bool = False,
    ) -> IntelligenceConfig:
        """
        Run the full pipeline.

        Args:
            workbook_content_url   : content URL of the Tableau workbook
            existing_inventory_path: if provided, skip extraction and load this JSON
            offline                : if True, skip Tableau auth — use StubConnector
                                     and read workbook_meta from inventory JSON.
                                     Domain agents will infer KPIs from metadata alone.

        Returns:
            IntelligenceConfig — the assembled intelligence config
        """
        start = time.time()
        log.info("=== Pipeline start: workbook=%s (offline=%s) ===", workbook_content_url, offline)

        # ── step 1: get inventory ────────────────────────────────────────────
        if existing_inventory_path:
            log.info("Loading existing inventory from %s", existing_inventory_path)
            raw_inventory = json.loads(
                Path(existing_inventory_path).read_text(encoding="utf-8")
            )
        else:
            if offline:
                raise ValueError("offline=True requires an existing_inventory_path")
            raw_inventory = self._extract_inventory(workbook_content_url)

        # ── step 2: filter to semantic signal ────────────────────────────────
        log.info("Filtering inventory to semantic signal")
        filtered = filter_inventory(raw_inventory)
        log.info(
            "Inventory filtered: %d datasources, %d parameters, %d sheets",
            len(filtered.get("embedded_datasources", [])),
            len(filtered.get("parameters", [])),
            len(filtered.get("sheets", [])),
        )

        # ── step 3: pick a connector (VdsClient unified — auth-stable) ────────
        # Why VdsClient instead of TableauConnector (TSC): TSC and VdsClient
        # both auth via PAT, but Tableau Cloud invalidates earlier sessions when
        # a new PAT sign-in happens on the same site. Mixing them inside one
        # pipeline run caused mid-flight 401s during domain agent fetches.
        # VdsClient has drop-in shims (get_workbook_by_content_url, list_views,
        # get_view_data_by_name) so the agents continue to work unchanged.
        if offline:
            wb_raw        = raw_inventory.get("workbook", {})
            wb_meta       = {
                "luid":         wb_raw.get("luid", "offline"),
                "name":         wb_raw.get("name", workbook_content_url),
                "content_url":  wb_raw.get("content_url", workbook_content_url),
                "project_name": wb_raw.get("project_name"),
                "updated_at":   wb_raw.get("updated_at"),
            }
            workbook_luid = wb_meta["luid"]
            connector     = StubConnector()
            log.info("Offline mode — workbook: %s (luid=%s)", wb_meta["name"], workbook_luid)
        else:
            connector     = VdsClient.from_dict(self._creds)
            wb_meta       = None
            workbook_luid = None

        with connector as conn:
            if not offline:
                wb_meta       = conn.get_workbook_by_content_url(workbook_content_url)
                workbook_luid = wb_meta["luid"]
                log.info("Workbook: %s (luid=%s)", wb_meta["name"], workbook_luid)

            # ── step 4: EDA pre-analysis ─────────────────────────────────────
            from pipeline.eda import run_eda
            log.info("Running EDA pre-analysis")
            eda = run_eda(filtered)
            log.info(
                "EDA: %d fields, %d KPI candidates, %d domain clusters",
                eda["summary"]["total_fields"],
                len(eda["top_kpi_candidates"]),
                len(eda["domain_clusters"]),
            )

            # ── step 5: build field manifest (single auth session, no TSC) ────
            manifest: WorkbookManifest | None = None
            available_views: list[str] = []
            if not offline:
                try:
                    manifest = build_manifest(
                        workbook_name = wb_meta["name"],
                        workbook_luid = workbook_luid,
                        inventory     = raw_inventory,
                        vds_client    = conn,    # same client — single session
                    )
                    available_views = [v.name for v in manifest.views if v.columns]
                    reachable_count = sum(
                        1 for f in manifest.all_fields()
                        if f.reachable_via != "unreachable"
                    )
                    log.info(
                        "Manifest built: %d datasources, %d views, %d/%d fields reachable",
                        len(manifest.data_sources),
                        len(manifest.views),
                        reachable_count,
                        len(manifest.all_fields()),
                    )
                except Exception as exc:
                    log.warning("Manifest build failed (continuing without it): %s", exc)
                    manifest = None
                    views_meta = conn.list_views(workbook_luid)
                    available_views = [v["name"] for v in views_meta]

            # ── step 6: run orchestrator (uses same `conn` — VdsClient) ───────
            from agents.orchestrator import OrchestratorAgent

            orchestrator = OrchestratorAgent(
                connector       = conn,           # VdsClient with TSC-compatible shims
                workbook_luid   = workbook_luid,
                workbook_meta   = wb_meta,
                available_views = available_views,
                manifest        = manifest,
            )

            log.info("Running orchestrator agent")
            config = orchestrator.run_pipeline(filtered, eda=eda)

            # ── step 7: QA agent — review config, find gaps, add missing KPIs ──
            try:
                from agents.qa_agent import QAAgent
                from agents.orchestrator import _infer_persona_level

                log.info("Running QA agent to review config and find gaps")
                qa = QAAgent(connector=conn, workbook_luid=workbook_luid)
                qa_result = qa.review(
                    config          = config,
                    eda             = eda or {},
                    available_views = available_views,
                )

                supplementary = qa_result.get("supplementary_kpis", [])
                gaps          = qa_result.get("gaps_found", [])

                if gaps:
                    log.info("QA agent found %d gaps: %s", len(gaps), gaps[:3])

                if supplementary:
                    log.info("QA agent proposing %d supplementary KPIs", len(supplementary))
                    config = _inject_supplementary_kpis(config, supplementary)

            except Exception as exc:
                # QA agent failure should not abort the pipeline
                log.warning("QA agent failed (non-fatal): %s", exc)

        elapsed = time.time() - start
        log.info("=== Pipeline complete in %.1fs ===", elapsed)

        return config

    # ── inventory extraction ──────────────────────────────────────────────────

    def _extract_inventory(self, workbook_content_url: str) -> dict[str, Any]:
        """Extract full inventory via the existing extractor."""
        from tableau_inventory_extractor import TableauInventoryExtractor, WorkbookNotFoundError

        extractor = TableauInventoryExtractor(
            server_url  = self._creds["tableau_server_url"],
            site_name   = self._creds["tableau_site_name"],
            pat_name    = self._creds["tableau_pat_name"],
            pat_secret  = self._creds["tableau_pat_secret"],
        )

        with extractor as ex:
            try:
                inventory = ex.extract_workbook_inventory(workbook_content_url)
            except WorkbookNotFoundError as exc:
                raise RuntimeError(f"Workbook not found: {workbook_content_url}") from exc
            path = ex.write_to_json(inventory)
            log.info("Inventory written to %s", path)

        return inventory

    # ── convenience ───────────────────────────────────────────────────────────

    def run_and_save(
        self,
        workbook_content_url: str,
        output_dir: str | Path = "output",
        existing_inventory_path: str | Path | None = None,
        offline: bool = False,
    ) -> tuple[IntelligenceConfig, Path]:
        """
        Run the pipeline and save the config to a JSON file.

        Returns:
            (IntelligenceConfig, path_to_saved_file)
        """
        config = self.run(workbook_content_url, existing_inventory_path, offline=offline)

        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"intelligence_config_{workbook_content_url}_{ts}.json"
        path = output_dir / filename

        path.write_text(config.to_json(), encoding="utf-8")
        log.info("Intelligence Config saved to %s", path)

        return config, path

    @classmethod
    def from_env(cls) -> "PipelineRunner":
        """Create runner from environment variables."""
        import os
        return cls({
            "tableau_server_url": os.environ["TABLEAU_SERVER_URL"],
            "tableau_site_name":  os.environ["TABLEAU_SITE_NAME"],
            "tableau_pat_name":   os.environ["TABLEAU_PAT_NAME"],
            "tableau_pat_secret": os.environ["TABLEAU_PAT_SECRET"],
        })
