"""
pipeline/dimension_labels.py
────────────────────────────
Build id → display label maps by scanning all Hyper/view tables in cache.
E.g. facility_id '1' → 'Facility_1' from FORECAST_OCCUPANCY when staffing tables
only store numeric ids.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from pipeline.metric_contract import _norm, column_is_numeric

if TYPE_CHECKING:
    from schemas.config import KPI

log = logging.getLogger(__name__)

_ID_SUFFIXES = ("_id", " id")

_FACILITY_PLACEHOLDER = re.compile(r"^Facility_\d+$", re.IGNORECASE)


def _label_quality(label: str) -> int:
    """Higher score = better human-readable label (facility_dim beats Facility_N)."""
    s = (label or "").strip()
    if not s or s == "(null)":
        return 0
    if _FACILITY_PLACEHOLDER.match(s):
        return 1
    if re.fullmatch(r"\d+\.?\d*", s):
        return 0
    return 10


def _merge_label(bucket: dict[str, str], ik: str, nk: str) -> None:
    """Keep the best display label for each id key."""
    if ik not in bucket:
        bucket[ik] = nk
        return
    if _label_quality(nk) > _label_quality(bucket[ik]):
        bucket[ik] = nk


def _is_id_column(col: str) -> bool:
    cn = _norm(col)
    return cn.endswith(_ID_SUFFIXES) or cn in ("facility_id", "department_id")


def _is_name_column(col: str) -> bool:
    cn = _norm(col)
    return "name" in cn and not cn.endswith("_id")


def _scan_table_pairs(rows: list[dict]) -> list[tuple[str, str, dict[str, str]]]:
    """Return (id_col, name_col, {id_val: name_val}) for each id/name pair in a table."""
    if not rows or not isinstance(rows[0], dict):
        return []
    out: list[tuple[str, str, dict[str, str]]] = []
    cols = list(rows[0].keys())
    id_cols = [c for c in cols if _is_id_column(c)]
    name_cols = [c for c in cols if _is_name_column(c) and not column_is_numeric(rows, c)]

    for id_col in id_cols:
        id_base = _norm(id_col).replace(" id", "").replace("_id", "").strip()
        if not id_base:
            continue
        for name_col in name_cols:
            name_base = _norm(name_col).replace(" name", "").replace("_name", "").strip()
            if id_base not in name_base and name_base not in id_base:
                continue
            mapping: dict[str, str] = {}
            for r in rows[:5000]:
                id_v = r.get(id_col)
                name_v = r.get(name_col)
                if id_v is None or name_v is None:
                    continue
                ik = str(id_v).strip()
                nk = str(name_v).strip()
                if ik and nk and ik != "(null)" and nk != "(null)":
                    mapping[ik] = nk
            if len(mapping) >= 2:
                out.append((id_col, name_col, mapping))
    return out


def build_dimension_label_maps(
    view_cache: dict[str, list[dict]],
) -> dict[str, dict[str, str]]:
    """
    Merge id→label maps from all cache tables.
    Keys are normalised id column hints ('facility_id', 'department_id', …).
    """
    merged: dict[str, dict[str, str]] = {}

    def _table_priority(table_name: str) -> int:
        tn = table_name.lower()
        if "facility_dim" in tn or "demo_facility" in tn:
            return 0
        if "forecast_occupancy" in tn:
            return 2
        return 1

    ordered = sorted(view_cache.items(), key=lambda kv: _table_priority(kv[0]))
    for _table, rows in ordered:
        if not rows:
            continue
        for id_col, _name_col, mapping in _scan_table_pairs(rows):
            key = _norm(id_col).replace(" ", "_")
            bucket = merged.setdefault(key, {})
            for ik, nk in mapping.items():
                _merge_label(bucket, ik, nk)

    if merged:
        log.info(
            "Dimension labels: %d entity type(s), e.g. facility has %d labels",
            len(merged),
            len(merged.get("facility_id", {})),
        )
    return merged


def _labels_for_column(col: str | None, maps: dict[str, dict[str, str]]) -> dict[str, str]:
    if not col:
        return {}
    cn = _norm(col).replace(" ", "_")
    if cn in maps:
        return maps[cn]
    if _is_id_column(col):
        base = cn.replace("_id", "")
        return maps.get(f"{base}_id", maps.get(base, {}))
    return {}


def apply_dimension_labels(
    kpi: KPI,
    view_cache: dict[str, list[dict]],
) -> list[str]:
    """Attach chart.breakdown_labels for facility_id / department_id axes."""
    if not view_cache:
        return []

    maps = build_dimension_label_maps(view_cache)
    if not maps:
        return []

    ch = kpi.chart
    changes: list[str] = []
    combined: dict[str, str] = dict(ch.breakdown_labels or {})

    for attr in ("x_axis", "y_axis", "breakdown_by"):
        hint = getattr(ch, attr, None)
        if not hint or not _is_id_column(hint):
            continue
        labels = _labels_for_column(hint, maps)
        if not labels:
            continue
        before = len(combined)
        for ik, nk in labels.items():
            if ik not in combined or _label_quality(nk) > _label_quality(combined.get(ik, "")):
                combined[ik] = nk
        if len(combined) > before:
            changes.append(f"{kpi.name}: labels for {attr} ({len(combined) - before} new keys)")

    if combined and combined != (ch.breakdown_labels or {}):
        ch.breakdown_labels = combined

    return changes


def apply_dimension_labels_config(
    config,
    view_cache: dict[str, list[dict]] | None = None,
) -> list[str]:
    cache = view_cache or {}
    messages: list[str] = []
    for pv in config.personas:
        for sec in pv.dashboard_sections:
            for kpi in sec.kpis:
                messages.extend(apply_dimension_labels(kpi, cache))
    return messages
