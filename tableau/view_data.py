"""
tableau/view_data.py
─────────────────────
Helper utilities for fetching, summarising and extracting KPI values from
Tableau view data rows.

Used by domain agents via the fetch_view_data tool.

parse_value()   — parse any Tableau display-formatted string to float
                  ("$1.2B" → 1.2e9, "19.5%" → 19.5, "(500)" → -500)
normalize_rows() — strip Grand Total rows, unpivot Measure_Names/Values pivot,
                   remove blank rows
summarise_rows() — compact summary for passing to agents (uses both helpers)
"""

from __future__ import annotations

import logging
import statistics
from collections import OrderedDict
from typing import Any

log = logging.getLogger(__name__)

# ── Value strings that should be treated as missing ──────────────────────────
_NULL_STRINGS = frozenset({
    "", "-", "—", "–", "n/a", "na", "null", "none", "#num!", "#n/a",
    "#value!", "#ref!", "#div/0!", ".", "..", "...",
})


def parse_value(raw: Any) -> float | None:
    """
    Parse a Tableau display-formatted value to a plain float.

    Handles:
      "$6,928"      → 6928.0
      "€1,234"      → 1234.0
      "$1.2B"       → 1_200_000_000.0
      "$2.3M"       → 2_300_000.0
      "45.7K"       → 45_700.0
      "1.2T"        → 1_200_000_000_000.0
      "19.5%"       → 19.5   (kept as-is; NOT divided by 100)
      "(500)"       → -500.0  (parentheses = negative)
      "1,234.56"    → 1234.56
      "N/A", ""     → None
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if s.lower() in _NULL_STRINGS:
        return None

    # ── Parentheses = negative ─────────────────────────────────────────────────
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()

    # ── Strip leading currency symbols ─────────────────────────────────────────
    s = s.lstrip("$€£¥₹").strip()

    # ── Strip trailing percent (value kept as-is, e.g. 19.5 not 0.195) ────────
    if s.endswith("%"):
        s = s[:-1].strip()

    # ── Scale suffixes (last character, case-insensitive) ─────────────────────
    multiplier = 1.0
    if s:
        last = s[-1].upper()
        if last == "T":
            multiplier, s = 1e12, s[:-1]
        elif last == "B":
            multiplier, s = 1e9, s[:-1]
        elif last == "M":
            multiplier, s = 1e6, s[:-1]
        elif last == "K":
            multiplier, s = 1e3, s[:-1]

    # ── Strip commas (thousands separators) ────────────────────────────────────
    s = s.replace(",", "").strip()

    try:
        val = float(s) * multiplier
        return -val if negative else val
    except (ValueError, TypeError):
        return None


# ── Grand Total markers ───────────────────────────────────────────────────────
_TOTAL_MARKERS = frozenset({"grand total", "total", "subtotal", "all", "overall"})


def normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Preprocess raw Tableau rows before analysis.

    1. Strip "Grand Total" / "Total" summary rows — they inflate aggregates.
    2. Unpivot Measure_Names / Measure_Values pivot format into wide format:
       each dimension combination gets ONE row, with separate columns per measure.
    3. Remove fully blank / null rows.

    Returns the cleaned list (original list returned unchanged if empty).
    """
    if not rows:
        return rows

    # ── 1. Strip Grand Total rows ──────────────────────────────────────────────
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        if any(str(v).strip().lower() in _TOTAL_MARKERS for v in row.values()):
            continue
        cleaned.append(row)
    # Safety: if every row matched (odd edge-case), keep originals
    rows = cleaned if cleaned else rows

    # ── 2. Unpivot Measure_Names / Measure_Values ──────────────────────────────
    if not rows:
        return rows

    cols = list(rows[0].keys())

    def _norm(c: str) -> str:
        """Normalise column name: lowercase + spaces→underscores."""
        return c.replace(" ", "_").lower()

    mn_col = next((c for c in cols if _norm(c) == "measure_names"), None)
    mv_col = next((c for c in cols if _norm(c) == "measure_values"), None)

    if mn_col and mv_col:
        dim_cols = [c for c in cols if c not in (mn_col, mv_col)]

        # wide[dim_key] = {measure_name: measure_value}
        wide: OrderedDict[tuple, dict[str, Any]] = OrderedDict()
        # dim_data[dim_key] = {dim_col: value}  — stable values for non-measure cols
        dim_data: dict[tuple, dict[str, Any]] = {}

        for row in rows:
            dim_key = tuple(row.get(c) for c in dim_cols)
            mname = str(row.get(mn_col, "")).strip()
            mval  = row.get(mv_col)

            if dim_key not in wide:
                wide[dim_key] = {}
                dim_data[dim_key] = {c: row.get(c) for c in dim_cols}

            if mname and mval is not None and str(mval).strip():
                wide[dim_key][mname] = mval

        # Reconstruct: dim columns + all discovered measure columns
        unpivoted: list[dict[str, Any]] = []
        for dk, measures in wide.items():
            r = dict(dim_data[dk])
            r.update(measures)
            unpivoted.append(r)

        if unpivoted:
            rows = unpivoted

    # ── 3. Remove fully blank rows ─────────────────────────────────────────────
    BLANK = frozenset({"", "null", "none"})
    rows = [
        row for row in rows
        if any(
            v is not None and str(v).strip().lower() not in BLANK
            for v in row.values()
        )
    ]

    return rows if rows else []


