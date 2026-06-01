"""
api/routes/viewdata.py
───────────────────────
GET /viewdata  — live Tableau view data for the frontend.

The frontend calls this at chart-render time to get fresh rows.
It does NOT use raw_data embedded in the config — the config only
carries the L1 aggregate and chart hints. The actual time-series
data always comes from this live endpoint.

Query params:
  workbook : Tableau workbook content URL  (e.g. "Superstore")
  view     : Tableau view / sheet name     (e.g. "Sales by Month")
  max_rows : optional row limit (default 0 = no limit)

Shared session
──────────────
All concurrent requests reuse a single signed-in VDS client stored at
module level. This prevents the 401/429 cascade that occurred when every
chart request fired an independent Tableau sign-in simultaneously.

A threading.Lock ensures only one sign-in happens at a time. On any 401
the shared session is invalidated and the request retries once.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query

if TYPE_CHECKING:
    from tableau.vds import VdsClient

log = logging.getLogger(__name__)
router = APIRouter()

# ── Shared VDS session ────────────────────────────────────────────────────────

_session_lock = threading.Lock()
_session: dict[str, Any] = {"client": None}   # shared signed-in VdsClient

def _get_conn() -> "VdsClient":
    """
    Return the shared signed-in VDS client, signing in once if needed.

    The lock ensures parallel requests don't each trigger their own sign-in
    (which would cause Tableau to 401 the older tokens immediately).
    """
    from tableau.vds import VdsClient

    with _session_lock:
        client = _session["client"]
        if client is None or client._token is None:
            try:
                client = VdsClient.from_env()
            except KeyError as exc:
                raise HTTPException(
                    status_code=503,
                    detail=f"Tableau credentials not configured: {exc}",
                )
            client.sign_in()
            _session["client"] = client
            log.info("VDS: signed in (shared session)")
    return _session["client"]


def _invalidate_conn() -> None:
    """Clear the shared session so the next request triggers a fresh sign-in."""
    with _session_lock:
        _session["client"] = None
    log.info("VDS: shared session invalidated")


# ── Row / LUID caches ─────────────────────────────────────────────────────────

# workbook content_url → luid (avoids re-querying all workbooks each call)
_luid_cache: dict[str, str] = {}

# (workbook_lower, view_lower) → (rows, timestamp)  — TTL: 15 minutes
_VIEW_CACHE_TTL = 900
_view_cache: dict[tuple, tuple] = {}


def clear_view_cache(workbook_content_url: str) -> None:
    """Remove all cached view entries for a given workbook."""
    key_prefix = workbook_content_url.lower()
    to_delete = [k for k in _view_cache if k[0] == key_prefix]
    for k in to_delete:
        del _view_cache[k]
    if to_delete:
        log.info("View cache cleared for workbook '%s' (%d entries)", workbook_content_url, len(to_delete))


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("")
def get_view_data(
    workbook: str = Query(..., description="Workbook content URL"),
    view:     str = Query(..., description="View / sheet name"),
    max_rows: int = Query(0,   description="Max rows to return (0 = no limit)"),
):
    """
    Fetch live Tableau view data for a KPI chart.

    Returns JSON rows that the frontend transforms into ECharts options
    using the x_axis / y_axis / aggregation hints stored in the config.

    Caches workbook LUID and view rows in-process to avoid redundant
    Tableau connections on every chart render.
    """
    # ── Check view cache first ────────────────────────────────────────────────
    cache_key = (workbook.lower(), view.lower())
    if cache_key in _view_cache:
        cached_rows, cached_ts = _view_cache[cache_key]
        if time.time() - cached_ts < _VIEW_CACHE_TTL:
            log.debug("View cache hit: %s / %s (%d rows)", workbook, view, len(cached_rows))
            sliced = cached_rows if not max_rows else cached_rows[:max_rows]
            return {
                "workbook":  workbook,
                "view":      view,
                "rows":      sliced,
                "row_count": len(sliced),
            }
        else:
            log.debug("View cache expired: %s / %s", workbook, view)
            del _view_cache[cache_key]

    # ── Fetch from Tableau — retry once on 401 ────────────────────────────────
    rows: list = []
    last_exc: Exception | None = None

    for attempt in range(2):
        try:
            conn = _get_conn()
            workbook_key = workbook.lower()

            if workbook_key not in _luid_cache:
                wb_meta = conn.get_workbook_by_content_url(workbook)
                if not wb_meta:
                    raise ValueError(f"Workbook not found: {workbook!r}")
                _luid_cache[workbook_key] = wb_meta["luid"]
                log.debug("LUID cache miss — fetched: %s → %s", workbook, wb_meta["luid"])
            else:
                log.debug("LUID cache hit: %s → %s", workbook, _luid_cache[workbook_key])

            rows = conn.get_view_data_by_name(
                workbook_luid = _luid_cache[workbook_key],
                view_name     = view,
                max_rows      = max_rows,
            )
            last_exc = None
            break  # success

        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            last_exc = exc
            if "401" in str(exc) and attempt == 0:
                log.warning(
                    "VDS 401 on %s/%s — invalidating shared session and retrying",
                    workbook, view,
                )
                _invalidate_conn()
                # Also drop cached LUID — token invalidation may have changed site context
                _luid_cache.pop(workbook.lower(), None)
                continue
            break

    if last_exc is not None:
        log.error(
            "Failed to fetch view data: workbook=%s view=%s error=%s",
            workbook, view, last_exc,
        )
        raise HTTPException(
            status_code=502,
            detail=f"Tableau data fetch failed: {last_exc}",
        )

    # Normalise: strip Grand Total rows, unpivot Measure Names/Values
    # Same cleanup the pipeline applies before passing rows to agents.
    from tableau.view_data import normalize_rows
    rows = normalize_rows(rows)

    # Cache for subsequent requests
    _view_cache[cache_key] = (rows, time.time())

    return {
        "workbook":  workbook,
        "view":      view,
        "rows":      rows,
        "row_count": len(rows),
    }
