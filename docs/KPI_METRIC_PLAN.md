# Navigator KPI / L1–L2–L3 Plan

**Living document** — update this file at the end of each work session so context is not lost between turns.

| Field | Value |
|-------|--------|
| **Last updated** | 2026-06-10 |
| **Active branch** | `feature/kpi-metric-contract` |
| **Safe baseline branch** | `v1.0.4` (unchanged at commit `628ec26`) |
| **Workbook under test** | `Navigator_Predictive_Analytics_v2_Extract` |
| **Config served by API** | `output/intelligence_config_*_l3.json` (lexicographic sort — known drift risk) |

---

## Goal

One **metric contract** so agent config, L3 pipeline, and frontend compute the same numbers:

- **L1** = correct “now” (snapshot vs accumulator vs rate)
- **L2** = formula projection on 7D / 30D (from `l2_projection`)
- **L3** = TimesFM: history in → 30d forecast + p10/p90 out (per series when breakdown)

**Core rule:** TimesFM is dumb — it only sees number arrays. Correctness = how we slice, bucket, and aggregate Hyper rows **before** the call.

---

## Branch & save status

| Item | Status |
|------|--------|
| Branch created | ✅ `feature/kpi-metric-contract` |
| Phase 1 coded | ✅ (see files below) |
| Phase 1 committed | ❌ **Not committed yet** |
| `v1.0.4` preserved | ✅ |

### Uncommitted files (metric-contract work)

**New**

- `pipeline/metric_contract.py` — Python rules + `normalize_config()`
- `frontend/src/lib/metricCompute.ts` — TypeScript mirror
- `pipeline/l3_forecaster.py` — TimesFM step (also on branch as new file)
- `run_l3.py` — standalone L3 patch script

**Modified (metric-contract)**

- `pipeline/runner.py` — calls `normalize_config()` before L3
- `frontend/src/components/NavigatorCanvas.tsx` — L1/L2 via metricCompute
- `frontend/src/components/NavigatorKpiChart.tsx` — chart agg, hourly buckets for snapshot, `l3Eligible` guard

**Modified (pre-existing on branch — not part of metric-contract)**

- `agents/domain_agent.py`, `agents/orchestrator.py`
- `api/routes/dashboard.py`, `api/routes/viewdata.py`
- `frontend/src/NavigatorApp.tsx`, `frontend/src/types/navigator.ts`
- `schemas/config.py`

> **Before merging:** decide whether to commit metric-contract only or include orchestrator/domain changes.

---

## Architecture (who owns what)

```
Agent writes config (l1, l2_projection, chart)
        ↓
normalize_config()  ← fixes aggregation, date_field, strips bad L3 on breakdown
        ↓
run_l3_forecasts()  ← builds series from Hyper, TimesFM, writes l3_forecast
        ↓
Frontend            ← computeL1Value / computeL2ProjectionValue / resolveChartAggregation
```

| Layer | Produced by | Consumed by |
|-------|-------------|-------------|
| L1 value | Domain agent (+ live recompute) | Tiles, modal headline (Now) |
| L2 recipe | Domain agent (`l2_projection`) | Frontend evaluates on 7D/30D |
| L3 forecast | `l3_forecaster.py` (TimesFM) | Frontend reads `l3_forecast` |
| Chart spec | Chart agent | `NavigatorKpiChart` |

**Per-breakdown L3:** `l3_forecast_by_series` on KPI + aggregate `l3_forecast` for tile headline.

---

## Phase checklist

### Phase 1 — Metric contract (DONE — pending commit + L3 re-run)

| Task | Status | Notes |
|------|--------|-------|
| `pipeline/metric_contract.py` | ✅ Done | snapshot/rate/accumulator, L1/L2 helpers, normalizer |
| `frontend/src/lib/metricCompute.ts` | ✅ Done | Mirrors Python rules |
| Wire normalizer in `runner.py` | ✅ Done | After QA, before L3 |
| Wire normalizer in `run_l3.py` | ✅ Done | On config load |
| L3 uses `resolve_chart_aggregation` | ✅ Done | Not raw `chart.aggregation` alone |
| Frontend L1/L2 use metricCompute | ✅ Done | NavigatorCanvas |
| Chart uses `resolveChartAggregation` | ✅ Done | NavigatorKpiChart |
| Hourly buckets for snapshot KPIs | ✅ Done | Beds-scale fix |
| **User verify: Available Beds** | ✅ User confirmed fixed | |
| **User verify: ED Holds** | ✅ User confirmed fixed | |
| **User verify: Pending Transfers** | ✅ User confirmed fixed | |
| **User verify: Occupancy by Facility** | ⚠️ Re-test | Chart now filters 7D/30D + per-series L3/L2 overlay |
| Commit Phase 1 | ❌ Not done | |
| Re-run `run_l3.py` after normalize | ❌ **Required** | Populates `l3_forecast_by_series` |

