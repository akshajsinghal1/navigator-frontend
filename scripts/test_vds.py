"""
Test VizQL Data Service against Superstore.

Steps:
  1. Sign in via REST API -> get auth token + site LUID
  2. List published data sources for the site (find Superstore's 3 data sources)
  3. For each, request metadata (queryable fields)
  4. Run a sample VDS query to fetch real data
"""

from __future__ import annotations
import os
import json
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

SERVER  = os.environ["TABLEAU_SERVER_URL"].rstrip("/")
SITE    = os.environ["TABLEAU_SITE_NAME"]
PAT     = os.environ["TABLEAU_PAT_NAME"]
SECRET  = os.environ["TABLEAU_PAT_SECRET"]
API_VER = "3.22"   # Tableau Cloud current

# -- 1. Sign in ----------------------------------------------------------------
print(f"Signing in to {SERVER}, site={SITE!r}...")
signin = requests.post(
    f"{SERVER}/api/{API_VER}/auth/signin",
    json={"credentials": {
        "personalAccessTokenName":   PAT,
        "personalAccessTokenSecret": SECRET,
        "site": {"contentUrl": SITE},
    }},
    headers={"Accept": "application/json"},
)
if signin.status_code != 200:
    print("Sign-in failed:", signin.status_code, signin.text)
    sys.exit(1)

body  = signin.json()["credentials"]
TOKEN = body["token"]
SITE_LUID = body["site"]["id"]
print(f"  OK. token={TOKEN[:14]}...  site_luid={SITE_LUID}")

H = {"X-Tableau-Auth": TOKEN, "Accept": "application/json", "Content-Type": "application/json"}

# -- 2. List published data sources, filter for Superstore --------------------─
print("\nListing all data sources on the site...")
ds_resp = requests.get(
    f"{SERVER}/api/{API_VER}/sites/{SITE_LUID}/datasources?pageSize=200",
    headers=H,
)
ds_resp.raise_for_status()
all_ds = ds_resp.json().get("datasources", {}).get("datasource", [])
print(f"  Total data sources on site: {len(all_ds)}")
print("\n  All 10 data sources:")
for d in all_ds:
    proj = (d.get("project") or {}).get("name", "?")
    print(f"    - {d['name']!r:<40} project={proj}")

# Filter to ones that have "Superstore" in name OR project
ss_ds = [
    d for d in all_ds
    if "superstore" in d.get("name", "").lower()
    or "superstore" in (d.get("project", {}) or {}).get("name", "").lower()
]
print(f"  Superstore-related data sources: {len(ss_ds)}")
for d in ss_ds:
    print(f"    - {d['name']!r}  luid={d['id']}  project={d.get('project', {}).get('name', '?')}")

if not ss_ds:
    # Fallback: show first 5 to understand naming
    print("\n  (No Superstore match — showing first 5 names for context:)")
    for d in all_ds[:5]:
        print(f"    - {d['name']!r}  project={d.get('project', {}).get('name', '?')}")
    sys.exit(0)

# -- 3. For each Superstore data source, request its metadata via VDS ----------
VDS_BASE = f"{SERVER}/api/v1/vizql-data-service"

for d in ss_ds:
    print(f"\n-- Data source: {d['name']!r} (luid={d['id']}) --")
    meta_resp = requests.post(
        f"{VDS_BASE}/read-metadata",
        headers=H,
        json={"datasource": {"datasourceLuid": d["id"]}},
    )
    if meta_resp.status_code != 200:
        print(f"  read-metadata failed: {meta_resp.status_code}")
        print(f"  {meta_resp.text[:500]}")
        continue
    meta = meta_resp.json()
    fields = meta.get("data", [])
    print(f"  Fields available ({len(fields)}):")
    for f in fields[:25]:
        print(f"    {f.get('fieldName', '?'):<40} {f.get('fieldCaption', '?'):<30} {f.get('dataType', '?')}")
    if len(fields) > 25:
        print(f"    ... and {len(fields) - 25} more")

    # -- 4. Sample query — pick the first dimension + first measure ------------─
    dim = next((f for f in fields if f.get("logicalTableId") and f.get("dataType") in ("STRING", "DATE")), None)
    meas = next((f for f in fields if f.get("dataType") in ("REAL", "INTEGER")), None)
    if dim and meas:
        print(f"\n  Sample query: SELECT {dim['fieldCaption']}, SUM({meas['fieldCaption']})")
        q = {
            "datasource": {"datasourceLuid": d["id"]},
            "query": {
                "fields": [
                    {"fieldCaption": dim["fieldCaption"]},
                    {"fieldCaption": meas["fieldCaption"], "function": "SUM"},
                ],
            },
            "options": {"returnFormat": "OBJECTS"},
        }
        qr = requests.post(f"{VDS_BASE}/query-datasource", headers=H, json=q)
        if qr.status_code == 200:
            rows = qr.json().get("data", [])
            print(f"  -> got {len(rows)} rows. First 5:")
            for r in rows[:5]:
                print(f"      {r}")
        else:
            print(f"  query failed: {qr.status_code}  {qr.text[:300]}")

# -- 5. Sign out --------------------------------------------------------------─
requests.post(f"{SERVER}/api/{API_VER}/auth/signout", headers=H)
print("\nDone.")
