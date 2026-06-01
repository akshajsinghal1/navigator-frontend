"""
Run pipeline on all 6 workbooks back-to-back. Audit each config.
Output: per-workbook summary of what's good and what's broken.
"""
from __future__ import annotations
import sys, json, time, subprocess, glob
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

WORKBOOKS = [
    "Superstore",
    "WorldIndicators",
    "Referral_Intelligence_Dashboard_Extract_V1",
    "Navigator_Healthcare_Operations_Dashboard",
    "AdminInsightsStarter",
    "Staffing_Capacity_Dashboard_v2_Extract",
]

def latest_config(workbook: str) -> str | None:
    cands = sorted(glob.glob(f"output/intelligence_config_{workbook}_*.json"))
    return cands[-1] if cands else None

def audit_config(path: str) -> dict:
    cfg = json.load(open(path))
    seen, total, with_l1, with_chart_data, mismatches = set(), 0, 0, 0, []
    chart_types = {}
    for p in cfg["personas"]:
        for s in p["dashboard_sections"]:
            for k in s["kpis"]:
                if k["id"] in seen: continue
                seen.add(k["id"]); total += 1
                l1 = k.get("l1") or {}; ch = k.get("chart") or {}
                rd = k.get("raw_data") or []
                if l1.get("value") is not None: with_l1 += 1
                if rd: with_chart_data += 1
                fn, y = (l1.get("field_name") or ""), (ch.get("y_axis") or "")
                if fn and y and fn != y:
                    mismatches.append({"id": k["id"], "field_name": fn, "y_axis": y})
                t = ch.get("type") or "?"
                chart_types[t] = chart_types.get(t, 0) + 1
    return {
        "personas":   len(cfg["personas"]),
        "objective":  cfg.get("objective", "")[:80],
        "total_kpis": total,
        "with_l1":    with_l1,
        "with_chart_data": with_chart_data,
        "mismatches": mismatches,
        "chart_types": chart_types,
    }

start_all = time.time()
results = []
for wb in WORKBOOKS:
    print(f"\n{'='*70}\nRunning: {wb}\n{'='*70}")
    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, "run_pipeline.py", "--workbook", wb],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600,
    )
    dt = time.time() - t0
    if proc.returncode != 0:
        print(f"  FAILED in {dt:.0f}s. Last 500 chars of stderr:")
        print(proc.stderr[-500:])
        results.append({"workbook": wb, "ok": False, "elapsed": dt,
                        "error": proc.stderr[-300:]})
        continue
    cfg_path = latest_config(wb)
    if not cfg_path:
        results.append({"workbook": wb, "ok": False, "elapsed": dt,
                        "error": "no config saved"})
        continue
    audit = audit_config(cfg_path)
    results.append({"workbook": wb, "ok": True, "elapsed": dt,
                    "cfg": Path(cfg_path).name, **audit})
    print(f"  done in {dt:.0f}s. KPIs={audit['total_kpis']} "
          f"l1={audit['with_l1']} chart_data={audit['with_chart_data']} "
          f"mismatches={len(audit['mismatches'])}")

print(f"\n\n{'='*70}\nFINAL AUDIT (total {time.time()-start_all:.0f}s)\n{'='*70}\n")
for r in results:
    if not r["ok"]:
        print(f"✗ {r['workbook']:<55} FAILED ({r.get('error','')[:80]})")
        continue
    mm = r["mismatches"]
    ok = "✓" if (r["total_kpis"] == r["with_l1"] and not mm) else "⚠"
    print(f"{ok} {r['workbook']:<55} {r['elapsed']:>4.0f}s   "
          f"{r['total_kpis']:>2} KPIs | L1: {r['with_l1']}/{r['total_kpis']} | "
          f"chart_data: {r['with_chart_data']}/{r['total_kpis']} | "
          f"mismatches: {len(mm)}")
    print(f"     personas={r['personas']}  charts={r['chart_types']}")
    if mm:
        print(f"     ⚠ field-name mismatches:")
        for m in mm[:3]:
            print(f"       {m['id']}: l1.field={m['field_name']!r} vs y_axis={m['y_axis']!r}")
