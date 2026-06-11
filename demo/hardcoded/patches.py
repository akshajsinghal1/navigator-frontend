"""
demo/hardcoded/patches.py
─────────────────────────
One-off demo snapshot fixes for today's presentation.
Not used by the production pipeline — only scripts/build_demo_snapshot.py.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LABELS_PATH = Path(__file__).resolve().parent / "facility_labels.json"

_CHART_PREFIX = re.compile(
    r"^Chart:\s*[\w_]+\s*[-–—]\s*",
    re.IGNORECASE,
)


def load_facility_labels() -> dict[str, str]:
    data = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
    # Uppercase forecast-table keys share the same ids.
    out = dict(data)
    for k, v in list(data.items()):
        out[f"Facility_{k}"] = v
    return out


def strip_chart_prefix(text: str | None) -> str | None:
    if not text:
        return text
    cleaned = _CHART_PREFIX.sub("", text.strip())
    return cleaned or text


def _merge_labels(existing: dict[str, str] | None, facility: dict[str, str]) -> dict[str, str]:
    merged = dict(facility)
    if existing:
        for k, v in existing.items():
            if k in facility:
                merged[k] = facility[k]
            elif v.startswith("Facility_") and k in facility:
                merged[k] = facility[k]
            else:
                merged[k] = v
    return merged


def _patch_chart(kpi: dict[str, Any], facility: dict[str, str]) -> None:
    ch = kpi.get("chart")
    if not ch:
        return
    hints = [ch.get("x_axis"), ch.get("y_axis"), ch.get("breakdown_by"), ch.get("color_by")]
    if any(h and "facility" in str(h).lower() for h in hints):
        ch["breakdown_labels"] = _merge_labels(ch.get("breakdown_labels"), facility)


def _kpi_by_name(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for pv in config.get("personas", []):
        for sec in pv.get("dashboard_sections", []):
            for kpi in sec.get("kpis", []):
                out[kpi["name"]] = kpi
    return out


# Per-KPI surgical fixes (demo-only).
_KPI_PATCHES: dict[str, dict[str, Any]] = {
    "Staffed vs Licensed Beds": {
        "description": "Staffed beds compared with licensed capacity for each facility.",
        "chart": {
            "type": "horizontal_bar_chart",
            "x_axis": "facility_name",
            "y_axis": "staffed_beds",
            "x_axis_type": "categorical",
            "aggregation": "sum",
            "sort_order": "desc",
            "breakdown_by": None,
            "color_by": None,
            "sort_by": None,
            "notes": None,
        },
    },
    "Agency vs Overtime Hours": {
        "description": "Overtime hours trend over time — one line per facility.",
        "chart": {
            "type": "line_chart",
            "x_axis": "requirement_date",
            "y_axis": "overtime_hours",
            "x_axis_type": "temporal",
            "aggregation": "sum",
            "sort_order": "none",
            "breakdown_by": "facility_id",
            "notes": None,
        },
    },
    "Forecast Occupancy Heatmap": {
        "description": "Predicted occupancy by facility and department.",
        "chart": {
            "type": "heatmap_chart",
            "x_axis": "FACILITY_NAME",
            "y_axis": "DEPARTMENT_NAME",
            "x_axis_type": "categorical",
            "aggregation": "avg",
            "breakdown_by": None,
            "notes": None,
        },
    },
    "Occupancy Heatmap": {
        "description": "Average occupancy by facility and department.",
        "chart": {
            "type": "heatmap_chart",
            "x_axis": "department_name",
            "y_axis": "facility_id",
            "x_axis_type": "categorical",
            "aggregation": "avg",
            "breakdown_by": None,
        },
    },
    "Facilities at High Occupancy Risk": {
        "description": "Facilities with predicted occupancy at or above the risk threshold.",
        "chart": {
            "type": "horizontal_bar_chart",
            "x_axis": "FACILITY_NAME",
            "y_axis": "PREDICTED_OCCUPANCY",
            "x_axis_type": "categorical",
            "aggregation": "avg",
            "sort_order": "desc",
            "breakdown_by": None,
            "notes": None,
        },
        "l1": {"unit": "facilities", "format": "number"},
    },
    "Available Beds Snapshot": {
        "description": "Available beds by facility and department.",
    },
    "Total Referrals Trend": {
        "description": "Total referral volume over time.",
        "chart": {
            "type": "line_chart",
            "x_axis": "referral_date",
            "y_axis": "referral_id",
            "x_axis_type": "temporal",
            "aggregation": "count",
            "sort_order": "none",
            "breakdown_by": None,
            "notes": None,
        },
    },
    "Referral Status Mix": {
        "description": "Referral volume by status over time — each line is one status.",
        "l2_projection": {
            "method": "daily_rate",
            "value_field": "referral_count",
            "aggregation": "sum",
            "date_field": "referral_date",
        },
        "chart": {
            "type": "line_chart",
            "x_axis": "referral_date",
            "y_axis": "referral_count",
            "x_axis_type": "temporal",
            "aggregation": "sum",
            "sort_order": "none",
            "breakdown_by": "referral_status",
            "notes": None,
        },
    },
    "ED Holds & Pending Transfers": {
        "description": "ED holds and pending transfers by facility over time.",
        "chart": {
            "type": "line_chart",
            "x_axis": "utilization_datetime",
            "y_axis": "ed_holds",
            "x_axis_type": "temporal",
            "aggregation": "max",
            "sort_order": "none",
            "breakdown_by": "facility_id",
            "notes": None,
        },
    },
    "Escalated Referrals": {
        "description": "Escalated vs non-escalated referral volume over time.",
        "chart": {
            "type": "line_chart",
            "x_axis": "referral_date",
            "y_axis": "referral_id",
            "x_axis_type": "temporal",
            "aggregation": "count",
            "breakdown_by": "escalation_flag",
            "notes": None,
        },
    },
}


def apply_demo_patches(config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Apply demo-only patches; return (config, change log)."""
    cfg = deepcopy(config)
    facility = load_facility_labels()
    changes: list[str] = []
    by_name = _kpi_by_name(cfg)

    for pv in cfg.get("personas", []):
        for sec in pv.get("dashboard_sections", []):
            for kpi in sec.get("kpis", []):
                before_desc = kpi.get("description")
                kpi["description"] = strip_chart_prefix(before_desc)
                if kpi.get("description") != before_desc:
                    changes.append(f"{kpi['name']}: stripped chart prefix from description")

                _patch_chart(kpi, facility)

    for name, patch in _KPI_PATCHES.items():
        kpi = by_name.get(name)
        if not kpi:
            continue
        for key, val in patch.items():
            if key == "chart":
                ch = kpi.setdefault("chart", {})
                ch.update(val)
                changes.append(f"{name}: chart patch ({val.get('type', 'fields')})")
            elif key == "l1":
                kpi.setdefault("l1", {}).update(val)
                changes.append(f"{name}: L1 patch")
            elif key == "l2_projection":
                kpi.setdefault("l2_projection", {}).update(val)
                changes.append(f"{name}: l2_projection patch")
            else:
                kpi[key] = val
                changes.append(f"{name}: {key} updated")

    # Tag snapshot metadata
    cfg.setdefault("demo", {})
    cfg["demo"]["snapshot"] = "hardcoded_20260610"
    cfg["demo"]["facility_labels"] = facility

    return cfg, changes


