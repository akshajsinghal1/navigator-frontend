"""
Tableau Inventory → Operational Intelligence Blueprint pipeline.

Stages
──────
1  InventoryParser        extract signals from inventory JSON
2  ClaudeSemanticAnalyzer infer domain / objectives / personas / KPIs via Claude API
3  PrioritizationEngine   score and rank KPIs per persona (rule-based)
4  VisualizationSelector  recommend chart type per KPI pattern (rule-based)
5  DrillDownGraphBuilder  construct drill-down hierarchies (rule-based)
6  ExplanationGenerator   build why-surfaced explanation contracts
7  BlueprintAssembler     assemble and return the final blueprint dict
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class FieldSignal:
    name: str
    typename: str
    data_type: str | None = None
    role: str | None = None
    formula: str | None = None
    is_hidden: bool = False
    folder: str | None = None


@dataclass
class InventorySignals:
    workbook_name: str
    luid: str
    views: list[str]
    all_fields: list[FieldSignal]
    calculated_fields: list[FieldSignal]
    parameters: list[dict]
    upstream_tables: list[dict]
    upstream_databases: list[dict]
    connections: list[dict]
    data_quality_warnings: list[dict]
    embedded_ds_names: list[str]
    published_ds_names: list[str]


# ──────────────────────────────────────────────────────────────────────────────
# Stage 1 — Inventory Parser
# ──────────────────────────────────────────────────────────────────────────────

class InventoryParser:
    """Parses raw inventory JSON into structured InventorySignals."""

    def parse(self, inventory: dict[str, Any]) -> InventorySignals:
        wb = inventory.get("workbook", {})
        views = [v.get("name", "") for v in inventory.get("views", [])]

        seen: set[str] = set()
        all_fields: list[FieldSignal] = []

        for ds in (
            inventory.get("embedded_datasources", [])
            + inventory.get("published_datasources", [])
        ):
            for f in ds.get("fields", []):
                key = f.get("id") or f.get("name")
                if key in seen:
                    continue
                seen.add(key)
                all_fields.append(FieldSignal(
                    name=f.get("name", ""),
                    typename=f.get("__typename", ""),
                    data_type=f.get("dataType"),
                    role=f.get("role"),
                    formula=f.get("formula"),
                    is_hidden=f.get("isHidden", False),
                    folder=f.get("folderName"),
                ))

        visible = [f for f in all_fields if not f.is_hidden]
        calculated = [f for f in visible if f.typename == "CalculatedField"]

        return InventorySignals(
            workbook_name=wb.get("name", "Unknown"),
            luid=wb.get("luid", ""),
            views=views,
            all_fields=visible,
            calculated_fields=calculated,
            parameters=inventory.get("parameters", []),
            upstream_tables=inventory.get("upstream_tables", []),
            upstream_databases=inventory.get("upstream_databases", []),
            connections=inventory.get("connections", []),
            data_quality_warnings=inventory.get("data_quality_warnings", []),
            embedded_ds_names=[
                ds.get("name", "") for ds in inventory.get("embedded_datasources", [])
            ],
            published_ds_names=[
                ds.get("name", "") for ds in inventory.get("published_datasources", [])
            ],
        )


# ──────────────────────────────────────────────────────────────────────────────
# Stage 2 — Claude Semantic Analyzer
# ──────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a senior business intelligence architect specialising in converting \
Tableau workbook metadata into operational intelligence blueprints.

Your task: analyse the field inventory, view names, calculated-field formulas, \
and parameter definitions extracted from a Tableau workbook, then emit a \
complete intelligence blueprint via the emit_blueprint tool.

Rules
─────
• Derive business meaning strictly from the metadata signals provided — use \
  exact field names, formulas, and view names as evidence.
• Never fabricate field names. Use them verbatim in evidence arrays.
• Layer 1 KPIs must reference existing column or calculated fields only.
• Layer 2 KPIs must reference existing calculated fields or parameters that \
  contain explicit deterministic formulas (forecasts, targets, quotas, rates).
• Layer 3 KPIs are model proposals — describe what to predict and which \
  existing fields would serve as drivers; acknowledge no model exists yet.
• Every KPI must carry the personas who benefit from it.
• Drill-down dimensions must form a logical hierarchy from aggregate → granular.
• Output all arrays as non-empty; omit a layer only if the metadata genuinely \
  cannot support it.
"""