### Phase 2 — L3 per breakdown + chart bands (DONE — pending L3 re-run)

| Task | Status | Notes |
|------|--------|-------|
| Schema: `l3_forecast_by_series` | ✅ | `schemas/config.py` + `navigator.ts` |
| Pipeline: split rows by `breakdown_by`, batch `tfm.forecast` | ✅ | `l3_forecaster.py` |
| Cap breakdown cardinality (max 8) | ✅ | `top_breakdown_keys()` |
| Skip series with &lt; 64 buckets | ✅ | Omitted from batch; L2 flat fallback on chart |
| Frontend: dashed L3 + p10/p90 **per series** | ✅ | `NavigatorKpiChart` breakdown block |
| Aggregate `l3_forecast` for tile headline | ✅ | Combined-rows series in pipeline |
| Normalizer: clear stale aggregate-only L3 | ✅ | Keeps per-series after L3 run |

**Effort estimate:** ~1–2 days. TimesFM batch API already supports `[series_a, series_b, ...]`.

### Phase 2b — L2 per breakdown chart (DONE)

| Task | Status | Notes |
|------|--------|-------|
| Dashed L2 line per facility on 7D/30D | ✅ | ratio/stable when no per-series L3 |

### Phase 3 — Remaining KPI classes (NOT STARTED)

| Issue | KPIs | Fix |
|-------|------|-----|
| L1 lifetime total vs L3 daily | Total Referrals, Pending Referrals, Escalations | Cumulative-series detection → latest-period increment |
| Wrong field / units | Staffing Gap % (chart shows -0.7, headline -14%) | Agent + validator: correct `value_field` |
| `x_axis` ≠ `date_field` | Staffing Gap, Overtime, Productivity | Normalizer syncs `date_field` → `chart.x_axis` |
| `value_field` mismatch | Staffing Gap %, etc. | Normalizer + compute try l1.field_name / y_axis |
| No L3 | Labor Cost, Staffing Gap, Overtime, Productivity | Eligibility + date fix + re-run L3 |
| `date_field: "null"` | Referral KPIs | Normalizer fixes some; verify after pipeline |

### Phase 4 — Ops / config hygiene (PARTIAL)

| Task | Status |
|------|--------|
| Stop `_l3.json` winning API file sort | ✅ `api/config_files.py` — mtime sort |
| Single config artifact per pipeline run (L3 inside runner) | ❌ Partial — runner has L3 step |
| QA: stop duplicate KPIs (“Average ED Holds”) | ❌ |
| Post-pipeline config audit script | ❌ |
| `NavigatorKpiCard.tsx` → metricCompute | ✅ Done |
| `pipeline/audit_config.py` | ✅ Done |
| `run_l3.py` fetches sheet views | ✅ Done |

---

## KPI audit snapshot (`_l3.json`, 18 KPIs)

**User-confirmed fixed (Phase 1):** Available Beds, ED Holds, Pending Transfers

**Breakdown + needs L3 per series:** Occupancy Trend by Facility, Current Occupancy Rate, Total Referrals, Referral Escalations

**Single-series L3 OK in principle:** Referral Turnaround Time (after date_field fix)

**No L3 / L2-only today:** Staffing Gap %, Overtime %, Productivity, Labor Cost, Referral Trend by Status

**Pre-forecast Tableau views (L1 only):** Forecasted Occupancy Rate, Predicted Staffing Shortage, etc.

---

## Verification checklist (run each session)

### Local setup

```bash
git checkout feature/kpi-metric-contract
# frontend: npm run dev in frontend/
# API on :8001
# URL: ?workbook=Navigator_Predictive_Analytics_v2_Extract&company=navigator_predictive_analytics_v2_extract
```

### Per KPI smoke test

