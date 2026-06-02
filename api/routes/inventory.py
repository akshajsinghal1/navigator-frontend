"""
api/routes/inventory.py
────────────────────────
GET /inventory/{company_id}

Returns a structured summary of what Navigator read from Tableau during the
pipeline run — views, data sources, fields, parameters, and what was generated
(personas + KPI count).

This powers the InventoryScreen shown between the pipeline and the dashboard,
giving users a transparent view of what was discovered before they see the
AI-generated intelligence layer.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{company_id}")
def get_inventory(company_id: str):
    """
    Return inventory stats for a company's Tableau workbook.

    Reads the latest inventory_*.json and intelligence_config_*.json for the
    given company_id (matched by substring on the filename).
    """
    key        = company_id.lower().replace("-", "_").replace(" ", "_")
    output_dir = Path("output")

    if not output_dir.exists():
        raise HTTPException(status_code=404, detail="Output directory not found")

    # ── Find latest inventory file ─────────────────────────────────────────────
    inventory_files = sorted(
        [p for p in output_dir.glob("inventory_*.json") if key in p.name.lower()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    # ── Find latest intelligence config file ───────────────────────────────────
    config_files = sorted(
        [p for p in output_dir.glob("intelligence_config_*.json") if key in p.name.lower()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not config_files:
        raise HTTPException(
            status_code=404,
            detail=f"No pipeline output found for '{company_id}'. Run the pipeline first.",
        )

    # ── Load intelligence config (always available post-pipeline) ──────────────
    try:
        cfg = json.loads(config_files[0].read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read config: {exc}")

    workbook_meta = cfg.get("workbook", {})

    # ── Compute KPI + persona stats from config ────────────────────────────────
    personas = cfg.get("personas", [])
    persona_list = []
    total_kpis   = 0

    for pv in personas:
        p    = pv.get("persona", {})
        kpis = [k for sec in pv.get("dashboard_sections", []) for k in sec.get("kpis", [])]
        total_kpis += len(kpis)
        persona_list.append({
            "role":        p.get("role", ""),
            "focus_areas": p.get("focus_areas", []),
            "kpi_count":   len(kpis),
            "kpi_names":   [k.get("name", "") for k in kpis],
        })

    # ── Load raw inventory if available ───────────────────────────────────────
    views       = []
    datasources = []
    parameters  = []
    total_fields = 0

    if inventory_files:
        try:
            inv = json.loads(inventory_files[0].read_text(encoding="utf-8"))

            views = [
                {"name": v.get("name", ""), "updated_at": v.get("updated_at")}
                for v in inv.get("views", [])
            ]

            for ds in inv.get("embedded_datasources", []):
                fields = ds.get("fields", [])
                total_fields += len(fields)
                datasources.append({
                    "name":        ds.get("name", ""),
                    "field_count": len(fields),
                })

            parameters = [
                {
                    "name":          p.get("name", ""),
                    "current_value": p.get("current_value") or p.get("default_value"),
                    "data_type":     p.get("data_type", ""),
                }
                for p in inv.get("parameters", [])
            ]

        except Exception as exc:
            log.warning("Could not read inventory file: %s", exc)

    # ── If no inventory file, synthesise from config ───────────────────────────
    if not datasources and workbook_meta.get("data_sources"):
        datasources = [{"name": ds, "field_count": None} for ds in workbook_meta["data_sources"]]

    return {
        "company_id":    company_id,
        "workbook_name": workbook_meta.get("name", company_id),
        "generated_at":  cfg.get("generated_at", ""),
        "objective":     cfg.get("objective", ""),

        # Tableau inventory
        "views":        views,
        "view_count":   len(views),
        "datasources":  datasources,
        "total_fields": total_fields,
        "parameters":   parameters,

        # What Navigator generated
        "total_kpis":    total_kpis,
        "persona_count": len(persona_list),
        "personas":      persona_list,
    }
