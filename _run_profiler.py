"""Fetch a workbook's views and run the generic profiler. Transport only."""
import os, sys, json
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
from tableau.vds import VdsClient
from pipeline.profiler import profile_workbook

WB = sys.argv[1] if len(sys.argv) > 1 else "Navigator_Predictive_Analytics_v2_Extract"
creds = {k: os.environ.get(v, "") for k, v in {
    'tableau_server_url': 'TABLEAU_SERVER_URL', 'tableau_site_name': 'TABLEAU_SITE_NAME',
    'tableau_pat_name': 'TABLEAU_PAT_NAME', 'tableau_pat_secret': 'TABLEAU_PAT_SECRET'}.items()}

with VdsClient.from_dict(creds) as conn:
    wb = conn.get_workbook_by_content_url(WB)
    views = conn.list_workbook_views(wb["luid"])
    raw = {}
    for v in views:
        try:
            raw[v["name"]] = conn.fetch_view_csv(v["luid"], max_rows=100000) or []
        except Exception:
            raw[v["name"]] = []

prof = profile_workbook(raw, total_views=len(views))
outname = f"_profile_{WB.replace(' ','_')}.json"
with open(outname, "w", encoding="utf-8") as f:
    json.dump(prof.to_dict(), f, indent=2, default=str)
print("ARTIFACT:", outname)

# ── summary ──────────────────────────────────────────────────────────────
print("="*72)
print(f"PROFILE: {WB}")
print("="*72)
print(f"Views: {prof.total_views} total, {prof.data_views} with data")
print(f"Columns profiled: {len(prof.columns)}  "
      f"({sum(1 for c in prof.columns if c.role=='measure')} measures, "
      f"{sum(1 for c in prof.columns if c.role=='dimension')} dimensions)")

print(f"\nENTITIES discovered ({len(prof.entities)}):")
for e in prof.entities:
    print(f"  • {e.name}: {len(e.canonical_values)} canonical values "
          f"(from {e.raw_value_count} raw), in {len(e.columns)} views")
    print(f"      values: {e.canonical_values}")
    if e.aliases:
        print(f"      normalized aliases: {e.aliases}")

print(f"\nRELATIONSHIPS discovered ({len(prof.relationships)}):")
for r in prof.relationships:
    print(f"  • [{r.kind}] {r.expr}")

print(f"\nQUALITY FLAGS ({len(prof.flags)}):")
for fl in prof.flags:
    tag = "⚠ " if fl.severity == "warn" else "· "
    print(f"  {tag}[{fl.code}] {fl.where}")
    print(f"       {fl.message}")

print("\nFull artifact → _profile_artifact.json")
