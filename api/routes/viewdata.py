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

# ── Multi-workbook VDS session pool ──────────────────────────────────────────
# Sessions are keyed by (server_url, site_name, pat_name) so different Tableau
# instances / tenants get separate sessions without interfering with each other.
# Single-tenant deployments (one PAT in env) effectively use one shared session.

_session_lock = threading.Lock()
_session_pool: dict[str, Any] = {}   # instance_key → VdsClient


def _instance_key() -> str:
    """Derive a session pool key from the current Tableau environment credentials."""
    import os
    return ":".join([
        os.environ.get("TABLEAU_SERVER_URL", ""),
        os.environ.get("TABLEAU_SITE_NAME", ""),
        os.environ.get("TABLEAU_PAT_NAME", ""),
    ])


def _get_conn() -> "VdsClient":
    """
    Return a signed-in VDS client for the current Tableau instance.
    One session per (server_url, site_name, pat_name) combination.
    Lock prevents concurrent sign-ins which would invalidate each other's tokens.
    """
    from tableau.vds import VdsClient

    key = _instance_key()
    with _session_lock:
        client = _session_pool.get(key)
        if client is None or client._token is None:
            try:
                client = VdsClient.from_env()
            except KeyError as exc:
                raise HTTPException(
                    status_code=503,
                    detail=f"Tableau credentials not configured: {exc}",
                )
            client.sign_in()
            _session_pool[key] = client
            log.info("VDS: signed in (session key=%s…)", key[:30])
    return _session_pool[key]


def _invalidate_conn() -> None:
    """Clear the session for the current Tableau instance."""
    key = _instance_key()
    with _session_lock:
        _session_pool.pop(key, None)
    log.info("VDS: session invalidated for key=%s…", key[:30])


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


# ── Hyper extract reader ─────────────────────────────────────────────────────
# Cached per workbook so we only download + read the .hyper once per process.
_hyper_cache: dict[str, tuple[dict, float]] = {}   # workbook → ({table→rows}, ts)
_HYPER_CACHE_TTL = 3600  # 1 hour
_hyper_load_lock = threading.Lock()  # dedupe concurrent Tableau downloads


def _use_local_hyper() -> bool:
    """Local .twbx is opt-in only — default is live Tableau Server extract."""
    import os
    return os.environ.get("NAVIGATOR_USE_LOCAL_HYPER", "").strip().lower() in (
        "1", "true", "yes",
    )


def _twbx_path_from_env() -> "Path | None":
    import os
    from pathlib import Path

    raw = os.environ.get("NAVIGATOR_LOCAL_TWBX", "output/wb_download.twbx").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path if path.is_file() else None


def _local_twbx_path() -> "Path | None":
    """Pinned local extract — only when NAVIGATOR_USE_LOCAL_HYPER=1."""
    if not _use_local_hyper():
        return None
    return _twbx_path_from_env()


def _cached_twbx_fallback_path() -> "Path | None":
    """Cached .twbx from a prior Tableau download — used when live fetch fails."""
    return _twbx_path_from_env()


def _read_hyper_tables_from_twbx(twbx_path: "Path") -> dict[str, list]:
    """Return {table_name_lower: rows} from a local .twbx Hyper extract."""
    import tempfile
    import zipfile
    from pathlib import Path

    from pipeline.hyper_extractor import _read_hyper

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        with zipfile.ZipFile(twbx_path) as zf:
            hyper_files = [n for n in zf.namelist() if n.endswith(".hyper")]
            if not hyper_files:
                raise ValueError(f"No .hyper file in {twbx_path}")
            zf.extract(hyper_files[0], tmpdir)
        hyper_path = tmp / hyper_files[0]
        tables = _read_hyper(str(hyper_path), sample_rows=1, max_full_rows=0)
    return {t.table_name.lower(): t.full_rows for t in tables}


def _hyper_response(
    workbook: str,
    view: str,
    tables_data: dict[str, list],
    table_name: str,
    max_rows: int,
) -> dict:
    from pipeline.dimension_labels import build_dimension_label_maps

    rows = tables_data.get(table_name, [])
    if not rows and table_name not in tables_data:
        log.warning(
            "Table '%s' not found in Hyper extract for workbook '%s'. Available: %s",
            table_name, workbook, sorted(tables_data.keys()),
        )
    sliced = rows if not max_rows else rows[:max_rows]
    return {
        "workbook": workbook,
        "view": view,
        "rows": sliced,
        "row_count": len(sliced),
        "dimension_labels": build_dimension_label_maps(tables_data),
    }


