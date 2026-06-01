"""
pipeline/l2_evaluator.py
─────────────────────────
Deterministic L2 KPI evaluator.

L2 = "what would this KPI be if we applied the existing Tableau formulas
      and current parameter default values to the current L1 value?"

This is PURE MATH — no AI involved.

How it works:
1. Find the KPI's calculated field formula in the inventory
2. Find the parameter values used in the formula (use their Tableau defaults)
3. Substitute: replace [Field] references with known values
4. Evaluate the arithmetic expression safely (no exec/eval on user code)

Currently supports:
  - Simple arithmetic formulas: +, -, *, /
  - Parameter references: [Param Name]
  - Field references: [Field Name] (replaced with l1_value if it matches)
  - Nested parens

If evaluation fails for any reason, returns L2 with error field set (never crashes).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from schemas.config import L2Data

log = logging.getLogger(__name__)

# Match [Something] references in a formula
_REF_RE = re.compile(r"\[([^\]]+)\]")

# Safe arithmetic evaluator — only digits, operators, parens, decimal, space
_SAFE_EXPR_RE = re.compile(r"^[\d\s\+\-\*\/\.\(\)]+$")


def _safe_eval(expr: str) -> float:
    """
    Evaluate a purely arithmetic expression string safely.
    Raises ValueError if the expression is not safe or not valid.
    """
    clean = expr.strip()
    if not _SAFE_EXPR_RE.match(clean):
        raise ValueError(f"Unsafe or non-arithmetic expression: {clean!r}")
    # Use Python's compile + eval with empty builtins for safety
    code = compile(clean, "<l2_expr>", "eval")
    # Whitelist: no builtins, no globals, no locals
    result = eval(code, {"__builtins__": {}}, {})  # noqa: S307 (intentionally restricted)
    return float(result)


def _extract_parameter_defaults(filtered_inventory: dict[str, Any]) -> dict[str, float | None]:
    """
    Extract parameter default values from the inventory.
    Since the raw inventory doesn't store current parameter values (they're
    user-set at runtime), we can't know the exact value here.
    We return None for each parameter — callers must handle this.

    In a live system, you'd fetch parameter values via the Tableau API.
    For now, this is a placeholder that signals "parameter exists but value unknown".
    """
    params: dict[str, float | None] = {}
    for p in filtered_inventory.get("parameters", []):
        name = p.get("name", "")
        if name:
            params[name] = None  # unknown at this stage
    return params


def _extract_field_formulas(filtered_inventory: dict[str, Any]) -> dict[str, str]:
    """
    Build a map of field_name → formula from the inventory.
    Only includes CalculatedField entries with a formula.
    """
    formulas: dict[str, str] = {}
    for ds in filtered_inventory.get("embedded_datasources", []):
        for field in ds.get("fields", []):
            if field.get("type") == "CalculatedField" and field.get("formula"):
                formulas[field["name"]] = field["formula"]
    return formulas


def evaluate_l2(
    kpi_raw: dict[str, Any],
    filtered_inventory: dict[str, Any],
) -> L2Data | None:
    """
    Attempt to compute an L2 forecast for a KPI.

    Args:
        kpi_raw           : a KPI dict from domain agent result
        filtered_inventory: the semantic-filtered inventory

    Returns:
        L2Data if the KPI has a calculable formula, else None.
    """
    kpi_name  = kpi_raw.get("name", "")
    l1_value  = kpi_raw.get("l1_value")

    # Find the formula for this KPI by matching its field name
    formulas   = _extract_field_formulas(filtered_inventory)
    field_name = kpi_raw.get("l1_field_name", kpi_name)

    formula = formulas.get(field_name) or formulas.get(kpi_name)
    if not formula:
        return None  # no formula → no L2

    # Find parameters in the formula
    refs   = _REF_RE.findall(formula)
    params = _extract_parameter_defaults(filtered_inventory)
    param_names_in_formula = [r for r in refs if r in params]

    if not param_names_in_formula:
        # Formula exists but uses only field/column references — no adjustable parameters.
        # We can't evaluate it without knowing all referenced field aggregates,
        # but we still capture it so the frontend can display the formula.
        return L2Data(
            formula         = formula,
            parameters_used = [],
            forecast_value  = None,
            method          = "formula_eval",
            error           = (
                "Formula uses field references only (no parameters). "
                "Evaluation requires runtime aggregates from Tableau. "
                "Formula is captured for display purposes."
            ),
        )

    # Attempt substitution
    try:
        expr = formula

        # Replace known field reference with l1_value (current state)
        if l1_value is not None:
            try:
                l1_float = float(l1_value)
                expr = re.sub(
                    re.escape(f"[{field_name}]"),
                    str(l1_float),
                    expr,
                    flags=re.IGNORECASE,
                )
                expr = re.sub(
                    re.escape(f"[{kpi_name}]"),
                    str(l1_float),
                    expr,
                    flags=re.IGNORECASE,
                )
            except (ValueError, TypeError):
                pass

        # Check if all parameter values are known (they may be None)
        unknown_params = [p for p in param_names_in_formula if params.get(p) is None]
        if unknown_params:
            # Parameter values not available — describe formula but can't compute
            return L2Data(
                formula          = formula,
                parameters_used  = param_names_in_formula,
                forecast_value   = None,
                method           = "formula_eval",
                error            = f"Parameter values not available for runtime evaluation: {unknown_params}. "
                                   f"Fetch current parameter values from Tableau to enable L2.",
            )

        # Substitute all parameter values
        for pname, pval in params.items():
            if pval is not None:
                expr = re.sub(
                    re.escape(f"[{pname}]"),
                    str(float(pval)),
                    expr,
                    flags=re.IGNORECASE,
                )

        # Remove any remaining [Ref] that we couldn't resolve
        remaining = _REF_RE.findall(expr)
        if remaining:
            return L2Data(
                formula         = formula,
                parameters_used = param_names_in_formula,
                forecast_value  = None,
                method          = "formula_eval",
                error           = f"Unresolved references in formula: {remaining}",
            )

        # Evaluate
        forecast = _safe_eval(expr)
        return L2Data(
            formula         = formula,
            parameters_used = param_names_in_formula,
            forecast_value  = round(forecast, 4),
            method          = "formula_eval",
        )

    except Exception as exc:
        log.warning("L2 evaluation failed for KPI '%s': %s", kpi_name, exc)
        return L2Data(
            formula         = formula,
            parameters_used = param_names_in_formula,
            forecast_value  = None,
            method          = "formula_eval",
            error           = str(exc),
        )
