"""
pipeline/manifest.py
────────────────────
Build a unified "field manifest" for a workbook.

The manifest is the single source of truth for the agent pipeline and the
frontend. It captures, for every field in a workbook:

  - The metadata name (as seen in inventory XML / Tableau Desktop)
  - The "real" name that comes back when actual data is fetched
    (CSV column name from view export OR exact field caption from VDS)
  - The reachability path: which API can fetch this field
    ("vds" + datasource_luid, OR "view" + view_name)
  - The data type and role

How it's built
──────────────
  1. For each published data source the workbook uses → call VDS read-metadata.
  2. For each view in the workbook → fetch a tiny CSV sample to capture the
     view's actual column headers.
  3. Merge the workbook XML inventory + VDS metadata + view CSV columns into
     one structure, deduping by real name.

Workbook-agnostic
─────────────────
Nothing in this module assumes a specific workbook, schema, or industry.
The output structure is identical for every workbook; only the values vary.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

log = logging.getLogger(__name__)


# ── manifest data classes ─────────────────────────────────────────────────────


@dataclass
class FieldEntry:
    """One field that the agent / frontend can reference."""
    # Names
    real_name:     str                        # what appears in the actual fetched data
    metadata_name: Optional[str] = None       # name in workbook XML inventory, if known
    caption:       Optional[str] = None       # display caption (from VDS) — for UI labels

    # Type info
    data_type: str = "unknown"                # "STRING" | "REAL" | "INTEGER" | "DATE" | "DATETIME" | "BOOLEAN"
    role:      str = "unknown"                # "MEASURE" | "DIMENSION" | "CALCULATED" | "unknown"

    # Reachability
    reachable_via:    str = "unknown"         # "vds" | "view" | "unreachable"
    datasource_luid:  Optional[str] = None    # set when reachable_via == "vds"
    datasource_name:  Optional[str] = None
    view_name:        Optional[str] = None    # set when reachable_via == "view"

    # Extra
    is_calculated: bool = False
    formula:       Optional[str] = None
    notes:         Optional[str] = None


@dataclass
class ViewEntry:
    """A Tableau view that exposes some fields."""
    name:    str
    luid:    Optional[str] = None
    columns: list[str] = field(default_factory=list)
    error:   Optional[str] = None  # if probing failed


@dataclass
class DataSourceEntry:
    """One data source the workbook depends on."""
    name:         str
    luid:         Optional[str] = None
    is_published: bool = False
    fields:       list[FieldEntry] = field(default_factory=list)
    error:        Optional[str] = None


@dataclass
class WorkbookManifest:
    """Top-level manifest for a single workbook."""
    workbook_name: str
    workbook_luid: str
    data_sources: list[DataSourceEntry] = field(default_factory=list)
    views:        list[ViewEntry]       = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    # ── lookup helpers ────────────────────────────────────────────────────────

    def all_fields(self) -> list[FieldEntry]:
        """Flatten fields across data sources (deduped by real_name + source)."""
        out: list[FieldEntry] = []
        for ds in self.data_sources:
            out.extend(ds.fields)
        # If views captured columns not in any data source, surface them too
        ds_field_names = {f.real_name for f in out}
        for v in self.views:
            for col in v.columns:
                if col not in ds_field_names:
                    out.append(FieldEntry(
                        real_name     = col,
                        reachable_via = "view",
                        view_name     = v.name,
                        notes         = "discovered via view probe only",
                    ))
                    ds_field_names.add(col)
        return out

    def find_field(self, real_name: str) -> Optional[FieldEntry]:
        for f in self.all_fields():
            if f.real_name == real_name:
                return f
        return None


# ── builder ───────────────────────────────────────────────────────────────────


def _probe_view_columns(client, view_luid: str, view_name: str) -> ViewEntry:
    """Fetch a tiny CSV from a view — record its column headers."""
    entry = ViewEntry(name=view_name, luid=view_luid)
    try:
        rows = client.fetch_view_csv(view_luid, max_rows=5)
        if rows:
            entry.columns = list(rows[0].keys())
        else:
            entry.columns = []
            entry.error   = "view returned 0 rows"
    except Exception as exc:
        entry.error = f"{type(exc).__name__}: {exc}"
    return entry


def _list_workbook_view_names(client, workbook_luid: str) -> list[dict[str, Any]]:
    try:
        return client.list_workbook_views(workbook_luid)
    except Exception as exc:
        log.warning("list_views failed: %s", exc)
        return []


def build_manifest(
    workbook_name: str,
    workbook_luid: str,
    inventory:     dict[str, Any],
    vds_client,
    max_view_workers: int = 8,
) -> WorkbookManifest:
    """
    Build a complete manifest for one workbook.

    Args:
        workbook_name    : display name of the workbook
        workbook_luid    : Tableau LUID of the workbook
        inventory        : output of tableau_inventory_extractor (already extracted)
        vds_client       : authenticated VdsClient — handles BOTH VDS calls AND
                           REST workbook/view/CSV operations (single auth session)
        max_view_workers : how many view probes to run in parallel

    Returns:
        WorkbookManifest with all data sources + views populated.
    """
    connector = vds_client  # alias — same client serves both purposes
    log.info("Building manifest for workbook %s (luid=%s)", workbook_name, workbook_luid)
    manifest = WorkbookManifest(workbook_name=workbook_name, workbook_luid=workbook_luid)

    # ── 1. Inventory-derived data sources (always available) ──────────────────
    inv_sources: dict[str, DataSourceEntry] = {}
    for ds in inventory.get("embedded_datasources", []):
        name = ds.get("name") or "Unknown"
        if name in inv_sources:
            continue
        ds_entry = DataSourceEntry(name=name, is_published=False)
        # Pre-fill fields from inventory metadata (real_name = metadata_name as a starting point)
        for f in ds.get("fields", []):
            ds_entry.fields.append(FieldEntry(
                real_name      = f.get("name", ""),  # will be corrected/augmented downstream
                metadata_name  = f.get("name"),
                caption        = f.get("caption") or f.get("name"),
                data_type      = (f.get("dataType") or "unknown").upper(),
                role           = (f.get("role") or "unknown").upper(),
                is_calculated  = (f.get("type") == "CalculatedField"),
                formula        = f.get("formula"),
                reachable_via  = "view",                  # default for embedded; may be upgraded if VDS catches it
                datasource_name= name,
            ))
        inv_sources[name] = ds_entry
        manifest.data_sources.append(ds_entry)

    # ── 2. Try VDS for published data sources ─────────────────────────────────
    if vds_client is not None:
        try:
            published = vds_client.list_workbook_datasources(workbook_luid)
        except Exception as exc:
            log.warning("VDS list_workbook_datasources failed: %s", exc)
            published = []

        # Only the ones VDS can actually read back (read-metadata 200) become "published"
        for ds in published:
            ds_luid = ds["luid"]
            ds_name = ds.get("name") or "Unknown"
            try:
                meta_fields = vds_client.read_datasource_metadata(ds_luid)
                log.info("VDS metadata OK for %s (%d fields)", ds_name, len(meta_fields))
            except Exception as exc:
                # Connection is reported but the data source is embedded — VDS rejects it.
                log.debug("VDS read-metadata not available for %s: %s", ds_name, exc)
                continue

            # We have a queryable published data source → upgrade or add an entry
            ds_entry = next((d for d in manifest.data_sources if d.name == ds_name), None)
            if ds_entry is None:
                ds_entry = DataSourceEntry(name=ds_name, is_published=True, luid=ds_luid)
                manifest.data_sources.append(ds_entry)
            else:
                ds_entry.is_published = True
                ds_entry.luid         = ds_luid

            # Replace fields with VDS metadata — these names are EXACT
            ds_entry.fields = [
                FieldEntry(
                    real_name      = f.get("fieldCaption", ""),
                    metadata_name  = f.get("fieldName"),
                    caption        = f.get("fieldCaption"),
                    data_type      = (f.get("dataType") or "unknown").upper(),
                    role           = ("MEASURE" if f.get("defaultAggregation") not in (None, "NONE")
                                       else "DIMENSION"),
                    is_calculated  = bool(f.get("formula")) or "Calculation_" in (f.get("fieldName") or ""),
                    reachable_via  = "vds",
                    datasource_luid= ds_luid,
                    datasource_name= ds_name,
                )
                for f in meta_fields
            ]

    # ── 3. Probe views in parallel — captures real CSV column names ───────────
    views = _list_workbook_view_names(vds_client, workbook_luid)
    log.info("Workbook has %d views — probing column headers in parallel", len(views))

    with ThreadPoolExecutor(max_workers=max_view_workers) as ex:
        future_to_view = {
            ex.submit(_probe_view_columns, vds_client, v["luid"], v["name"]): v
            for v in views
        }
        for fut in as_completed(future_to_view):
            v = future_to_view[fut]
            entry = fut.result()
            manifest.views.append(entry)
            if entry.error:
                log.debug("view probe %r error: %s", v["name"], entry.error)
            else:
                log.debug("view probe %r → %d columns: %s",
                          v["name"], len(entry.columns), entry.columns[:5])

    # ── 4. Promote inventory fields that map cleanly to a view column ─────────
    # If a field's metadata_name (or a normalized variant) matches a CSV column
    # in some view, set its real_name to the CSV column AND record view_name.
    all_view_cols: dict[str, list[str]] = {v.name: v.columns for v in manifest.views if v.columns}

    def _matching_column(metadata_name: str) -> Optional[tuple[str, str]]:
        """Return (view_name, csv_column) if any view has a column that maps to this metadata name."""
        if not metadata_name:
            return None
        norm_meta = _norm(metadata_name)
        for vname, cols in all_view_cols.items():
            for c in cols:
                if _norm(c) == norm_meta:
                    return vname, c
        # Fallback: contained substring match
        for vname, cols in all_view_cols.items():
            for c in cols:
                if norm_meta in _norm(c) or _norm(c) in norm_meta:
                    return vname, c
        return None

    for ds_entry in manifest.data_sources:
        # Skip published ones — VDS already gave us exact names
        if ds_entry.is_published:
            continue
        for fe in ds_entry.fields:
            if fe.reachable_via == "vds":
                continue
            match = _matching_column(fe.metadata_name or "")
            if match:
                vname, col = match
                fe.real_name = col
                fe.view_name = vname
                fe.reachable_via = "view"
            else:
                # Not visible on any view → mark unreachable
                fe.real_name      = fe.metadata_name or fe.real_name
                fe.reachable_via  = "unreachable"

    log.info(
        "Manifest built: %d data sources, %d views, %d fields total (%d reachable)",
        len(manifest.data_sources),
        len(manifest.views),
        sum(len(d.fields) for d in manifest.data_sources),
        sum(1 for f in manifest.all_fields() if f.reachable_via != "unreachable"),
    )
    return manifest


# ── name normalisation helper ─────────────────────────────────────────────────


def _norm(s: str) -> str:
    """Normalise a field/column name for fuzzy matching."""
    import re
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")