def finalize_demo_l3(config: dict[str, Any]) -> list[str]:
    """
    Demo-only: breakdown KPIs use per-series L3 only (no aggregate spike).
    Expand l3_forecast_by_series keys for Facility_N / hospital name lookup.
    """
    facility = load_facility_labels()
    changes: list[str] = []
    for pv in config.get("personas", []):
        for sec in pv.get("dashboard_sections", []):
            for kpi in sec.get("kpis", []):
                ch = kpi.get("chart") or {}
                breakdown = ch.get("breakdown_by")
                by_series = kpi.get("l3_forecast_by_series")
                if not breakdown or not by_series:
                    continue
                if kpi.get("l3_forecast"):
                    kpi["l3_forecast"] = None
                    changes.append(f"{kpi['name']}: drop aggregate L3 (use per-series)")
                expanded = dict(by_series)
                for key, fc in by_series.items():
                    if key in facility:
                        expanded[facility[key]] = fc
                    if str(key).isdigit():
                        expanded[f"Facility_{key}"] = fc
                if expanded != by_series:
                    kpi["l3_forecast_by_series"] = expanded
                    changes.append(f"{kpi['name']}: L3 keys for facility/department names")
    return changes


def force_facility_labels(cfg: Any) -> list[str]:
    """
    Re-apply demo facility names after post_process_config (which may overwrite
    breakdown_labels with FORECAST_OCCUPANCY Facility_N placeholders).
    """
    facility = load_facility_labels()
    changes: list[str] = []
    for pv in cfg.personas:
        for sec in pv.dashboard_sections:
            for kpi in sec.kpis:
                ch = kpi.chart
                if not ch:
                    continue
                hints = [ch.x_axis, ch.y_axis, ch.breakdown_by, ch.color_by]
                if not any(h and "facility" in str(h).lower() for h in hints):
                    continue
                existing = dict(ch.breakdown_labels) if ch.breakdown_labels else None
                merged = _merge_labels(existing, facility)
                if merged != (existing or {}):
                    ch.breakdown_labels = merged
                    changes.append(f"{kpi.name}: forced demo facility labels")
    return changes
