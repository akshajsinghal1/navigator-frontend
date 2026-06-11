"""
pipeline/view_rows.py
─────────────────────
Resolve full Hyper/view rows for a KPI (shared by classifier, blueprint, L3, audit).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pipeline.metric_contract import _norm

if TYPE_CHECKING:
    from schemas.config import KPI

RowSource = Literal["cache", "sample", "none"]


def rows_for_kpi(
    kpi: KPI,
    view_cache: dict[str, list[dict]],
) -> tuple[list[dict], RowSource]:
    """
    Return rows for a KPI and where they came from.

    Priority: view_data_cache (full data) → raw_data sample (20 rows) → none.
    """
    view = (kpi.l1.view_name if kpi.l1 else "") or ""

    if view and view in view_cache and view_cache[view]:
        return view_cache[view], "cache"

    if view:
        view_n = _norm(view)
        for key, rows in view_cache.items():
            if rows and (_norm(key) == view_n or view_n in _norm(key) or _norm(key) in view_n):
                return rows, "cache"

    raw = kpi.raw_data
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return raw, "sample"

    return [], "none"


def resolve_cache_view_name(view: str, view_cache: dict[str, list[dict]]) -> str | None:
    """Return the canonical view_cache key for a KPI view_name, if rows exist."""
    if not view or not view_cache:
        return None
    if view in view_cache and view_cache[view]:
        return view
    view_n = _norm(view)
    for key, rows in view_cache.items():
        if not rows:
            continue
        key_n = _norm(key)
        if key_n == view_n or view_n in key_n or key_n in view_n:
            return key
    return None


def align_kpi_view_names(
    config: "IntelligenceConfig",
    view_cache: dict[str, list[dict]],
) -> list[str]:
    """
    Rewrite kpi.l1.view_name to match view_data_cache keys when fuzzy-matched.
    Prevents L1/L3 from falling back to the 20-row sample.
    """
    changes: list[str] = []
    if not view_cache:
        return changes

    for pv in config.personas:
        for sec in pv.dashboard_sections:
            for kpi in sec.kpis:
                if not kpi.l1 or not kpi.l1.view_name:
                    continue
                canonical = resolve_cache_view_name(kpi.l1.view_name, view_cache)
                if canonical and canonical != kpi.l1.view_name:
                    old = kpi.l1.view_name
                    kpi.l1.view_name = canonical
                    changes.append(f"{kpi.name}: view_name '{old}' → '{canonical}'")

    if changes:
        log = __import__("logging").getLogger(__name__)
        log.info("View align: %d view_name(s) canonicalized", len(changes))
    return changes
