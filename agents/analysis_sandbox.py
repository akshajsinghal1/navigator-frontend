"""
agents/analysis_sandbox.py
──────────────────────────
Shared, safe pandas execution for run_analysis (domain + QA agents).

Two problems this fixes:
  1. eval() only runs EXPRESSIONS — agents kept writing assignments like
     `df['x'] = df['x'].str.rstrip('%').astype(float); df.groupby(...)...`
     which raised "invalid syntax". We now support multi-statement code via
     ast: exec everything except the last expression, then eval the last.
  2. String/percent columns ('74.3%', '1,234') arrive as dtype=object, so
     df.groupby(...)[col].mean() failed with "agg function failed". We now
     auto-coerce numeric-looking columns to real numbers BEFORE the agent
     runs anything — so the agent rarely needs to clean them at all.

Still safe: no imports, no file/network builtins; only pd, np, and a curated
set of numeric builtins are in scope.
"""

from __future__ import annotations

import ast
import re
from typing import Any

import numpy as np
import pandas as pd

_SAFE_BUILTINS = {
    "float": float, "int": int, "str": str, "bool": bool,
    "len": len, "sum": sum, "round": round, "abs": abs,
    "min": min, "max": max, "list": list, "tuple": tuple,
    "dict": dict, "set": set, "range": range, "sorted": sorted,
    "enumerate": enumerate, "zip": zip, "map": map, "filter": filter,
    "True": True, "False": False, "None": None,
    "isinstance": isinstance, "any": any, "all": all,
}

_NUM_CLEAN = re.compile(r"[,$%€£¥₹₩\s]")


def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert numeric-looking object columns to real numbers so groupby/mean work.
    A column is converted only if ≥80% of its non-null values parse cleanly —
    genuine text/category columns are left untouched.
    """
    for col in df.columns:
        s = df[col]
        if s.dtype != object:
            continue
        cleaned = (
            s.astype(str)
             .str.replace(_NUM_CLEAN, "", regex=True)
             .str.replace(r"^\((.*)\)$", r"-\1", regex=True)  # (500) -> -500
        )
        conv = pd.to_numeric(cleaned, errors="coerce")
        nonnull = s.notna().sum()
        if nonnull and conv.notna().sum() / nonnull >= 0.8:
            df[col] = conv
    return df


def _execute(expression: str, df: pd.DataFrame) -> Any:
    g: dict[str, Any] = {"__builtins__": _SAFE_BUILTINS, "pd": pd, "np": np}
    l: dict[str, Any] = {"df": df}
    tree = ast.parse(expression, mode="exec")
    if not tree.body:
        return None
    last = tree.body[-1]
    if isinstance(last, ast.Expr):
        if len(tree.body) > 1:
            prefix = ast.Module(body=tree.body[:-1], type_ignores=[])
            exec(compile(prefix, "<analysis>", "exec"), g, l)  # noqa: S102
        return eval(compile(ast.Expression(body=last.value), "<analysis>", "eval"), g, l)  # noqa: S307
    exec(compile(tree, "<analysis>", "exec"), g, l)  # noqa: S102
    return l.get("result")


def run_pandas_analysis(expression: str, rows: list[dict]) -> Any:
    """
    Execute `expression` (which may be multi-statement) against a DataFrame `df`.

    Robust to BOTH styles:
      • clean style — numeric columns are auto-coerced, so `df.groupby(..).mean()`
        just works without stripping '%'.
      • manual style — if the agent still writes `.str.rstrip('%').astype(float)`
        (which fails on an already-numeric column), we transparently retry on the
        RAW, uncoerced data so it succeeds anyway.
    """
    coerced = _coerce_numeric(pd.DataFrame(rows))
    try:
        return _execute(expression, coerced)
    except Exception:
        # Retry on raw (string) data — handles manual .str/.astype cleaning
        return _execute(expression, pd.DataFrame(rows))


def format_result(value: Any, limit: int = 2000) -> str:
    """Render an analysis result to a compact string for the LLM."""
    if value is None:
        return "(no value returned — assign to `result` or end with an expression)"
    s = value.to_string() if hasattr(value, "to_string") else str(value)
    return s[:limit] + ("…" if len(s) > limit else "")
