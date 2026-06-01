"""
fetch_and_fill.py
─────────────────
Connects to Tableau using existing credentials, fetches real CSV data from
each key view, aggregates the KPI values, detects trends, and fills in all
the null fields in the Intelligence Config.

Run:
    python fetch_and_fill.py

Requires:
    TABLEAU_* vars in .env  (already set)
    No ANTHROPIC_API_KEY needed
"""

from __future__ import annotations

import json
import logging
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)


# ── which view to fetch for each KPI ─────────────────────────────────────────
# (kpi_id, view_name, value_column, agg_method, date_column)
# Column names verified against actual Tableau CSV export
KPI_VIEW_MAP = [
    # Revenue & Profitability
    ("total_sales",              "Performance",      "Sales",                "sum",  "Month of Order Date"),
    ("profit_ratio",             "Customers",        "Profit Ratio",         "avg",  None),
    ("profit_per_order",         "Customers",        "Profit",               "sum",  None),
    ("sales_vs_target",          "Performance",      "Sales",                "sum",  None),

    # Customer Performance
    ("sales_per_customer",       "Customers",        "Sales per Customer",   "avg",  None),
    ("top_customers_by_revenue", "Customers",        "Sales",                "sum",  None),
    ("order_profitability_rate", "Customers",        "Profit",               "sum",  None),

    # Shipping & Operations
    ("on_time_shipment_rate",    "Shipping",         "Ship Status",          "rate", None),
    ("avg_days_to_ship",         "Shipping",         "Days to Ship Actual",  "avg",  None),

    # Sales Force & Forecasting
    ("quota_attainment",         "Commission Model", "Achievement (estimated)", "avg", None),
    ("ote_vs_actual_compensation","Commission Model","Total Compensation",   "avg",  None),
    ("sales_forecast",           "Forecast",         "Sales",                "sum",  "Month of Order Date"),
]


