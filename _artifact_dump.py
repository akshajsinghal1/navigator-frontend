"""
Dumps every intermediate artifact from the Navigator pipeline
so the full artifact flow can be reviewed for quality degradation.
Uses cached inventory + profiler data — no fresh Tableau calls needed.
"""
import json, glob, os, sys, io, textwrap
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SEP = "\n" + "="*72 + "\n"

# ── 1. Inventory ─────────────────────────────────────────────────────────────
inv_f = sorted(glob.glob("output/inventory_Navigator*.json"), key=os.path.getmtime)[-1]
inv   = json.load(open(inv_f))
cfg_f = sorted(glob.glob("output/intelligence_config_Navigator*.json"), key=os.path.getmtime)[-1]
cfg   = json.load(open(cfg_f))

print(SEP + "STAGE 1 — INVENTORY" + SEP)
wb = inv.get("workbook", {})
ds = inv.get("embedded_datasources", [{}])[0]
fields = ds.get("fields", [])
print(f"Workbook : {wb.get('name')}")
print(f"Fields   : {len(fields)}")
print(f"Sheets   : {len(inv.get('sheets', []))}")
print("\nField sample (name | dataType | role | calculated):")
for f in fields[:8]:
    calc = "CALC" if f.get("type") == "CalculatedField" else "     "
    print(f"  {calc}  {f.get('name','?'):40s}  {f.get('dataType','?'):10s}  {f.get('role','?')}")
print(f"  ... {len(fields)-8} more")

# ── 2. Semantic Filter ────────────────────────────────────────────────────────
print(SEP + "STAGE 2 — SEMANTIC FILTER" + SEP)
from tableau.semantic_filter import filter_inventory
filtered = filter_inventory(inv)
fds      = filtered.get("embedded_datasources", [{}])[0]
ff       = fds.get("fields", [])
removed  = len(fields) - len(ff)
print(f"Fields in  : {len(fields)}")
print(f"Fields out : {len(ff)}  (removed {removed} system/noise fields)")
print(f"Sheets out : {len(filtered.get('sheets', []))}")

# ── 3. Structural EDA ─────────────────────────────────────────────────────────
print(SEP + "STAGE 3 — STRUCTURAL EDA (what agent sees before profiler)" + SEP)
from pipeline.eda import run_eda, format_eda_for_agent
eda = run_eda(filtered)
eda_text = format_eda_for_agent(eda)
print(f"EDA output ({len(eda_text)} chars):")
print(eda_text[:1200] + "\n... [truncated]")

# ── 4. Manifest ───────────────────────────────────────────────────────────────
print(SEP + "STAGE 4 — MANIFEST (view→column map)" + SEP)
print("Key output: which views have data and what columns they expose")
# Read from the profile artifact instead of hitting Tableau
profile_file = "_profile_artifact.json"
if os.path.exists(profile_file):
    pa = json.load(open(profile_file))
    vws = pa.get("views", {})
    print(f"Views probed: {len(vws)}")
    for vname, vs in list(vws.items())[:5]:
        if vs.get("rows",0) > 0:
            dims = vs.get("dimensions",[])
            meas = vs.get("measures",[])
            grain = vs.get("grain","?")
            print(f"  [{grain:6s}] {vname[:40]:40s}  dims={dims[:2]}  meas={meas[:2]}")
    print(f"  ...")
else:
    print("  (profile_artifact.json not found — run _run_profiler.py first)")

# ── 5. Profiler ───────────────────────────────────────────────────────────────
print(SEP + "STAGE 5 — PROFILER OUTPUT (what orchestrator receives)" + SEP)
if os.path.exists(profile_file):
    from pipeline.profiler import profile_workbook, format_profile_for_agent
    pa = json.load(open(profile_file))
    # Reconstruct a minimal WorkbookProfile-like object for display
    print(f"Entities   : {len(pa['entities'])}")
    for e in pa["entities"]:
        print(f"  {e['name']}: {len(e['canonical_values'])} canonical values → {e['canonical_values'][:5]}")
        if e.get("aliases"):
            print(f"    aliases: {dict(list(e['aliases'].items())[:3])}")
    print(f"\nRelationships: {len(pa['relationships'])}")
    for r in pa["relationships"]:
        print(f"  [{r['kind']}] {r['expr'][:80]}")
    print(f"\nQuality flags: {len(pa['flags'])}")
    from collections import Counter
    fc = Counter(f["code"] for f in pa["flags"])
    for code, n in fc.most_common():
        print(f"  {code:30s}  x{n}")
    print("\nVERIFIED_DATA_PROFILE text sent to orchestrator (first 2000 chars):")
    # Rebuild the text
    from pipeline.profiler import WorkbookProfile, ColumnProfile, Entity, Relationship, QualityFlag, format_profile_for_agent
    # Just show what the orchestrator actually receives by reading from existing run log
    run_log = sorted(glob.glob("output/logs/*.json"), key=os.path.getmtime)
    if run_log:
        log = json.load(open(run_log[-1]))
        print("  (reconstructed from profiler — see _profile_artifact.json for full data)")
else:
    print("  (profile_artifact.json not found)")

