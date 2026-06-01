"""
Prove: we can access ANY field in the data source via VDS,
whether or not it's used in any published view.

Steps:
  1. Sign in
  2. List Superstore workbook views (so we know what fields the dashboards use)
  3. List Superstore Datasource fields (the full underlying field list)
  4. Pick fields that are NOT used in any view
  5. Query those fields via VDS -> proves we can access them
"""

from __future__ import annotations
import os
import requests
from dotenv import load_dotenv

load_dotenv()

SERVER  = os.environ["TABLEAU_SERVER_URL"].rstrip("/")
SITE    = os.environ["TABLEAU_SITE_NAME"]
PAT     = os.environ["TABLEAU_PAT_NAME"]
SECRET  = os.environ["TABLEAU_PAT_SECRET"]
API_VER = "3.22"

# ── Sign in ────────────────────────────────────────────────────────────────────
signin = requests.post(
    f"{SERVER}/api/{API_VER}/auth/signin",
    json={"credentials": {
        "personalAccessTokenName":   PAT,
        "personalAccessTokenSecret": SECRET,
        "site": {"contentUrl": SITE},
    }},
    headers={"Accept": "application/json"},
)
TOKEN = signin.json()["credentials"]["token"]
SITE_LUID = signin.json()["credentials"]["site"]["id"]
H = {"X-Tableau-Auth": TOKEN, "Accept": "application/json", "Content-Type": "application/json"}
print(f"Signed in. site_luid={SITE_LUID}")

# ── Find Superstore workbook ──────────────────────────────────────────────────
wb_resp = requests.get(
    f"{SERVER}/api/{API_VER}/sites/{SITE_LUID}/workbooks?filter=name:eq:Superstore",
    headers=H,
)
workbooks = wb_resp.json().get("workbooks", {}).get("workbook", [])
if not workbooks:
    # fallback: find any workbook with 'superstore' in name
    wb_resp = requests.get(f"{SERVER}/api/{API_VER}/sites/{SITE_LUID}/workbooks?pageSize=200", headers=H)
    all_wbs = wb_resp.json().get("workbooks", {}).get("workbook", [])
    workbooks = [w for w in all_wbs if "superstore" in w["name"].lower()]

ss_wb = workbooks[0]
print(f"\nSuperstore workbook: {ss_wb['name']!r}  luid={ss_wb['id']}")

# ── List its views and find fields used (via Metadata API GraphQL) ────────────
GRAPHQL = f"{SERVER}/api/metadata/graphql"
gq = """
query SuperstoreFields($wbId: String!) {
  workbooks(filter: {luid: $wbId}) {
    name
    sheets {
      name
      sheetFieldInstances { name fields { name } }
    }
  }
}
"""
mr = requests.post(GRAPHQL, headers=H, json={"query": gq, "variables": {"wbId": ss_wb["id"]}})
fields_used_in_views: set[str] = set()
sheets_info = []
mj = mr.json() if mr.status_code == 200 else {}
print(f"  GraphQL status={mr.status_code} response_keys={list(mj.keys())}")
if mj.get("errors"):
    print(f"  GraphQL errors: {mj['errors'][:1]}")
data_block = mj.get("data") or {}
for wb in (data_block.get("workbooks") or []):
    for sheet in wb.get("sheets", []) or []:
        sheet_fields = []
        for inst in sheet.get("sheetFieldInstances", []) or []:
            for f in inst.get("fields", []) or []:
                fname = f.get("name")
                if fname:
                    fields_used_in_views.add(fname)
                    sheet_fields.append(fname)
            inst_name = inst.get("name")
            if inst_name:
                fields_used_in_views.add(inst_name)
        sheets_info.append((sheet["name"], sheet_fields))

print(f"\nSheets in Superstore workbook: {len(sheets_info)}")
for sn, sf in sheets_info[:8]:
    print(f"  - {sn:<30} uses {len(set(sf))} fields")
print(f"\nTotal unique fields used across all sheets: {len(fields_used_in_views)}")

# ── Get ALL fields from the Superstore data source via VDS ────────────────────
VDS = f"{SERVER}/api/v1/vizql-data-service"
DS_LUID = "9ce6d861-5af5-4096-9571-1aa1b7e1ad9a"  # Superstore Datasource

meta = requests.post(f"{VDS}/read-metadata", headers=H, json={"datasource": {"datasourceLuid": DS_LUID}})
all_fields = meta.json().get("data", [])
all_captions = {f["fieldCaption"] for f in all_fields}
print(f"\nALL fields in Superstore Datasource (via VDS): {len(all_captions)}")
for cap in sorted(all_captions):
    used = cap in fields_used_in_views
    mark = "[USED in views]" if used else "[UNUSED]"
    print(f"  {mark:<18} {cap}")

unused = all_captions - fields_used_in_views
print(f"\nFields NOT used in any view: {len(unused)}")
for u in sorted(unused):
    print(f"  - {u}")

# ── Now query an UNUSED field via VDS to prove we can access it ───────────────
if unused:
    test_field = sorted(unused)[0]
    # Find the type
    f_def = next(f for f in all_fields if f["fieldCaption"] == test_field)
    dtype = f_def.get("dataType")
    print(f"\nTesting access to UNUSED field: {test_field!r}  (dataType={dtype})")
    # Build a simple query
    if dtype in ("REAL", "INTEGER"):
        q = {
            "datasource": {"datasourceLuid": DS_LUID},
            "query": {"fields": [{"fieldCaption": test_field, "function": "SUM"}]},
            "options": {"returnFormat": "OBJECTS"},
        }
    else:
        q = {
            "datasource": {"datasourceLuid": DS_LUID},
            "query": {"fields": [{"fieldCaption": test_field}]},
            "options": {"returnFormat": "OBJECTS"},
        }
    qr = requests.post(f"{VDS}/query-datasource", headers=H, json=q)
    if qr.status_code == 200:
        data = qr.json().get("data", [])
        print(f"  SUCCESS -> got {len(data)} rows. Sample:")
        for r in data[:5]:
            print(f"    {r}")
        print(f"\n  PROVEN: VDS can access {test_field!r} even though no view uses it.")
    else:
        print(f"  FAILED: {qr.status_code}  {qr.text[:300]}")

# Sign out
requests.post(f"{SERVER}/api/{API_VER}/auth/signout", headers=H)