def _download_hyper_from_tableau(workbook: str) -> dict[str, list]:
    """Download .twbx from Tableau Server and return {table_name_lower: rows}."""
    import os
    import tempfile
    import zipfile

    from pipeline.hyper_extractor import _read_hyper
    from tableau.connector import TableauConnector

    conn = TableauConnector(
        server_url = os.environ["TABLEAU_SERVER_URL"],
        site_name  = os.environ.get("TABLEAU_SITE_NAME", ""),
        pat_name   = os.environ["TABLEAU_PAT_NAME"],
        pat_secret = os.environ["TABLEAU_PAT_SECRET"],
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        with conn:
            all_wbs, _ = conn._server.workbooks.get()
            wb_key = workbook.lower()
            wb = next((w for w in all_wbs
                       if w.content_url.lower() == wb_key
                       or w.name.lower() == wb_key), None)
            if not wb:
                raise ValueError(f"Workbook '{workbook}' not found")
            dl_path = conn._server.workbooks.download(
                wb.id, filepath=os.path.join(tmpdir, "wb"), include_extract=True
            )
            with zipfile.ZipFile(dl_path) as z:
                hyper_files = [f for f in z.namelist() if f.endswith(".hyper")]
                if not hyper_files:
                    raise ValueError("No Hyper extract in workbook")
                z.extract(hyper_files[0], tmpdir)
            hyper_path = os.path.join(tmpdir, hyper_files[0])
            tables = _read_hyper(hyper_path, sample_rows=1, max_full_rows=0)

    return {t.table_name.lower(): t.full_rows for t in tables}


def _load_hyper_tables(workbook: str) -> dict[str, list]:
    """
    Return cached Hyper tables for a workbook.
    One Tableau download per workbook — concurrent chart requests share the lock.
    """
    cache_key = workbook.lower()
    cached = _hyper_cache.get(cache_key)
    if cached:
        tables_data, ts = cached
        if time.time() - ts < _HYPER_CACHE_TTL:
            return tables_data

    with _hyper_load_lock:
        cached = _hyper_cache.get(cache_key)
        if cached:
            tables_data, ts = cached
            if time.time() - ts < _HYPER_CACHE_TTL:
                return tables_data

        log.info("Hyper: downloading workbook '%s' from Tableau", workbook)
        source = "tableau"
        try:
            tables_data = _download_hyper_from_tableau(workbook)
        except Exception as exc:
            fallback = _cached_twbx_fallback_path()
            if not fallback:
                raise exc
            log.warning(
                "Tableau Hyper fetch failed (%s) — using cached twbx %s",
                exc, fallback,
            )
            tables_data = _read_hyper_tables_from_twbx(fallback)
            source = "cached_twbx"

        _hyper_cache[cache_key] = (tables_data, time.time())
        log.info("Hyper: cached %d tables for '%s' (%s)", len(tables_data), workbook, source)
        return tables_data


def _get_hyper_table_data(workbook: str, view: str, max_rows: int) -> dict:
    """
    Serve data for [TABLE] view names from the workbook's Hyper extract.
    Downloads the .twbx once, caches the extracted tables for 1 hour.
    """
    table_name = view.replace("[TABLE]", "").strip().strip('"').lower()
    try:
        tables_data = _load_hyper_tables(workbook)
    except Exception as exc:
        log.error("Hyper data fetch failed: workbook=%s table=%s error=%s", workbook, table_name, exc)
        raise HTTPException(status_code=502, detail=f"Hyper data fetch failed: {exc}") from exc

    return _hyper_response(workbook, view, tables_data, table_name, max_rows)


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("")
def get_view_data(
    workbook: str = Query(..., description="Workbook content URL"),
    view:     str = Query(..., description="View / sheet name"),
    max_rows: int = Query(0,   description="Max rows to return (0 = no limit)"),
):
    """
    Fetch live data for a KPI chart.

    Handles two source types:
      [TABLE] views  → read directly from the workbook's Hyper extract
      Regular views  → fetch live from Tableau (existing behaviour)

    Caches results in-process (15-minute TTL) to avoid redundant I/O.
    """
    # ── [TABLE] source: read from Hyper extract, not Tableau ─────────────────
    if view.startswith("[TABLE]"):
        return _get_hyper_table_data(workbook, view, max_rows)

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