# ─────────────────────────────────────────────────────────────────────────────


def aggregate_column(
    rows: list[dict[str, Any]],
    column: str,
    method: str = "sum",
) -> float | None:
    """
    Aggregate a numeric column from view rows.

    method: "sum" | "avg" | "min" | "max" | "count" | "latest"
    Returns None if column not found or no numeric values.
    """
    if not rows:
        return None

    values: list[float] = []
    for row in rows:
        val = parse_value(row.get(column))
        if val is not None:
            values.append(val)

    if not values:
        return None

    if method == "sum":
        return sum(values)
    if method == "avg":
        return statistics.mean(values)
    if method == "min":
        return min(values)
    if method == "max":
        return max(values)
    if method == "count":
        return float(len(values))
    if method == "latest":
        return values[-1]

    return sum(values)  # default


def detect_trend(
    rows: list[dict[str, Any]],
    value_column: str,
    date_column: str | None = None,
) -> dict[str, Any]:
    """
    Detect a simple trend (up/down/flat) from a series of rows.

    Returns:
        {
          "direction": "up" | "down" | "flat",
          "pct_change": float,   # % change from first to last
          "description": str,    # human-readable
        }
    """
    values: list[float] = []
    for row in rows:
        val = parse_value(row.get(value_column))
        if val is not None:
            values.append(val)

    if len(values) < 2:
        return {"direction": "flat", "pct_change": 0.0, "description": "Insufficient data to determine trend"}

    first, last = values[0], values[-1]
    if first == 0:
        pct = 100.0 if last > 0 else 0.0
    else:
        pct = (last - first) / abs(first) * 100

    if pct > 2:
        direction = "up"
        desc = f"Up {abs(pct):.1f}% from start to end of period"
    elif pct < -2:
        direction = "down"
        desc = f"Down {abs(pct):.1f}% from start to end of period"
    else:
        direction = "flat"
        desc = f"Relatively stable ({pct:+.1f}%)"

    return {"direction": direction, "pct_change": round(pct, 2), "description": desc}


def detect_anomalies(
    rows: list[dict[str, Any]],
    value_column: str,
    z_threshold: float = 2.5,
) -> list[dict[str, Any]]:
    """
    Detect statistical anomalies using z-score.

    Returns list of {row_index, value, z_score} for outliers.
    """
    values: list[float] = []
    for row in rows:
        val = parse_value(row.get(value_column))
        values.append(val if val is not None else float("nan"))

    clean = [v for v in values if v == v]  # filter NaN (NaN != NaN)
    if len(clean) < 4:
        return []

    mean = statistics.mean(clean)
    try:
        stdev = statistics.stdev(clean)
    except statistics.StatisticsError:
        return []

    if stdev == 0:
        return []

    anomalies = []
    for i, v in enumerate(values):
        if v != v:  # NaN
            continue
        z = abs((v - mean) / stdev)
        if z > z_threshold:
            anomalies.append({"row_index": i, "value": v, "z_score": round(z, 2)})

    return anomalies