_EMIT_BLUEPRINT_TOOL: dict[str, Any] = {
    "name": "emit_blueprint",
    "description": "Emit the full operational intelligence blueprint as structured JSON.",
    "input_schema": {
        "type": "object",
        "required": ["workbook_summary", "business_objectives", "personas", "kpi_layers"],
        "properties": {
            "workbook_summary": {
                "type": "object",
                "required": [
                    "workbook_name", "detected_business_domain",
                    "detected_operating_model", "data_domains",
                ],
                "properties": {
                    "workbook_name": {"type": "string"},
                    "detected_business_domain": {"type": "string"},
                    "detected_operating_model": {
                        "type": "array", "items": {"type": "string"},
                        "description": "High-level operating areas, e.g. Revenue management",
                    },
                    "data_domains": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Data topic areas present in the workbook",
                    },
                },
            },
            "business_objectives": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "objective_id", "objective", "confidence",
                        "evidence", "primary_personas", "kpi_domains",
                    ],
                    "properties": {
                        "objective_id": {"type": "string"},
                        "objective": {"type": "string"},
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                        "evidence": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Exact field names that signal this objective",
                        },
                        "primary_personas": {"type": "array", "items": {"type": "string"}},
                        "kpi_domains": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "personas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "persona_id", "persona", "orientation",
                        "primary_questions", "default_kpis", "relevant_objectives",
                    ],
                    "properties": {
                        "persona_id": {"type": "string"},
                        "persona": {"type": "string"},
                        "orientation": {"type": "string"},
                        "primary_questions": {"type": "array", "items": {"type": "string"}},
                        "default_kpis": {"type": "array", "items": {"type": "string"}},
                        "relevant_objectives": {
                            "type": "array", "items": {"type": "string"},
                            "description": "objective_ids this persona cares about",
                        },
                    },
                },
            },
            "kpi_layers": {
                "type": "object",
                "required": [
                    "layer_1_current_state",
                    "layer_2_deterministic",
                    "layer_3_predictive",
                ],
                "properties": {
                    "layer_1_current_state": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": [
                                "kpi_id", "kpi", "domain", "definition",
                                "business_question", "source_fields",
                                "drill_down", "personas",
                            ],
                            "properties": {
                                "kpi_id": {"type": "string"},
                                "kpi": {"type": "string"},
                                "domain": {"type": "string"},
                                "definition": {"type": "string"},
                                "business_question": {"type": "string"},
                                "source_fields": {
                                    "type": "array", "items": {"type": "string"},
                                },
                                "drill_down": {
                                    "type": "array", "items": {"type": "string"},
                                    "description": "Ordered drill-down dimensions, aggregate → granular",
                                },
                                "personas": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                    "layer_2_deterministic": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": [
                                "kpi_id", "kpi", "definition",
                                "business_question", "source_fields",
                                "time_horizons", "personas",
                            ],
                            "properties": {
                                "kpi_id": {"type": "string"},
                                "kpi": {"type": "string"},
                                "definition": {"type": "string"},
                                "business_question": {"type": "string"},
                                "source_fields": {
                                    "type": "array", "items": {"type": "string"},
                                },
                                "time_horizons": {
                                    "type": "array", "items": {"type": "string"},
                                },
                                "personas": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                    "layer_3_predictive": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": [
                                "kpi_id", "kpi", "prediction_target",
                                "model_type", "drivers", "output_fields", "personas",
                            ],
                            "properties": {
                                "kpi_id": {"type": "string"},
                                "kpi": {"type": "string"},
                                "prediction_target": {"type": "string"},
                                "model_type": {"type": "string"},
                                "drivers": {"type": "array", "items": {"type": "string"}},
                                "output_fields": {
                                    "type": "array", "items": {"type": "string"},
                                    "description": "e.g. predicted_value, confidence, top_drivers, recommended_intervention",
                                },
                                "personas": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                },
            },
        },
    },
}