| KPI | Now (L1) | 7D headline | 30D headline | Chart history scale | Chart 7D/30D overlay |
|-----|----------|-------------|--------------|---------------------|---------------------|
| Available Beds | | | | | |
| ED Holds | | | | | |
| Pending Transfers | | | | | |
| Occupancy by Facility | | | | | |
| Total Referrals | | | | | |
| Labor Cost | | | | | |
| Staffing Gap % | | | | | |

### Pipeline

```bash
python run_l3.py output/intelligence_config_Navigator_Predictive_Analytics_v2_Extract_l3.json
# Or full pipeline when ready
```

### Config audit (quick)

```bash
python -c "from pipeline.metric_contract import normalize_config; from schemas.config import IntelligenceConfig; from pathlib import Path; c=IntelligenceConfig.from_json(Path('output/intelligence_config_Navigator_Predictive_Analytics_v2_Extract_l3.json').read_text()); print(normalize_config(c))"
```

---

## Decisions log

| Date | Decision |
|------|----------|
| 2026-06-10 | Work on `feature/kpi-metric-contract`; keep `v1.0.4` as baseline |
| 2026-06-10 | Single aggregate L3 on breakdown charts is **misleading** — disabled until per-series L3 |
| 2026-06-10 | TimesFM: same for all KPIs; per-facility = multiple arrays in one `forecast()` call |
| 2026-06-10 | Target: **every chart series** gets L3 point + p10/p90 band (single or multi) |

---

## Session changelog

_Update this section at the end of each turn._

### 2026-06-10 (session 1)

- Created branch `feature/kpi-metric-contract`
- Implemented Phase 1: `metric_contract.py`, `metricCompute.ts`, wiring
- User confirmed Beds / ED Holds / Pending Transfers fixed
- Created this plan file

### 2026-06-10 (session 3 — continued)

- **Cumulative field detection** — monotonic running-totals → per-period increments (L1/L2/L3)
- **`pipeline/l3_cache.py`** — `run_l3.py` fetches Hyper tables **and** sheet views via VDS
- **`run_l3.py`** — mtime config pick, normalize before L3, per-series summary
- **`pipeline/audit_config.py`** — generic post-pipeline audit (`python -m pipeline.audit_config`)
- **`NavigatorKpiCard.tsx`** — wired to `metricCompute` (L1/L2/L3 parity with Canvas)
- **Domain agent** — `date_field` must match `chart.x_axis`; `value_field` ↔ `l1.field_name`
- Frontend build passes

### 2026-06-10 (session 2 — 100% generic fix)

- **Phase 2:** `l3_forecast_by_series` schema + batch TimesFM per `breakdown_by` (top 8 series)
- **Phase 2b:** Per-series L2 flat overlay (ratio/stable) when L3 missing for a line
- **Charts:** `filterRowsForPeriod()` — 7D/30D truncates history; `groupByStacked` uses `temporalGroupKey`
- **Normalizer:** `date_field` ↔ `x_axis` sync, `value_field` fallback from `l1.field_name`
- **API:** Latest config by file mtime (`api/config_files.py`) — fixes `_l3.json` drift
- Frontend build passes
- **Action required:** `python run_l3.py <config>` to populate per-series forecasts
- **No git commit yet**

### _Next session template_

```
### YYYY-MM-DD
- [ ] ...
- Verified: ...
- Commits: ...
- Blockers: ...
```

---

## Next recommended actions (ordered)

1. **Re-run L3:** `python run_l3.py` (needs Tableau creds + `output/` config)
2. **Audit:** `python -m pipeline.audit_config output/your_config.json`
3. **Verify** in browser — Occupancy by Facility, referrals, staffing gap
4. **Commit** on `feature/kpi-metric-contract` when satisfied

---

## Key file map

| File | Role |
|------|------|
| `pipeline/metric_contract.py` | Single source of truth (Python) |
| `frontend/src/lib/metricCompute.ts` | Single source of truth (UI) |
| `pipeline/l3_forecaster.py` | TimesFM; extend for per-breakdown |
| `pipeline/runner.py` | Pipeline orchestration + normalize + L3 |
| `agents/domain_agent.py` | Agent prompts for L1/L2 |
| `frontend/src/components/NavigatorCanvas.tsx` | Tiles, period, L1/L2/L3 headline |
| `frontend/src/components/NavigatorKpiChart.tsx` | Chart rendering, overlays |
| `schemas/config.py` | IntelligenceConfig schema — extend for per-series L3 |
| `api/routes/dashboard.py` | Config serving — fix `_l3.json` sort issue later |
