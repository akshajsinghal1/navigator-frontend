"""
tableau/semantic_filter.py
──────────────────────────
Strips all operational noise from the raw Tableau inventory JSON and returns
only the semantic signal needed by the agent pipeline.

Raw inventory  ≈ 50 k tokens
Filtered output ≈ 4–6 k tokens

KEEP (semantic signal)
───────────────────────
  workbook        : name, project_name, updated_at
  parameters      : name, names of calculations that reference it
  views (tabs)    : names of dashboard/sheet tabs
  graphql_views   : all sheet names (reveals granular business intent)
  connections     : datasource_name, connection_type
  embedded_datasources:
    - name
    - fields (visible only, no system fields):
        name, description (if not null/empty), dataType, role
        + formula (if CalculatedField)
    - upstream_tables: name + column names

STRIP (noise)
──────────────
  All UUIDs / IDs
  All timestamps (except workbook updated_at)
  isHidden=True fields
  System / internal fields  (name starts with ":")
  extract* refresh timestamps (always null in cloud)
  server block, extracted_at, notes
  owner_* blocks everywhere
  tags, size, webpage_url, vizportal_url_id, show_tabs
  views[].content_url, created_at, updated_at, owner_id, tags
  connections[].id, datasource_id, server_address, server_port, username, embed_password
  columns[].id, remoteType, isNullable, description (empty)
  tables[].schema, id, fullName  (redundant)
  upstream_databases / upstream_tables at top-level  (duplicated inside embedded_datasources)
  refresh_schedules (null)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


# Regex to detect Tableau system/internal field names
_SYSTEM_FIELD_RE = re.compile(r"^:")

# Purely-display utility field patterns (safe to prune – they add no semantic value)
_UTILITY_NAME_RE = re.compile(
    r"\b(label|tooltip|rank over|sort by field|sort helper)\b",
    re.IGNORECASE,
)


def _is_utility_field(name: str, formula: str | None) -> bool:
    """Return True for fields that are clearly display/sorting utilities."""
    if _UTILITY_NAME_RE.search(name):
        return True
    # Formula that does nothing but reference a single parameter/field with no math
    if formula and re.match(r"^\[[\w\s]+\]$", formula.strip()):
        # e.g. "[Base Salary]" – just an alias, no transformation
        if _UTILITY_NAME_RE.search(name):
            return True
    return False


def filter_inventory(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Accept the raw inventory dict (as loaded from inventory_*.json).
    Return a stripped-down dict with only semantic signal.
    """
    out: dict[str, Any] = {}

    # ── workbook ────────────────────────────────────────────────────────────
    wb = raw.get("workbook", {})
    out["workbook"] = {
        "name": wb.get("name"),
        "project_name": wb.get("project_name"),
        "updated_at": wb.get("updated_at"),
    }

    # ── parameters ──────────────────────────────────────────────────────────
    params = []
    for p in raw.get("parameters", []):
        name = p.get("name", "")
        if _SYSTEM_FIELD_RE.match(name):
            continue
        refs = [r["name"] for r in p.get("referencedByCalculations", []) if r.get("name")]
        params.append({
            "name": name,
            "used_in_calculations": refs,
        })
    out["parameters"] = params

    # ── dashboard tabs (views) ───────────────────────────────────────────────
    out["dashboard_tabs"] = [
        v["name"] for v in raw.get("views", []) if v.get("name")
    ]

    # ── granular sheets (graphql_views) ─────────────────────────────────────
    # Keep all sheet/dashboard names – they reveal business intent at grain level.
    # Filter out tooltip-only sheets (clearly utility, not shown to users).
    sheet_names = []
    for gv in raw.get("graphql_views", []):
        name = gv.get("name", "")
        if not name:
            continue
        if name.lower().startswith("tooltip"):
            continue
        sheet_names.append(name)
    out["sheets"] = sorted(set(sheet_names))

    # ── data connections ─────────────────────────────────────────────────────
    connections = []
    for c in raw.get("connections", []):
        dsname = c.get("datasource_name")
        ctype  = c.get("connection_type")
        if dsname:
            connections.append({
                "datasource": dsname,
                "type": ctype,
            })
    out["data_connections"] = connections

    # ── embedded datasources ─────────────────────────────────────────────────
    datasources = []
    for ds in raw.get("embedded_datasources", []):
        ds_name = ds.get("name", "")

        # ── fields ──
        fields: list[dict] = []
        for f in ds.get("fields", []):
            fname = f.get("name", "")

            # Strip hidden fields
            if f.get("isHidden", False):
                continue
            # Strip system/internal fields
            if _SYSTEM_FIELD_RE.match(fname):
                continue

            typename = f.get("__typename", "ColumnField")
            formula  = f.get("formula") if typename == "CalculatedField" else None

            # Strip pure utility fields
            if _is_utility_field(fname, formula):
                continue

            entry: dict[str, Any] = {
                "name":     fname,
                "type":     typename,
                "dataType": f.get("dataType"),
                "role":     f.get("role"),
            }
            desc = f.get("description")
            if desc:
                entry["description"] = desc
            if formula:
                entry["formula"] = formula

            fields.append(entry)

        # ── upstream tables ──
        tables: list[dict] = []
        for t in ds.get("upstream_tables", []):
            tname = t.get("name", "")
            cols  = [c["name"] for c in t.get("columns", []) if c.get("name")]
            if tname:
                tables.append({"table": tname, "columns": cols})

        datasources.append({
            "name":   ds_name,
            "fields": fields,
            "upstream_tables": tables,
        })

    out["embedded_datasources"] = datasources

    return out


def filter_inventory_json(raw_json: str) -> str:
    """Accept raw JSON string, return filtered JSON string."""
    return json.dumps(filter_inventory(json.loads(raw_json)), indent=2)


def filter_inventory_file(input_path: str | Path) -> dict[str, Any]:
    """Load an inventory JSON file and return the filtered dict."""
    raw = json.loads(Path(input_path).read_text(encoding="utf-8"))
    return filter_inventory(raw)


def token_estimate(d: dict[str, Any]) -> int:
    """Rough token estimate: chars / 4."""
    return len(json.dumps(d)) // 4


# ── CLI helper ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else None
    if path is None:
        candidates = sorted(
            Path("output").glob("inventory_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            sys.exit("No inventory_*.json found in output/")
        path = candidates[0]

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    filtered = filter_inventory(raw)

    raw_tokens  = token_estimate(raw)
    filt_tokens = token_estimate(filtered)

    print(json.dumps(filtered, indent=2))
    print(f"\n── token estimates ──", file=sys.stderr)
    print(f"  raw      : ~{raw_tokens:,} tokens", file=sys.stderr)
    print(f"  filtered : ~{filt_tokens:,} tokens", file=sys.stderr)
    print(f"  reduction: {100*(1-filt_tokens/raw_tokens):.0f}%", file=sys.stderr)