class ClaudeSemanticAnalyzer:
    """Calls Claude API with forced tool_use to emit a structured blueprint."""

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self._client = anthropic.Anthropic()
        self._model = model

    def analyze(self, signals: InventorySignals) -> dict[str, Any]:
        user_content = self._build_user_prompt(signals)
        log.info("calling Claude semantic analyzer (model=%s)", self._model)

        response = self._client.messages.create(
            model=self._model,
            max_tokens=8192,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[_EMIT_BLUEPRINT_TOOL],
            tool_choice={"type": "tool", "name": "emit_blueprint"},
            messages=[{"role": "user", "content": user_content}],
        )

        log.info(
            "Claude response received (input_tokens=%d, output_tokens=%d, "
            "cache_read=%d, cache_write=%d)",
            response.usage.input_tokens,
            response.usage.output_tokens,
            getattr(response.usage, "cache_read_input_tokens", 0),
            getattr(response.usage, "cache_creation_input_tokens", 0),
        )

        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "emit_blueprint":
                return block.input

        raise RuntimeError(
            "Claude did not return an emit_blueprint tool call. "
            f"Content types: {[getattr(b, 'type', '?') for b in response.content]}"
        )

    def _build_user_prompt(self, signals: InventorySignals) -> str:
        def bullet_list(items: list, fmt=None) -> list[str]:
            if not items:
                return ["(none)"]
            if fmt:
                return [f"- {fmt(x)}" for x in items]
            return [f"- {x}" for x in items]

        lines: list[str] = [
            f"# Workbook: {signals.workbook_name}",
            f"LUID: {signals.luid}",
            "",
            "## Dashboard / sheet views",
            *bullet_list(signals.views),
            "",
            "## Embedded datasources",
            *bullet_list(signals.embedded_ds_names),
            "",
            "## Published datasources",
            *bullet_list(signals.published_ds_names),
            "",
            "## Upstream databases",
            *bullet_list(
                signals.upstream_databases,
                lambda db: f"{db.get('name', '?')} ({db.get('connectionType', '?')})",
            ),
            "",
            "## Upstream tables",
            *bullet_list(
                signals.upstream_tables,
                lambda t: t.get("fullName") or t.get("name", "?"),
            ),
            "",
            "## Parameters",
            *bullet_list(signals.parameters, lambda p: p.get("name", "?")),
            "",
            "## All visible fields  (name | typename | dataType | role)",
            *[
                f"{f.name} | {f.typename} | {f.data_type or '-'} | {f.role or '-'}"
                for f in signals.all_fields
            ],
            "",
            "## Calculated field formulas",
        ]

        if signals.calculated_fields:
            for f in signals.calculated_fields:
                lines += [f"### {f.name}", f.formula or "(no formula)", ""]
        else:
            lines.append("(none)")

        lines += [
            "",
            "## Data quality warnings",
            *bullet_list(
                signals.data_quality_warnings,
                lambda w: (
                    f"[{w.get('attached_to_kind')}] "
                    f"{w.get('attached_to_name')}: {w.get('message', '?')}"
                ),
            ),
        ]

        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Stage 3 — Prioritization Engine
# ──────────────────────────────────────────────────────────────────────────────

_DOMAIN_IMPORTANCE: dict[str, int] = {
    "Revenue": 10,
    "Margin": 10,
    "Target": 9,
    "Sales Ops": 8,
    "Customer": 8,
    "Product": 7,
    "Shipping": 7,
    "Returns": 6,
    "Discounting": 6,
}

_PERSONA_DOMAIN_BOOSTS: dict[str, dict[str, int]] = {
    "Executive":          {"Revenue": 5, "Margin": 5, "Target": 4, "Customer": 3},
    "Sales Leader":       {"Target": 5, "Revenue": 4, "Sales Ops": 4, "Customer": 3},
    "Regional Manager":   {"Revenue": 4, "Target": 4, "Customer": 3},
    "Product Manager":    {"Product": 5, "Margin": 4, "Returns": 4},
    "Customer Manager":   {"Customer": 5, "Returns": 4, "Revenue": 3},
    "Operations Manager": {"Shipping": 5, "Returns": 4},
    "Finance":            {"Margin": 5, "Target": 4, "Sales Ops": 5, "Revenue": 3},
}