# ── 6. Orchestrator Input ─────────────────────────────────────────────────────
print(SEP + "STAGE 6 — ORCHESTRATOR INPUT (what Gemini actually receives)" + SEP)
print("Tools available to orchestrator:")
from schemas.tools import ORCHESTRATOR_TOOLS
for t in ORCHESTRATOR_TOOLS:
    props = list(t.get("input_schema",{}).get("properties",{}).keys())
    print(f"  {t['name']:30s}  params={props}")

# ── 7. Domain Agent Input/Output ──────────────────────────────────────────────
print(SEP + "STAGE 7 — DOMAIN AGENT INPUT / OUTPUT" + SEP)
print("Tools available to domain agents:")
from schemas.tools import DOMAIN_TOOLS
for t in DOMAIN_TOOLS:
    props = list(t.get("input_schema",{}).get("properties",{}).keys())
    print(f"  {t['name']:30s}  params={props}")

print("\nActual domain agent OUTPUT (from latest config raw_data):")
personas = cfg.get("personas", [])
for p in personas:
    persona = p.get("persona", {})
    for s in p.get("dashboard_sections", []):
        for k in s.get("kpis", []):
            l1 = k.get("l1", {})
            raw = k.get("raw_data", [])
            td = k.get("trend_direction"); tp = k.get("trend_pct")
            print(f"\n  KPI: {k['name']}")
            print(f"    l1_value     : {l1.get('value')}  unit={l1.get('unit')}  fmt={l1.get('format')}")
            print(f"    view         : {l1.get('view_name')}")
            print(f"    field        : {l1.get('field_name')}")
            print(f"    trend        : {td} {tp}%")
            print(f"    raw_data rows: {len(raw)}")
            if raw:
                print(f"    raw_data[0]  : {dict(list(raw[0].items())[:3])}")
            break  # just first KPI per section
        break
    break

# ── 8. Chart Agent Input/Output ───────────────────────────────────────────────
print(SEP + "STAGE 8 — CHART AGENT INPUT / OUTPUT" + SEP)
print("Tools available to chart agents:")
from schemas.tools import CHART_TOOLS
for t in CHART_TOOLS:
    props = list(t.get("input_schema",{}).get("properties",{}).keys())
    print(f"  {t['name']:30s}  params={props}")

print("\nActual chart spec OUTPUT (from latest config):")
for p in cfg.get("personas", []):
    for s in p.get("dashboard_sections", []):
        for k in s.get("kpis", []):
            ch = k.get("chart", {})
            print(f"\n  KPI: {k['name']}")
            print(f"    type         : {ch.get('type')}")
            print(f"    x_axis       : {ch.get('x_axis')}")
            print(f"    y_axis       : {ch.get('y_axis')}")
            print(f"    x_axis_type  : {ch.get('x_axis_type')}")
            print(f"    breakdown_by : {ch.get('breakdown_by')}")
            print(f"    aggregation  : {ch.get('aggregation')}")
            print(f"    sort_order   : {ch.get('sort_order')}")
            exp = k.get("explanation", {})
            print(f"    explanation.what: {str(exp.get('what',''))[:80]}")
            print(f"    explanation.key_insight: {str(exp.get('key_insight',''))[:80]}")
            break
        break
    break

# ── 9. Final Config Schema ─────────────────────────────────────────────────────
print(SEP + "STAGE 9 — FINAL INTELLIGENCE CONFIG SCHEMA" + SEP)
print("Top-level keys:", list(cfg.keys()))
print("\nPersona schema:")
p0 = cfg["personas"][0]
print("  persona keys:", list(p0.keys()))
per = p0.get("persona", {})
print("  persona.keys:", list(per.keys()))
sec = p0.get("dashboard_sections", [{}])[0]
print("  section keys:", list(sec.keys()))
kpi = sec.get("kpis", [{}])[0]
print("  kpi keys:", list(kpi.keys()))
print("\nFull KPI structure (first KPI):")
print(json.dumps(kpi, indent=2, default=str)[:1500])

# ── 10. Information Loss Analysis ─────────────────────────────────────────────
print(SEP + "INFORMATION LOSS ANALYSIS" + SEP)
print("What each stage DROPS:")
print()
print("  Inventory → Semantic Filter:")
print(f"    removed {removed} fields ({removed/len(fields)*100:.0f}%)")
print()
print("  Profiler → Orchestrator:")
print("    full row data DROPPED — only entity model + flags + column names passed")
print("    agent cannot see: actual data values, distributions, outliers")
print()
print("  Domain Agent → Assembler:")
print("    raw_data kept (limited sample) but trend_description often NULL")
print("    anomaly detection output not persisted")
print()
print("  Chart Agent → Assembler:")
print("    no intermediate: chart agent emits directly to chart_specs dict")
print("    NOTHING logged between chart_agent.generate() and emit_chart_spec")
print()
print("What NEVER gets persisted to disk:")
print("  - Filtered inventory (in-memory only)")
print("  - Profiler VERIFIED_DATA_PROFILE text (in-memory only)")
print("  - Domain agent raw emit before assembler validation")
print("  - Chart agent reasoning before emit_chart_spec")
print("  - Orchestrator domain grouping decisions")
print("  - Per-KPI iteration counts / retry flags")
print("  - Any confidence / quality scores")
