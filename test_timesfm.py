"""
test_timesfm.py
───────────────
Standalone TimesFM test — NO changes to Navigator codebase.

Tests L3 time series forecasting on real workbook data:
  1. Occupancy % trend (demo_bed_utilization_hourly)
  2. Staffing gap trend (demo_staffing_requirements)

Run: python test_timesfm.py
"""

import sys, numpy as np
sys.stdout.reconfigure(encoding='utf-8')

# ── Step 1: Load real time series from Hyper extract ─────────────────────────
print("Loading Hyper data...")
from pipeline.hyper_extractor import _read_hyper

tables = {
    t.table_name: t
    for t in _read_hyper(
        'output/wb_extract/Data/Extracts/federated_1q4nymg0zdt0tl1fvgw811.hyper',
        sample_rows=1,
        max_full_rows=0,
    )
}

# ── Occupancy % — use HOURLY data (100,800 rows) — need 490+ context points ──
print("\nPreparing occupancy time series (hourly)...")
occ_rows = tables['"demo_bed_utilization_hourly'].full_rows

# Sort by datetime, take all hourly values (avg across facilities per hour)
from collections import defaultdict
hourly_occ = defaultdict(list)
for r in occ_rows:
    dt  = str(r.get('utilization_datetime', ''))[:16]  # YYYY-MM-DDTHH:MM
    val = r.get('occupancy_percent')
    if dt and val is not None:
        hourly_occ[dt].append(float(val))

occ_series = [(dt, sum(vs)/len(vs)) for dt, vs in sorted(hourly_occ.items())]
occ_values = [v for _, v in occ_series]
print(f"  Occupancy series: {len(occ_values)} hourly points")
print(f"  Range: {min(occ_values):.1f}% - {max(occ_values):.1f}%  avg={sum(occ_values)/len(occ_values):.1f}%")

# ── Staffing gap — use SHIFT-LEVEL data (21,000 rows) for sufficient context ──
print("\nPreparing staffing gap time series (shift-level)...")
stf_rows = tables['"demo_staffing_requirements'].full_rows

# Each row is one shift on one day — sort by date+shift, take raw staffing_gap values
shift_gaps = []
for r in sorted(stf_rows, key=lambda x: (str(x.get('requirement_date','')), str(x.get('shift_name','')))):
    val = r.get('staffing_gap')
    if val is not None:
        shift_gaps.append(float(val))

gap_values = shift_gaps
print(f"  Staffing gap series: {len(gap_values)} shift-level points")
print(f"  Range: {min(gap_values):.2f} - {max(gap_values):.2f}  avg={sum(gap_values)/len(gap_values):.2f}")

# ── Step 2: Load TimesFM model ────────────────────────────────────────────────
print("\nLoading TimesFM model (first run downloads ~800MB)...")
try:
    import timesfm
    import torch
    torch.set_float32_matmul_precision("high")

    tfm = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
        "google/timesfm-2.5-200m-pytorch",
    )
    tfm.compile(timesfm.ForecastConfig(
        max_context=512,
        max_horizon=128,
        normalize_inputs=False,
    ))
    print("Model loaded and compiled OK")

    # ── Step 3: Forecast occupancy ────────────────────────────────────────────
    HORIZON = 30  # forecast 30 days ahead
    CONTEXT  = min(512, len(occ_values))  # last N days as context

    context_occ = occ_values[-CONTEXT:]

    print(f"\nForecasting occupancy: {CONTEXT} days context → {HORIZON} days ahead...")
    point_forecasts, quantile_forecasts = tfm.forecast(
        HORIZON,
        [np.array(context_occ, dtype=np.float32)],
    )

    print(f"  Raw output shapes: point={point_forecasts.shape}  quantile={quantile_forecasts.shape}")
    print(f"  Raw point values (first 5): {point_forecasts[0, :5]}")
    print(f"  Input stats: min={min(context_occ):.2f} max={max(context_occ):.2f} has_nan={any(np.isnan(context_occ))}")
    occ_forecast    = point_forecasts[0].tolist()
    occ_lower       = quantile_forecasts[0, :, 0].tolist()   # first quantile
    occ_upper       = quantile_forecasts[0, :, -1].tolist()  # last quantile

    print(f"  Last actual  : {occ_values[-1]:.2f}%")
    print(f"  Day +1  pred : {occ_forecast[0]:.2f}%  (p10={occ_lower[0]:.1f}, p90={occ_upper[0]:.1f})")
    print(f"  Day +7  pred : {occ_forecast[6]:.2f}%  (p10={occ_lower[6]:.1f}, p90={occ_upper[6]:.1f})")
    print(f"  Day +30 pred : {occ_forecast[29]:.2f}% (p10={occ_lower[29]:.1f}, p90={occ_upper[29]:.1f})")

    # ── Step 4: Forecast staffing gap ─────────────────────────────────────────
    context_gap = gap_values[-CONTEXT:]

    print(f"\nForecasting staffing gap: {CONTEXT} days context → {HORIZON} days ahead...")
    point_gap, quantile_gap = tfm.forecast(
        HORIZON,
        [np.array(context_gap, dtype=np.float32)],
    )

    gap_forecast = point_gap[0].tolist()
    gap_lower    = quantile_gap[0, :, 0].tolist()
    gap_upper    = quantile_gap[0, :, -1].tolist()

    print(f"  Last actual  : {gap_values[-1]:.3f}")
    print(f"  Day +1  pred : {gap_forecast[0]:.3f}  (p10={gap_lower[0]:.2f}, p90={gap_upper[0]:.2f})")
    print(f"  Day +7  pred : {gap_forecast[6]:.3f}  (p10={gap_lower[6]:.2f}, p90={gap_upper[6]:.2f})")
    print(f"  Day +30 pred : {gap_forecast[29]:.3f} (p10={gap_lower[29]:.2f}, p90={gap_upper[29]:.2f})")

    print("\nTimesFM test PASSED")
    print("\nSample output for Navigator config:")
    print({
        "model": "timesfm-2.5-200m",
        "kpi": "Average Occupancy Rate",
        "horizon_days": HORIZON,
        "predictions": [round(v, 2) for v in occ_forecast[:7]],
        "lower_p10":   [round(v, 2) for v in occ_lower[:7]],
        "upper_p90":   [round(v, 2) for v in occ_upper[:7]],
    })

except ImportError as e:
    print(f"TimesFM import error: {e}")
except Exception as e:
    print(f"Error: {e}")
    import traceback; traceback.print_exc()