class PrioritizationEngine:
    def enrich_kpi_catalog(
        self,
        kpi_layers: dict[str, Any],
        dqws: list[dict],
        persona_id: str | None = None,
    ) -> dict[str, Any]:
        dqw_names = {
            (w.get("attached_to_name") or "").lower()
            for w in dqws
        }
        boosts = _PERSONA_DOMAIN_BOOSTS.get(persona_id or "Executive", {})

        def score(kpi: dict) -> float:
            domain = kpi.get("domain", "")
            base = _DOMAIN_IMPORTANCE.get(domain, 5)
            persona_boost = boosts.get(domain, 0)
            dqw_penalty = 2 if any(
                sf.lower() in n
                for sf in kpi.get("source_fields", [])
                for n in dqw_names
            ) else 0
            return base + persona_boost - dqw_penalty

        enriched = dict(kpi_layers)
        for kpi in enriched.get("layer_1_current_state", []):
            kpi["priority_score"] = score(kpi)

        enriched["layer_1_current_state"] = sorted(
            enriched.get("layer_1_current_state", []),
            key=lambda k: k.get("priority_score", 0),
            reverse=True,
        )
        return enriched


# ──────────────────────────────────────────────────────────────────────────────
# Stage 4 — Visualization Selector
# ──────────────────────────────────────────────────────────────────────────────

_VIZ_RULES: list[tuple[list[str], str]] = [
    (["vs target", "attainment", "gap", "variance"],        "bullet_chart"),
    (["forecast", "projection", "predicted"],               "line_chart_with_confidence_band"),
    (["risk", "probability", "likelihood"],                 "risk_gauge"),
    (["region", "state", "city", "geography"],              "choropleth_map"),
    (["mix", "breakdown", "by segment", "by category", "composition"], "stacked_bar_chart"),
    (["top", "bottom", "ranking", "leaderboard"],           "horizontal_bar_chart"),
    (["trend", "over time", "monthly", "quarterly"],        "line_chart"),
    (["scatter", "discount", "correlation"],                "scatter_plot"),
    (["waterfall", "driver", "contribution", "decomposition"], "waterfall_chart"),
    (["pareto", "concentration"],                           "pareto_chart"),
    (["ship", "days to ship", "late", "fulfillment"],       "grouped_bar_chart"),
    (["commission", "compensation", "quota", "ote"],        "table_with_heatmap"),
    (["customer", "per customer", "account"],               "ranked_table"),
    (["ratio", "rate", "margin", "%", "percentage"],        "kpi_card_with_sparkline"),
    (["total", "sum", "count", "revenue", "profit"],        "kpi_card"),
]


def _select_viz(kpi_name: str, domain: str, drill_down: list[str]) -> str:
    combined = (kpi_name + " " + domain).lower()
    for keywords, viz in _VIZ_RULES:
        if any(k in combined for k in keywords):
            return viz
    if any(d.lower() in ("region", "state", "city", "country") for d in drill_down):
        return "choropleth_map"
    return "bar_chart"


class VisualizationSelector:
    def enrich(self, kpi_layers: dict[str, Any]) -> dict[str, Any]:
        for kpi in kpi_layers.get("layer_1_current_state", []):
            kpi["recommended_viz"] = _select_viz(
                kpi.get("kpi", ""),
                kpi.get("domain", ""),
                kpi.get("drill_down", []),
            )
        return kpi_layers


# ──────────────────────────────────────────────────────────────────────────────
# Stage 5 — Drill-Down Graph Builder
# ──────────────────────────────────────────────────────────────────────────────

_STANDARD_HIERARCHIES: dict[str, list[str]] = {
    "geography": ["Region", "State/Province", "City", "Customer Name", "Order ID"],
    "product":   ["Category", "Sub-Category", "Product Name", "Order ID"],
    "time":      ["Year", "Quarter", "Month", "Week", "Order Date"],
    "customer":  ["Segment", "Customer Name", "Order ID"],
    "shipping":  ["Ship Mode", "Region", "State/Province", "Customer Name", "Order ID"],
    "sales_ops": ["Region", "Sales Person", "Customer Name", "Order ID"],
}

_HIERARCHY_SIGNALS: dict[str, list[str]] = {
    "shipping":  ["ship mode", "days to ship", "ship status", "late"],
    "product":   ["category", "sub-category", "product"],
    "time":      ["year", "month", "quarter", "date"],
    "customer":  ["segment", "customer"],
    "sales_ops": ["sales person", "quota", "commission", "ote"],
    "geography": ["region", "state", "city"],
}


def _detect_hierarchy(drill_down: list[str]) -> str:
    combined = " ".join(drill_down).lower()
    for hierarchy, signals in _HIERARCHY_SIGNALS.items():
        if any(s in combined for s in signals):
            return hierarchy
    return "geography"