def _clean_num(v) -> float | None:
    """Try to parse a value as float, stripping currency/percent symbols."""
    if v is None:
        return None
    s = str(v).replace(",", "").replace("$", "").replace("%", "").strip()
    if s in ("", "null", "None", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _aggregate(rows: list[dict], col: str, method: str) -> float | None:
    """Aggregate a column from rows."""
    # Try exact match first, then case-insensitive
    key = col
    if rows and col not in rows[0]:
        mapping = {k.lower(): k for k in rows[0].keys()}
        key = mapping.get(col.lower(), col)

    # Special: "rate" = % of rows where col == "Shipped On Time"
    if method == "rate":
        total    = len(rows)
        on_time  = sum(1 for r in rows if "on time" in str(r.get(key, "")).lower())
        return round(on_time / total * 100, 1) if total else None

    values = [_clean_num(r.get(key)) for r in rows]
    values = [v for v in values if v is not None]

    if not values:
        return None

    if method == "sum":   return round(sum(values), 2)
    if method == "avg":   return round(statistics.mean(values), 4)
    if method == "min":   return round(min(values), 4)
    if method == "max":   return round(max(values), 4)
    if method == "count": return float(len(values))
    return round(sum(values), 2)


def _detect_trend(rows: list[dict], value_col: str, date_col: str) -> str | None:
    """Detect simple trend from time-ordered rows."""
    if not rows or not date_col:
        return None

    # Find actual column names
    val_key  = value_col
    date_key = date_col
    if rows:
        mapping = {k.lower(): k for k in rows[0].keys()}
        val_key  = mapping.get(value_col.lower(),  value_col)
        date_key = mapping.get(date_col.lower(), date_col)

    # Sort by date
    dated = []
    for r in rows:
        raw_date = r.get(date_key, "")
        val = _clean_num(r.get(val_key))
        if raw_date and val is not None:
            dated.append((str(raw_date), val))

    if len(dated) < 2:
        return None

    dated.sort(key=lambda x: x[0])

    # Compare first half average to second half average
    mid = len(dated) // 2
    first_avg  = statistics.mean([v for _, v in dated[:mid]])
    second_avg = statistics.mean([v for _, v in dated[mid:]])

    if first_avg == 0:
        return None

    pct = (second_avg - first_avg) / abs(first_avg) * 100

    if pct > 5:
        return f"Up {abs(pct):.1f}% — growing over the period"
    elif pct < -5:
        return f"Down {abs(pct):.1f}% — declining over the period"
    else:
        return f"Relatively stable ({pct:+.1f}% change)"


def _guess_format(kpi_id: str, col: str, method: str = "") -> tuple[str, str]:
    """Guess unit and format from KPI id / column name."""
    name = (kpi_id + " " + col).lower()
    if method == "rate" or "on_time" in name or "rate" in name or "ratio" in name or "%" in name or "quota" in name:
        return "%", "percentage"
    if "days" in name:
        return "days", "number"
    if "achievement" in name or "compensation" in name or "ote" in name or "commission" in name or "sales" in name or "profit" in name:
        return "USD", "currency"
    return "", "number"


def _find_col(rows: list[dict], preferred: str) -> str:
    """Find the best matching column name in rows."""
    if not rows:
        return preferred
    keys = list(rows[0].keys())
    # Exact match
    if preferred in keys:
        return preferred
    # Case-insensitive
    mapping = {k.lower(): k for k in keys}
    return mapping.get(preferred.lower(), preferred)


def main():
    # ── load existing config ──────────────────────────────────────────────────
    config_files = sorted(
        Path("output").glob("intelligence_config_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not config_files:
        log.error("No intelligence_config_*.json found in output/")
        return

    config_path = config_files[0]
    log.info("Loading config: %s", config_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))

    # ── connect to Tableau ────────────────────────────────────────────────────
    from tableau.connector import TableauConnector

    workbook_url = os.environ.get("TARGET_WORKBOOK_CONTENT_URL", "Superstore")

    log.info("Connecting to Tableau...")
    with TableauConnector.from_env() as conn:
        wb = conn.get_workbook_by_content_url(workbook_url)
        workbook_luid = wb["luid"]
        log.info("Connected. Workbook: %s (luid=%s)", wb["name"], workbook_luid)

        # ── fetch data per view ───────────────────────────────────────────────
        view_cache: dict[str, list[dict]] = {}

        def fetch_view(view_name: str) -> list[dict]:
            if view_name not in view_cache:
                log.info("  Fetching view: %s", view_name)
                try:
                    rows = conn.get_view_data_by_name(workbook_luid, view_name, max_rows=500)
                    view_cache[view_name] = rows
                    log.info("    → %d rows", len(rows))
                except Exception as exc:
                    log.warning("    → FAILED: %s", exc)
                    view_cache[view_name] = []
            return view_cache[view_name]

        # ── build kpi_id → result map ─────────────────────────────────────────
        kpi_data: dict[str, dict] = {}

        for kpi_id, view_name, value_col, agg_method, date_col in KPI_VIEW_MAP:
            rows = fetch_view(view_name)
            if not rows:
                kpi_data[kpi_id] = {}
                continue

            value      = _aggregate(rows, value_col, agg_method)
            trend      = _detect_trend(rows, value_col, date_col) if date_col else None
            unit, fmt  = _guess_format(kpi_id, value_col, agg_method)
            col_actual = _find_col(rows, value_col)

            kpi_data[kpi_id] = {
                "l1_value":    value,
                "l1_unit":     unit,
                "l1_format":   fmt,
                "l1_view":     view_name,
                "l1_field":    col_actual,
                "trend":       trend,
                "raw_sample":  rows[:20],   # keep sample for frontend
                "total_rows":  len(rows),
                "columns":     list(rows[0].keys()) if rows else [],
            }

    # ── patch the config in-place ─────────────────────────────────────────────
    log.info("Patching config with real values...")

    patched = 0
    for section in config.get("dashboard_sections", []):
        for kpi in section.get("kpis", []):
            kid  = kpi["id"]
            data = kpi_data.get(kid, {})

            if not data:
                continue

            # L1
            if data.get("l1_value") is not None:
                kpi["l1"] = {
                    "value":      data["l1_value"],
                    "unit":       data["l1_unit"],
                    "format":     data["l1_format"],
                    "view_name":  data["l1_view"],
                    "field_name": data["l1_field"],
                }
                patched += 1
                log.info("  ✓ %s = %s %s", kid, data["l1_value"], data["l1_unit"])

            # Trend
            if data.get("trend"):
                kpi["explanation"]["trend"] = data["trend"]

            # Raw data sample
            if data.get("raw_sample"):
                kpi["raw_data"] = data["raw_sample"]

    log.info("Patched %d KPI L1 values", patched)

    # ── save updated config ───────────────────────────────────────────────────
    ts      = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outpath = Path("output") / f"intelligence_config_Superstore_{ts}_filled.json"
    outpath.write_text(json.dumps(config, indent=2), encoding="utf-8")

    log.info("Saved: %s", outpath)
    print(f"\nFilled config saved to: {outpath}")
    print(f"  KPIs with real L1 values: {patched}/12")


if __name__ == "__main__":
    main()
