"""
pipeline/l1_refresher.py
─────────────────────────
Data-only refresh — no AI, no agents.

When the inventory hash hasn't changed (same schema) but Tableau's
workbook.updated_at has advanced, only the numbers need updating.
This module re-fetches every KPI's L1 value and raw_data using the
pointers already baked into the existing IntelligenceConfig.

Cost:  ~30 seconds, zero Gemini API calls.
vs
Full pipeline: ~8 minutes, Gemini API cost.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from tableau.view_data import parse_value, normalize_rows

log = logging.getLogger(__name__)


def refresh_l1(
    config_dict: dict[str, Any],
    connector,
    workbook_luid: str,
) -> dict[str, Any]:
    """
    Re-fetch all L1 values and raw_data in the config without running AI.

    Args:
        config_dict  : existing IntelligenceConfig as a plain dict
        connector    : authenticated Tableau connector (inside a context manager)
        workbook_luid: LUID of the workbook

    Returns:
        Updated config dict with fresh l1.value and raw_data on every KPI.
        Everything else (personas, sections, chart specs, explanations) unchanged.
    """
    import copy
    config = copy.deepcopy(config_dict)

    # Cache fetched view data so we don't hit the same view twice
    view_cache: dict[str, list[dict]] = {}

    kpi_count     = 0
    refresh_count = 0
    skip_count    = 0

    for persona in config.get("personas", []):
        for section in persona.get("dashboard_sections", []):
            for kpi in section.get("kpis", []):
                kpi_count += 1
                l1 = kpi.get("l1")

                if not l1 or not l1.get("view_name"):
                    log.debug("KPI '%s' has no l1.view_name — skipping", kpi.get("name"))
                    skip_count += 1
                    continue

                view_name  = l1["view_name"]
                field_name = l1.get("field_name", "")

                # Fetch view data (cached per view)
                if view_name not in view_cache:
                    try:
                        raw = connector.get_view_data_by_name(workbook_luid=workbook_luid, view_name=view_name, max_rows=200)
                        view_cache[view_name] = raw
                        log.info("Fetched view '%s' — %d rows", view_name, len(raw))
                    except Exception as exc:
                        log.warning("Could not fetch view '%s': %s", view_name, exc)
                        view_cache[view_name] = []

                raw_rows = normalize_rows(view_cache[view_name])

                if not raw_rows:
                    skip_count += 1
                    continue

                # Update raw_data (normalized — Grand Totals stripped, pivot unpivoted)
                kpi["raw_data"] = raw_rows

                # Use the chart's aggregation method (set by the chart agent at
                # pipeline time) so L1 refresh matches what the AI originally computed.
                # Fall back to unit-based heuristic if chart agg is missing.
                chart_agg = (kpi.get("chart") or {}).get("aggregation") or ""
                l1_unit   = l1.get("unit", "")
                if chart_agg in ("avg", "count", "min", "max", "sum"):
                    agg_method = chart_agg
                else:
                    agg_method = "avg" if l1_unit == "%" else "sum"

                # Re-aggregate L1 value from the fresh rows
                new_value = _aggregate_l1(raw_rows, field_name, agg_method)
                if new_value is not None:
                    kpi["l1"]["value"] = new_value
                    log.info(
                        "KPI '%s' refreshed (%s): %s -> %s",
                        kpi.get("name"), agg_method, l1.get("value"), new_value,
                    )
                    refresh_count += 1
                else:
                    skip_count += 1

    # Stamp the refresh time in IST (UTC+5:30)
    ist = timezone(timedelta(hours=5, minutes=30))
    config["refreshed_at"] = datetime.now(ist).isoformat()

    log.info(
        "L1 refresh complete — %d KPIs total, %d refreshed, %d skipped",
        kpi_count, refresh_count, skip_count,
    )
    return config


def _aggregate_l1(
    rows: list[dict],
    field_name: str,
    method: str = "sum",
) -> float | None:
    """
    Extract a single aggregate value for field_name from raw rows.

    Args:
        rows      : list of row dicts from Tableau
        field_name: hint for matching the right column
        method    : "sum" | "avg" | "count" | "min" | "max"

    Handles Tableau-formatted strings: "$6,928", "19.5%", "(1,234)", etc.
    """
    if not rows or not field_name:
        return None

    cols = list(rows[0].keys())

    # Find best matching column
    target = _find_column(cols, field_name)
    if not target:
        return None

    values = []
    for row in rows:
        v = parse_value(row.get(target))
        if v is not None:
            values.append(v)

    if not values:
        return None

    if method == "count":
        return float(len(values))
    if method == "min":
        return round(min(values), 4)
    if method == "max":
        return round(max(values), 4)
    if method == "avg":
        return round(sum(values) / len(values), 4)
    # default: sum
    return round(sum(values), 4)



def _find_column(cols: list[str], hint: str) -> str | None:
    """Fuzzy match a column name against a hint string."""
    import re

    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()

    h = norm(hint)

    # Exact normalized match
    exact = next((c for c in cols if norm(c) == h), None)
    if exact:
        return exact

    # Substring match
    sub = next((c for c in cols if norm(c) in h or h in norm(c)), None)
    if sub:
        return sub

    # Word match (any significant word from hint appears in column)
    words = [w for w in h.split() if len(w) > 2]
    word = next((c for c in cols if any(w in norm(c) for w in words)), None)
    return word