class DrillDownGraphBuilder:
    def enrich(self, kpi_layers: dict[str, Any]) -> dict[str, Any]:
        for kpi in kpi_layers.get("layer_1_current_state", []):
            existing = kpi.get("drill_down", [])
            hierarchy_type = _detect_hierarchy(existing)
            standard = _STANDARD_HIERARCHIES[hierarchy_type]
            merged = list(existing)
            for step in standard:
                if step not in merged:
                    merged.append(step)
            kpi["drill_down_path"] = merged
            kpi["hierarchy_type"] = hierarchy_type
        return kpi_layers


# ──────────────────────────────────────────────────────────────────────────────
# Stage 6 — Explanation Generator
# ──────────────────────────────────────────────────────────────────────────────

_WHY_SURFACED: dict[str, str] = {
    "Revenue": (
        "Revenue performance directly affects growth objectives. "
        "Changes propagate to margin, target attainment, and commission forecasts."
    ),
    "Margin": (
        "Profitability is under simultaneous pressure from discounting, returns, and product mix. "
        "Margin signals often lag revenue signals — surface them first."
    ),
    "Target": (
        "Target gaps compound: a miss in one period shifts pressure to the next. "
        "Early detection enables intervention before the period closes."
    ),
    "Shipping": (
        "Shipping delays drive customer dissatisfaction and increase return probability. "
        "Operational issues here have downstream effects on customer value and retention."
    ),
    "Customer": (
        "Customer concentration risk is invisible in aggregate revenue views. "
        "A small number of accounts may drive a disproportionate share of sales."
    ),
    "Product": (
        "Product-level profitability hides inside category-level aggregates. "
        "Low-margin products may inflate revenue while dragging overall profit ratio."
    ),
    "Returns": (
        "Returns represent direct revenue leakage and signal product or fulfillment quality "
        "issues that compound over time."
    ),
    "Sales Ops": (
        "Compensation cost and quota attainment determine whether sales investment "
        "generates proportional return."
    ),
    "Discounting": (
        "Discounting is a leading indicator of margin erosion. "
        "Discount behaviour by product and region often predicts future profit ratio decline."
    ),
}

_INTERVENTIONS: dict[str, list[str]] = {
    "Revenue": [
        "Identify underperforming regions or segments and redirect sales effort.",
        "Compare current period vs prior year to detect seasonal vs structural decline.",
    ],
    "Margin": [
        "Review discounting policy on low-margin sub-categories.",
        "Identify products with high return rates that are eroding net margin.",
    ],
    "Target": [
        "Run a 30-day deterministic forecast to assess whether the gap is closeable.",
        "Rebalance sales focus toward high-attainment segments and categories.",
    ],
    "Shipping": [
        "Review ship mode selection for high-value orders in delayed regions.",
        "Identify whether specific ship modes consistently miss SLA thresholds.",
    ],
    "Customer": [
        "Flag accounts with declining sales trends for account manager review.",
        "Assess churn exposure in the bottom quartile of customer sales.",
    ],
    "Product": [
        "Shift promotional investment toward high-margin product lines.",
        "Review return-prone products for quality or description issues.",
    ],
    "Returns": [
        "Investigate return patterns by product category and customer segment.",
        "Correlate return rate with discount level and ship mode.",
    ],
    "Sales Ops": [
        "Compare OTE to actual compensation cost per representative.",
        "Identify reps below quota who may be misaligned with territory allocation.",
    ],
    "Discounting": [
        "Set discount floor thresholds by sub-category based on minimum acceptable margin.",
        "Correlate discount level with order profitability to identify destructive deals.",
    ],
}


class ExplanationGenerator:
    def build_contracts(self, kpi_layers: dict[str, Any]) -> list[dict[str, Any]]:
        contracts = []
        for kpi in kpi_layers.get("layer_1_current_state", []):
            domain = kpi.get("domain", "")
            contracts.append({
                "kpi_id": kpi.get("kpi_id"),
                "kpi": kpi.get("kpi"),
                "domain": domain,
                "priority_score": kpi.get("priority_score"),
                "recommended_viz": kpi.get("recommended_viz"),
                "why_surfaced": _WHY_SURFACED.get(
                    domain,
                    "This metric reflects a core operating objective identified in the workbook.",
                ),
                "driver_decomposition": kpi.get("drill_down", []),
                "drill_down_path": kpi.get("drill_down_path", kpi.get("drill_down", [])),
                "hierarchy_type": kpi.get("hierarchy_type"),
                "recommended_interventions": _INTERVENTIONS.get(domain, []),
                "affected_personas": kpi.get("personas", []),
                "source_fields": kpi.get("source_fields", []),
            })
        return contracts


