import json
from pathlib import Path

data = json.loads(Path("output/demo/kpi_display_audit.json").read_text())
for k in data["kpis"]:
    name = k["name"]
    unit = k["config_l1"]
    now = next(p for p in k["periods"] if p["period"] == "now")
    for p in k["periods"]:
        if p["period"] == "now":
            continue
        h, hn = p["headline"], now["headline"]
        if h is None or hn is None:
            continue
        flags = []
        if "%" in unit and h > 150:
            flags.append("PCT_HEADLINE_SPIKE")
        if abs(hn) > 0.01:
            r = abs(h) / abs(hn)
            if r > 3 or r < 0.33:
                flags.append(f"HEADLINE_JUMP_{r:.1f}x")
        if p["layer"] == "L2":
            flags.append("L2_LAYER")
        if p["layer"] == "L1" and k["has_l3"]:
            flags.append("HAS_L3_BUT_L1_LAYER")
        if not p["chart_ok"]:
            flags.append("CHART_FAIL")
        if flags:
            print(
                f"{name} [{p['period']}] layer={p['layer']} "
                f"now={hn:.4g} -> {h:.4g} | {', '.join(flags)}"
            )
