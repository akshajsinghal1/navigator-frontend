"""
Smoke test: VdsClient against every workbook on the site.
For each workbook:
  1. List its published data sources
  2. For each data source, read metadata (field count, sample fields)
  3. Run a tiny sample query to confirm we can actually fetch data
"""
from __future__ import annotations
import sys

# Make stdout safe for Windows console
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

import os
import sys as _sys
from pathlib import Path

# Add project root to path so we can import `tableau.vds`
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from dotenv import load_dotenv

load_dotenv()

from tableau.vds import VdsClient  # noqa: E402

WORKBOOKS = [
    "Superstore",
    "WorldIndicators",
    "Referral_Intelligence_Dashboard_Extract_V1",
    "Navigator_Healthcare_Operations_Dashboard",
    "AdminInsightsStarter",
    "Staffing_Capacity_Dashboard_v2_Extract",
]


def find_workbook_luid(vds: VdsClient, content_url: str) -> str | None:
    """Resolve a workbook's LUID by its content URL."""
    url = f"{vds.server_url}/api/{vds.api_version}/sites/{vds.site_luid}/workbooks?pageSize=200"
    r = requests.get(url, headers=vds._headers(), timeout=30)
    r.raise_for_status()
    wbs = r.json().get("workbooks", {}).get("workbook", [])
    match = [w for w in wbs if w.get("contentUrl") == content_url]
    return match[0]["id"] if match else None


def smoke_test_workbook(vds: VdsClient, content_url: str) -> dict:
    """Returns a small status dict per workbook."""
    print(f"\n{'='*72}\nWORKBOOK: {content_url}\n{'='*72}")
    result = {"workbook": content_url, "ok": False, "datasources": [], "error": None}

    wb_luid = find_workbook_luid(vds, content_url)
    if not wb_luid:
        result["error"] = "Workbook not found"
        print(f"  ERROR: workbook content_url={content_url!r} not found")
        return result
    print(f"  luid = {wb_luid}")

    # 1. List data sources
    try:
        sources = vds.list_workbook_datasources(wb_luid)
    except Exception as exc:
        result["error"] = f"list_workbook_datasources failed: {exc}"
        print(f"  ERROR: {result['error']}")
        return result

    if not sources:
        result["error"] = "No published data sources (may be all embedded)"
        print(f"  WARNING: no published data sources found")
        result["ok"] = True
        return result

    print(f"  Found {len(sources)} published data source(s):")
    for ds in sources:
        print(f"    - {ds['name']!r:<45} luid={ds['luid']}  type={ds['type']}")

    # 2. For each, read metadata and run a sample query
    for ds in sources:
        ds_status = {"luid": ds["luid"], "name": ds["name"], "field_count": 0, "sample_query_ok": False, "sample_row": None}
        try:
            fields = vds.read_datasource_metadata(ds["luid"])
            ds_status["field_count"] = len(fields)
            print(f"\n  Data source {ds['name']!r}: {len(fields)} fields")

            # Show first few fields
            for f in fields[:6]:
                cap   = f.get("fieldCaption", "?")
                dtype = f.get("dataType", "?")
                print(f"      {cap:<35} {dtype}")
            if len(fields) > 6:
                print(f"      ... and {len(fields) - 6} more")

            # Pick a dimension + measure and run a quick query
            dim  = next((f for f in fields if f.get("dataType") == "STRING"), None)
            meas = next((f for f in fields if f.get("dataType") in ("REAL", "INTEGER")
                          and "Calculation_" not in (f.get("fieldName") or "")), None)
            if dim and meas:
                q_fields = [
                    {"fieldCaption": dim["fieldCaption"]},
                    {"fieldCaption": meas["fieldCaption"], "function": "SUM"},
                ]
                rows = vds.query_datasource(ds["luid"], q_fields)
                ds_status["sample_query_ok"] = True
                ds_status["sample_row"] = rows[0] if rows else None
                print(f"    Sample query: SELECT {dim['fieldCaption']}, SUM({meas['fieldCaption']}) → {len(rows)} rows")
                if rows:
                    print(f"      first row: {rows[0]}")
            else:
                print(f"    (no dim/measure pair to query)")

        except Exception as exc:
            ds_status["error"] = str(exc)
            print(f"    ERROR on {ds['name']!r}: {exc}")

        result["datasources"].append(ds_status)

    result["ok"] = True
    return result


# ── main ──────────────────────────────────────────────────────────────────────
with VdsClient.from_env() as vds:
    print(f"Signed in. site_luid={vds.site_luid}\n")
    all_results = [smoke_test_workbook(vds, wb) for wb in WORKBOOKS]

print(f"\n\n{'='*72}\nSUMMARY\n{'='*72}")
for r in all_results:
    if not r["ok"]:
        print(f"  ✗ {r['workbook']:<55} ERROR: {r['error']}")
        continue
    if not r["datasources"]:
        print(f"  ⚠ {r['workbook']:<55} no published data sources ({r['error'] or '—'})")
        continue
    okcount = sum(1 for ds in r["datasources"] if ds.get("sample_query_ok"))
    total   = len(r["datasources"])
    print(f"  ✓ {r['workbook']:<55} {okcount}/{total} data sources queried successfully")
