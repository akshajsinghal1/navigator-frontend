"""
Investigate: relationship between workbook 'connections' LUIDs and site 'datasources' LUIDs.

Hypothesis: connections return EMBEDDED data source LUIDs (private to workbook),
            while VDS needs SEPARATELY PUBLISHED data source LUIDs.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from dotenv import load_dotenv
load_dotenv()
from tableau.vds import VdsClient


with VdsClient.from_env() as vds:
    # 1. All site data sources
    site_ds = vds.list_site_datasources()
    site_luids = {d["luid"]: d for d in site_ds}
    print(f"Site has {len(site_ds)} published data sources:")
    for d in site_ds:
        print(f"  - {d['name']!r:<45}  luid={d['luid']}  project={d.get('project','?')}")

    # 2. For Superstore, list its connections and cross-reference
    print("\n--- Superstore workbook connections ---")
    wb_luid = "dc42c0f3-e579-42a9-8bbc-f0791ad5ed7d"   # Superstore
    url = f"{vds.server_url}/api/{vds.api_version}/sites/{vds.site_luid}/workbooks/{wb_luid}/connections"
    r = requests.get(url, headers=vds._headers(), timeout=30)
    conns = r.json().get("connections", {}).get("connection", [])
    print(f"Connections returned: {len(conns)}")
    for c in conns:
        ds = c.get("datasource") or {}
        ds_luid = ds.get("id")
        in_site = "PUBLISHED" if ds_luid in site_luids else "EMBEDDED (not in site list)"
        print(f"  - conn id={c.get('id'):<40}")
        print(f"    datasource.id={ds_luid}  datasource.name={ds.get('name')!r}  status={in_site}")
        print(f"    type={c.get('type')}  serverAddress={c.get('serverAddress')}")

    # 3. Try VDS read-metadata on the SITE-LEVEL Superstore Datasource (the one that worked earlier)
    print("\n--- Try VDS on the site-level 'Superstore Datasource' ---")
    target = next((d for d in site_ds if d["name"].lower() == "superstore datasource"), None)
    if target:
        try:
            fields = vds.read_datasource_metadata(target["luid"])
            print(f"  SUCCESS: {len(fields)} fields. First 3: "
                  f"{[f['fieldCaption'] for f in fields[:3]]}")
        except Exception as e:
            print(f"  FAIL: {e}")

    # 4. Try a workbook's embedded connection LUID for comparison
    print("\n--- Try VDS on a Superstore workbook's connection LUID ---")
    if conns:
        emb_luid = conns[0].get("datasource", {}).get("id")
        try:
            fields = vds.read_datasource_metadata(emb_luid)
            print(f"  UNEXPECTED SUCCESS: {len(fields)} fields")
        except Exception as e:
            print(f"  FAIL (expected): {e}")
