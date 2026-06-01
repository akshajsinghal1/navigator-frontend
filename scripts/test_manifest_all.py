"""
Build the field manifest for every workbook on the site.
Verify:
  - All 6 workbooks produce a manifest without crashing
  - For each workbook: how many fields are reachable via vds / view / unreachable
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from dotenv import load_dotenv
load_dotenv()

from tableau.vds       import VdsClient
from pipeline.manifest import build_manifest


WORKBOOKS = [
    "Superstore",
    "WorldIndicators",
    "Referral_Intelligence_Dashboard_Extract_V1",
    "Navigator_Healthcare_Operations_Dashboard",
    "AdminInsightsStarter",
    "Staffing_Capacity_Dashboard_v2_Extract",
]


def find_workbook(vds: VdsClient, content_url: str) -> dict | None:
    try:
        return vds.find_workbook_by_content_url(content_url)
    except Exception as e:
        print(f"  [find_workbook] {e}")
        return None


def load_inventory(wb_name: str) -> dict:
    """Load most recent inventory JSON for a workbook from output/."""
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "output"
    cands = sorted(p.glob(f"inventory_{wb_name}_*.json"), reverse=True)
    if cands:
        try:
            return json.loads(cands[0].read_text(encoding="utf-8"))
        except Exception:
            pass
    # Some files use the content_url as the name
    cands = sorted(p.glob(f"*{wb_name}*.json"), reverse=True)
    for c in cands:
        if "inventory" in c.name.lower():
            try:
                return json.loads(c.read_text(encoding="utf-8"))
            except Exception:
                continue
    return {"embedded_datasources": []}


# ── run ───────────────────────────────────────────────────────────────────────
with VdsClient.from_env() as vds:
    print(f"VDS client ready (site_luid={vds.site_luid})\n")
    summary = []
    for content_url in WORKBOOKS:
        print(f"\n{'='*72}\nWORKBOOK: {content_url}\n{'='*72}")
        wb_meta = find_workbook(vds, content_url)
        if not wb_meta:
            print("  Skip — workbook not found")
            continue

        # Load inventory from disk if cached; otherwise pass empty (manifest still works via views/VDS)
        inventory = load_inventory(content_url)
        if not inventory.get("embedded_datasources"):
            print("  (no cached inventory found — proceeding without it)")

        manifest = build_manifest(
            workbook_name = wb_meta["name"],
            workbook_luid = wb_meta["luid"],
            inventory     = inventory,
            vds_client    = vds,
        )

        # ── report ──
        print(f"\n  Data sources: {len(manifest.data_sources)}")
        for ds in manifest.data_sources:
            via_vds = sum(1 for f in ds.fields if f.reachable_via == "vds")
            via_view = sum(1 for f in ds.fields if f.reachable_via == "view")
            unreach = sum(1 for f in ds.fields if f.reachable_via == "unreachable")
            tag = "[VDS]" if ds.is_published else "[embedded]"
            print(f"    {tag:<12} {ds.name!r:<35}  fields={len(ds.fields)}  "
                  f"vds={via_vds} view={via_view} unreach={unreach}")

        all_fields = manifest.all_fields()
        reachable  = [f for f in all_fields if f.reachable_via != "unreachable"]
        print(f"\n  Total fields={len(all_fields)}, reachable={len(reachable)}, "
              f"unreachable={len(all_fields)-len(reachable)}")
        print(f"  Views probed: {len(manifest.views)}  "
              f"(successful={sum(1 for v in manifest.views if v.columns)}, "
              f"failed={sum(1 for v in manifest.views if v.error)})")

        # Show a few reachable + unreachable
        print("\n  Sample reachable fields:")
        for f in reachable[:5]:
            via = f.reachable_via + (f":{f.view_name}" if f.view_name else "")
            print(f"    - {f.real_name!r:<35} via={via}  type={f.data_type}")
        unreach_fields = [f for f in all_fields if f.reachable_via == "unreachable"]
        if unreach_fields:
            print("\n  Sample unreachable fields:")
            for f in unreach_fields[:5]:
                print(f"    - {f.metadata_name!r:<35}  (not on any view)")

        summary.append({
            "workbook": content_url,
            "data_sources": len(manifest.data_sources),
            "total_fields": len(all_fields),
            "reachable": len(reachable),
        })

print(f"\n\n{'='*72}\nSUMMARY\n{'='*72}")
for s in summary:
    print(f"  {s['workbook']:<55}  {s['reachable']}/{s['total_fields']} fields reachable "
          f"across {s['data_sources']} data sources")
