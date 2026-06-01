"""
Quick proof: query a binned/calculated field that's unlikely to be on any view.
"""
import os, requests
from dotenv import load_dotenv
load_dotenv()

S = os.environ["TABLEAU_SERVER_URL"].rstrip("/")
auth = requests.post(f"{S}/api/3.22/auth/signin", json={"credentials": {
    "personalAccessTokenName":   os.environ["TABLEAU_PAT_NAME"],
    "personalAccessTokenSecret": os.environ["TABLEAU_PAT_SECRET"],
    "site": {"contentUrl": os.environ["TABLEAU_SITE_NAME"]},
}}, headers={"Accept": "application/json"}).json()["credentials"]

H = {"X-Tableau-Auth": auth["token"], "Accept": "application/json", "Content-Type": "application/json"}
VDS = f"{S}/api/v1/vizql-data-service"
DS_LUID = "9ce6d861-5af5-4096-9571-1aa1b7e1ad9a"

# Three queries that combine fields the standard Superstore dashboards likely don't expose:
tests = [
    {"name": "Manufacturer ranking (group field, rarely on views)",
     "q": {"datasource": {"datasourceLuid": DS_LUID},
           "query": {"fields": [
               {"fieldCaption": "Manufacturer"},
               {"fieldCaption": "Profit", "function": "SUM"},
           ]}}},
    {"name": "Profit Ratio by Sub-Category (calculated field)",
     "q": {"datasource": {"datasourceLuid": DS_LUID},
           "query": {"fields": [
               {"fieldCaption": "Sub-Category"},
               {"fieldCaption": "Profit Ratio", "function": "AVG"},
           ]}}},
    {"name": "Returned orders by Regional Manager",
     "q": {"datasource": {"datasourceLuid": DS_LUID},
           "query": {"fields": [
               {"fieldCaption": "Regional Manager"},
               {"fieldCaption": "Returned"},
               {"fieldCaption": "Quantity", "function": "SUM"},
           ]}}},
]

for t in tests:
    print(f"\n-- {t['name']} --")
    t["q"]["options"] = {"returnFormat": "OBJECTS"}
    r = requests.post(f"{VDS}/query-datasource", headers=H, json=t["q"])
    if r.status_code == 200:
        data = r.json().get("data", [])
        print(f"  -> {len(data)} rows. First 5:")
        for row in data[:5]:
            print(f"     {row}")
    else:
        print(f"  FAILED {r.status_code}: {r.text[:300]}")

requests.post(f"{S}/api/3.22/auth/signout", headers=H)