# ──────────────────────────────────────────────────────────────────────────────
# Stage 7 — Blueprint Assembler
# ──────────────────────────────────────────────────────────────────────────────

class BlueprintAssembler:
    def assemble(
        self,
        signals: InventorySignals,
        semantic: dict[str, Any],
        kpi_layers: dict[str, Any],
        explanation_contracts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": "1.0",
            "source": {
                "workbook_name": signals.workbook_name,
                "workbook_luid": signals.luid,
                "field_count": len(signals.all_fields),
                "calculated_field_count": len(signals.calculated_fields),
                "parameter_count": len(signals.parameters),
                "view_count": len(signals.views),
                "upstream_table_count": len(signals.upstream_tables),
                "dqw_count": len(signals.data_quality_warnings),
            },
            "workbook_summary": semantic.get("workbook_summary", {}),
            "business_objectives": semantic.get("business_objectives", []),
            "personas": semantic.get("personas", []),
            "kpi_layers": kpi_layers,
            "explanation_contracts": explanation_contracts,
            "meta": {
                "prioritization_method": (
                    "rule-based domain importance + persona alignment scoring"
                ),
                "prioritization_formula": (
                    "priority_score = domain_importance + persona_boost - dqw_penalty"
                ),
                "layer_1_sorted_by": "priority_score descending",
                "semantic_model": "claude-sonnet-4-6",
            },
        }


# ──────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────────────────────────

class BlueprintGenerator:
    """Runs all 7 pipeline stages and returns a complete blueprint dict."""

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self._parser = InventoryParser()
        self._analyzer = ClaudeSemanticAnalyzer(model=model)
        self._prioritizer = PrioritizationEngine()
        self._viz = VisualizationSelector()
        self._drill_down = DrillDownGraphBuilder()
        self._explainer = ExplanationGenerator()
        self._assembler = BlueprintAssembler()

    def generate(
        self,
        inventory: dict[str, Any],
        persona_id: str | None = None,
    ) -> dict[str, Any]:
        log.info("stage 1 — parsing inventory")
        signals = self._parser.parse(inventory)
        log.info(
            "parsed %d visible fields (%d calculated), %d views, %d parameters",
            len(signals.all_fields),
            len(signals.calculated_fields),
            len(signals.views),
            len(signals.parameters),
        )

        log.info("stage 2 — Claude semantic analysis")
        semantic = self._analyzer.analyze(signals)

        log.info("stage 3 — prioritization (persona=%s)", persona_id or "Executive")
        kpi_layers = self._prioritizer.enrich_kpi_catalog(
            semantic["kpi_layers"],
            signals.data_quality_warnings,
            persona_id,
        )

        log.info("stage 4 — visualization selection")
        kpi_layers = self._viz.enrich(kpi_layers)

        log.info("stage 5 — drill-down graph enrichment")
        kpi_layers = self._drill_down.enrich(kpi_layers)

        log.info("stage 6 — explanation contracts")
        contracts = self._explainer.build_contracts(kpi_layers)

        log.info("stage 7 — assembly")
        blueprint = self._assembler.assemble(signals, semantic, kpi_layers, contracts)

        log.info(
            "blueprint complete: %d objectives, %d personas, "
            "%d L1 KPIs, %d L2 KPIs, %d L3 KPIs",
            len(blueprint["business_objectives"]),
            len(blueprint["personas"]),
            len(kpi_layers.get("layer_1_current_state", [])),
            len(kpi_layers.get("layer_2_deterministic", [])),
            len(kpi_layers.get("layer_3_predictive", [])),
        )
        return blueprint

    def write_to_json(
        self,
        blueprint: dict[str, Any],
        output_dir: str = "output",
    ) -> str:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        wb_name = (
            blueprint.get("workbook_summary", {}).get("workbook_name")
            or blueprint.get("source", {}).get("workbook_name")
            or "blueprint"
        )
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = out / f"blueprint_{wb_name}_{ts}.json"
        path.write_text(json.dumps(blueprint, indent=2, default=str), encoding="utf-8")
        log.info("blueprint written to %s", path)
        return str(path)