def summarise_rows(
    rows: list[dict[str, Any]],
    max_rows: int = 200,
) -> dict[str, Any]:
    """
    Produce a compact summary of a dataset for passing to an agent.

    Applies normalize_rows() first (strips Grand Totals, unpivots pivot views),
    then uses parse_value() for all numeric computations so scale suffixes
    ($1.2B, $2.3M, 45K) and formatted strings ("19.5%", "($500)") are handled.

    Returns:
        {
            "total_rows":           int,
            "columns":              [str],
            "sample":               [dict],   # up to max_rows raw rows
            "numeric_summary":      {col: {min, max, mean, sum}},
            "categorical_breakdown": {
                cat_col: {
                    cat_value: {num_col: sum, ...},
                    ...
                }
            }
            # categorical_breakdown lets the agent compute FILTERED aggregates:
            # e.g. Referral Count where Status='Approved' without iterating rows.
            # Only built for categorical columns with ≤ 30 distinct values.
        }
    """
    # ── Pre-process ────────────────────────────────────────────────────────────
    rows = normalize_rows(rows)

    if not rows:
        return {
            "total_rows": 0, "columns": [], "sample": [],
            "numeric_summary": {}, "categorical_breakdown": {},
        }

    columns = list(rows[0].keys())
    sample  = rows[:max_rows]

    # ── Classify columns ───────────────────────────────────────────────────────
    numeric_cols:     list[str] = []
    categorical_cols: list[str] = []

    for col in columns:
        nums = [parse_value(r.get(col)) for r in rows]
        numeric_vals = [v for v in nums if v is not None]
        # A column is numeric if >50% of its non-null values parse to numbers
        total_non_null = sum(1 for r in rows if r.get(col) not in (None, "", "null"))
        if total_non_null > 0 and len(numeric_vals) / total_non_null > 0.5:
            numeric_cols.append(col)
        else:
            categorical_cols.append(col)

    # ── Numeric summary (across ALL rows) ─────────────────────────────────────
    numeric_summary: dict[str, dict] = {}
    for col in numeric_cols:
        nums = [parse_value(r.get(col)) for r in rows]
        nums = [v for v in nums if v is not None]
        if nums:
            numeric_summary[col] = {
                "min":  round(min(nums), 4),
                "max":  round(max(nums), 4),
                "mean": round(statistics.mean(nums), 4),
                "sum":  round(sum(nums), 4),
            }

    # ── Categorical breakdown ──────────────────────────────────────────────────
    # For each low-cardinality categorical column, compute numeric aggregates
    # per category value. This lets the agent compute filtered aggregates without
    # iterating rows — e.g. "Referral Count WHERE Status='Approved'" directly.
    categorical_breakdown: dict[str, dict] = {}

    for cat_col in categorical_cols:
        distinct_vals = list({str(r.get(cat_col, "")) for r in rows if r.get(cat_col) not in (None, "")})
        if len(distinct_vals) > 30:
            continue  # too many categories — skip (high-cardinality like names/IDs)

        breakdown: dict[str, dict] = {}
        for val in sorted(distinct_vals):
            filtered = [r for r in rows if str(r.get(cat_col, "")) == val]
            if not filtered:
                continue
            aggs: dict[str, Any] = {"count": len(filtered)}
            for num_col in numeric_cols:
                nums = [parse_value(r.get(num_col)) for r in filtered]
                nums = [v for v in nums if v is not None]
                if nums:
                    aggs[f"{num_col}_sum"]  = round(sum(nums), 4)
                    aggs[f"{num_col}_mean"] = round(statistics.mean(nums), 4)
            breakdown[val] = aggs

        if breakdown:
            categorical_breakdown[cat_col] = breakdown

    return {
        "total_rows":            len(rows),
        "columns":               columns,
        "sample":                sample,
        "numeric_summary":       numeric_summary,
        "categorical_breakdown": categorical_breakdown,
    }


def extract_top_n(
    rows: list[dict[str, Any]],
    key_column: str,
    value_column: str,
    n: int = 5,
    descending: bool = True,
) -> list[dict[str, Any]]:
    """Return top-N rows ranked by value_column."""
    keyed: list[tuple[Any, float, dict]] = []
    for row in rows:
        val = parse_value(row.get(value_column))
        if val is None:
            continue
        key = row.get(key_column, "")
        keyed.append((key, val, row))

    keyed.sort(key=lambda x: x[1], reverse=descending)
    return [r for _, _, r in keyed[:n]]
