"""
pipeline/hyper_extractor.py
────────────────────────────
Extracts rich schema + sample data from a Tableau workbook's embedded
datasource using the Hyper API.

Flow
────
1. Download the .twbx from Tableau Server (REST API)
2. Unzip → find the .hyper extract file
3. Read every table: columns (name + type), row count, sample rows
4. Parse the .twb XML → extract calculated field formulas
5. Return a HyperSchema that the profiler merges with view-level data

Nothing here is domain-specific. Works for any .twbx with an extract.

Falls back gracefully if:
  - tableauhyperapi is not installed
  - The workbook has no .hyper file (live connection)
  - Download fails (permissions)
"""

from __future__ import annotations

import logging
import os
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class HyperColumn:
    name:      str
    data_type: str            # INT, TEXT, DOUBLE, DATE, TIMESTAMP, BOOL, etc.
    nullable:  bool = True


@dataclass
class HyperTable:
    schema:      str
    table_name:  str
    row_count:   int
    columns:     list[HyperColumn]    = field(default_factory=list)
    sample_rows: list[dict[str, Any]] = field(default_factory=list)  # up to 5 rows
    full_rows:   list[dict[str, Any]] = field(default_factory=list)  # up to max_full_rows


@dataclass
class CalcField:
    name:     str             # display name in Tableau
    formula:  str             # Tableau formula string
    data_type: str = "unknown"


@dataclass
class HyperSchema:
    """Complete schema extracted from a .twbx workbook."""
    workbook_name:   str
    tables:          list[HyperTable]  = field(default_factory=list)
    calc_fields:     list[CalcField]   = field(default_factory=list)

    @property
    def total_columns(self) -> int:
        return sum(len(t.columns) for t in self.tables)

    @property
    def total_rows(self) -> int:
        return sum(t.row_count for t in self.tables)

    def as_profiler_views(self) -> dict[str, list[dict]]:
        """
        Convert HyperSchema into the same {view_name: [rows]} format the
        profiler and domain agents already understand.
        Each table becomes a synthetic view with its FULL row data (up to max_full_rows)
        so domain agents can compute real KPI values from raw columns.
        """
        result: dict[str, list[dict]] = {}
        for table in self.tables:
            key = f"[TABLE] {table.table_name}"
            # Use full_rows so agents can aggregate properly;
            # fall back to sample_rows if full data wasn't loaded
            result[key] = table.full_rows if table.full_rows else table.sample_rows
        return result

    def table_view_names(self) -> list[str]:
        """Return synthetic view names for all tables — for available_views list."""
        return [f"[TABLE] {t.table_name}" for t in self.tables]

    # ── Field resolver ────────────────────────────────────────────────────────

    @staticmethod
    def _norm(name: str) -> str:
        """Normalize field name for fuzzy matching."""
        import re
        # Strip Tableau cruft: table qualifiers like "(Demo Bed Utilization Hourly)"
        name = re.sub(r'\s*\([^)]*\)', '', name)
        return re.sub(r'[^a-z0-9]', '', name.lower())

    def _find_hyper_col(self, field_name: str) -> tuple[str, str] | None:
        """
        Find (table_name, column_name) in Hyper tables that best matches field_name.
        Returns None if no match found.
        """
        norm_field = self._norm(field_name)
        best_table:  str | None = None
        best_col:    str | None = None
        best_score:  int = 0

        for table in self.tables:
            for col in table.columns:
                norm_col = self._norm(col.name)
                # Exact normalized match
                if norm_col == norm_field:
                    return table.table_name, col.name
                # Partial match scoring
                score = 0
                if norm_field in norm_col or norm_col in norm_field:
                    score = min(len(norm_field), len(norm_col))
                if score > best_score:
                    best_score = score
                    best_table = table.table_name
                    best_col   = col.name

        if best_score >= 4 and best_table and best_col:   # min 4 chars overlap
            return best_table, best_col
        return None

    def _translate_formula(self, formula: str) -> tuple[str, list[str]] | None:
        """
        Translate a Tableau formula to a Python/pandas expression.
        Returns (python_expr, [referenced_fields]) or None if too complex.

        Handles: simple arithmetic, AVG/SUM/IF wrappers, field references.
        """
        import re

        # Extract all Tableau field references [Field Name]
        refs = re.findall(r'\[([^\]]+)\]', formula)
        if not refs:
            return None

        # Skip complex formulas (LOD, window functions, nested IF)
        skip_patterns = [r'\{', r'WINDOW_', r'LOOKUP', r'PREVIOUS_VALUE',
                         r'RUNNING_', r'INDEX\(', r'RANK\(']
        for pat in skip_patterns:
            if re.search(pat, formula, re.IGNORECASE):
                return None

        # Translate each [Field Name] → df['hyper_col']
        expr = formula.strip()
        field_map: dict[str, str] = {}

        for ref in refs:
            match = self._find_hyper_col(ref)
            if not match:
                return None   # can't resolve all fields → skip
            _, hyper_col = match
            field_map[ref] = hyper_col
            expr = expr.replace(f"[{ref}]", f"df['{hyper_col}']")

        # Remove aggregation wrappers — per-row computation
        expr = re.sub(r'\bAVG\s*\(', '(', expr, flags=re.IGNORECASE)
        expr = re.sub(r'\bSUM\s*\(', '(', expr, flags=re.IGNORECASE)
        expr = re.sub(r'\bMIN\s*\(', '(', expr, flags=re.IGNORECASE)
        expr = re.sub(r'\bMAX\s*\(', '(', expr, flags=re.IGNORECASE)

        # Simple IF → replace with np.where (skip complex nested IF)
        if re.search(r'\bIF\b', expr, re.IGNORECASE):
            return None   # too complex for simple translation

        return expr.strip(), list(field_map.values())

    def build_field_resolver(self) -> dict[str, dict]:
        """
        Build a complete mapping: field_name → {source, table, column, formula}

        For direct columns:
          "occupancy_percent" → {source: "direct", table: "demo_bed_utilization_hourly",
                                  column: "occupancy_percent"}

        For calculated fields:
          "Occupancy %" → {source: "formula", table: "demo_bed_utilization_hourly",
                            column: "Occupancy %",
                            formula: "df['occupied_beds'] / df['staffed_beds'] * 100",
                            refs: ["occupied_beds", "staffed_beds"]}
        """
        resolver: dict[str, dict] = {}

        # 1. Direct columns — every raw column in every Hyper table
        for table in self.tables:
            for col in table.columns:
                resolver[col.name] = {
                    "source": "direct",
                    "table":  f"[TABLE] {table.table_name}",
                    "column": col.name,
                    "dtype":  col.data_type,
                }

        # 2. Calculated fields — translate Tableau formulas to Python
        for cf in self.calc_fields:
            result = self._translate_formula(cf.formula)
            if result:
                py_expr, refs = result
                # Determine primary table from first referenced column
                primary = None
                for ref_col in refs:
                    for table in self.tables:
                        if any(c.name == ref_col for c in table.columns):
                            primary = f"[TABLE] {table.table_name}"
                            break
                    if primary:
                        break

                resolver[cf.name] = {
                    "source":  "formula",
                    "table":   primary or "unknown",
                    "column":  cf.name,
                    "formula": py_expr,
                    "refs":    refs,
                    "dtype":   cf.data_type,
                }
            else:
                # Formula too complex to translate — note it as Tableau-only
                resolver[cf.name] = {
                    "source":  "tableau_only",
                    "formula": cf.formula[:100],
                    "note":    "Complex formula — fetch from Tableau view",
                }

        return resolver

    def field_resolver_text(self) -> str:
        """
        Compact text for the orchestrator showing how to get every field from Hyper.
        """
        resolver = self.build_field_resolver()
        lines = [
            "",
            "=== FIELD RESOLVER — how to get every field from Hyper ===",
            "For each KPI field, fetch from [TABLE] source and compute if needed.",
            "",
        ]
        for field_name, info in resolver.items():
            src = info["source"]
            if src == "direct":
                lines.append(
                    f"  {field_name:<40} DIRECT  {info['table']}  col={info['column']}"
                )
            elif src == "formula":
                lines.append(
                    f"  {field_name:<40} FORMULA {info['table']}"
                )
                lines.append(
                    f"    → {info['formula'][:80]}"
                )
            # skip tableau_only — too complex, agent uses view
        return "\n".join(lines)

    def summary_text(self) -> str:
        """Compact text block for the orchestrator system prompt."""
        lines = [
            "=== HYPER EXTRACT — PRIMARY DATA SOURCE ===",
            "IMPORTANT: These raw tables are the PRIMARY source for ALL KPI data.",
            "Always use [TABLE] views over Tableau views for data computation.",
            "Tableau views are for reference/business logic only.",
            f"  Tables  : {len(self.tables)}",
            f"  Columns : {self.total_columns} raw",
            f"  Rows    : {self.total_rows:,} total across all tables",
            "",
        ]
        for t in self.tables:
            lines.append(f"  [TABLE] {t.table_name}  ({t.row_count:,} rows)")
            for c in t.columns:
                lines.append(f"    {c.name:<40} {c.data_type}")
            if t.sample_rows:
                lines.append(f"    sample row: {t.sample_rows[0]}")
            lines.append("")

        if self.calc_fields:
            lines += [
                f"Derived metrics — compute these from raw columns above:",
                f"(Use run_analysis to compute these in domain agents)",
            ]
            for cf in self.calc_fields:
                formula_short = cf.formula[:100].replace("\n", " ")
                lines.append(f"  {cf.name:<35} = {formula_short}")

        return "\n".join(lines)


# ── Hyper reader ───────────────────────────────────────────────────────────────

def _read_hyper(hyper_path: str, sample_rows: int = 5, max_full_rows: int = 0) -> list[HyperTable]:
    """Read all tables from a .hyper file. Returns [] if tableauhyperapi missing."""
    try:
        from tableauhyperapi import HyperProcess, Connection, Telemetry
    except ImportError:
        log.warning("tableauhyperapi not installed — skipping Hyper extraction. "
                    "Run: pip install tableauhyperapi")
        return []

    tables: list[HyperTable] = []

    with HyperProcess(telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as hyper:
        with Connection(hyper.endpoint, hyper_path) as conn:
            for schema_name in conn.catalog.get_schema_names():
                schema_str = str(schema_name)
                if schema_str == "public":
                    continue   # skip empty public schema
                for table_name in conn.catalog.get_table_names(schema_name):
                    try:
                        td        = conn.catalog.get_table_definition(table_name)
                        row_count = conn.execute_scalar_query(
                            f"SELECT COUNT(*) FROM {table_name}"
                        )

                        cols = [
                            HyperColumn(
                                name=str(c.name).strip('"'),
                                data_type=str(c.type).upper(),
                                nullable=c.nullability.value != 0,
                            )
                            for c in td.columns
                        ]

                        # Sample rows (5) + full rows (capped at max_full_rows)
                        col_names  = [c.name for c in cols]
                        col_exprs  = ", ".join(f"{table_name}.{c.name}" for c in td.columns)

                        def _coerce(val: Any) -> Any:
                            """Convert Hyper-specific types to JSON-safe primitives."""
                            if val is None:
                                return None
                            t = type(val).__name__
                            # tableauhyperapi Date / Timestamp → ISO strings
                            if t == "Date":
                                return f"{val.year:04d}-{val.month:02d}-{val.day:02d}"
                            if t == "Timestamp":
                                return (f"{val.year:04d}-{val.month:02d}-{val.day:02d}"
                                        f"T{val.hour:02d}:{val.minute:02d}:{val.second:02d}")
                            if t == "Interval":
                                return str(val)
                            return val

                        def _rows_to_dicts(raw: list) -> list[dict]:
                            return [
                                {col_names[i]: _coerce(row[i])
                                 for i in range(len(col_names))}
                                for row in raw
                            ]

                        raw_samples = conn.execute_list_query(
                            f"SELECT {col_exprs} FROM {table_name} LIMIT {sample_rows}"
                        )
                        samples = _rows_to_dicts(raw_samples)

                        # Full data — no cap, pandas sandbox handles any size
                        full_sql  = f"SELECT {col_exprs} FROM {table_name}"
                        if max_full_rows > 0:
                            full_sql += f" LIMIT {max_full_rows}"
                        raw_full  = conn.execute_list_query(full_sql)
                        full_data = _rows_to_dicts(raw_full)

                        # Clean table name (strip hash suffix)
                        display_name = str(table_name).strip('"').split(".")[-1].strip('"')
                        # Remove hash suffix like _BB3F763CC26F4F9E...
                        import re
                        display_name = re.sub(r'_[A-F0-9]{16,}$', '', display_name)

                        tables.append(HyperTable(
                            schema=schema_str,
                            table_name=display_name,
                            row_count=int(row_count),
                            columns=cols,
                            sample_rows=samples,
                            full_rows=full_data,
                        ))

                    except Exception as exc:
                        log.debug("Skipping table %s: %s", table_name, exc)

    return tables


# ── TWB XML parser ─────────────────────────────────────────────────────────────

def _parse_calc_fields(twb_content: str) -> list[CalcField]:
    """Extract calculated field formulas from the .twb XML."""
    calcs: list[CalcField] = []
    try:
        root = ET.fromstring(twb_content)
        for col in root.iter("column"):
            formula = col.get("formula") or col.find("calculation/[@formula]") and \
                      col.find("calculation").get("formula") if col.find("calculation") is not None else None
            if not formula:
                calc_el = col.find("calculation")
                if calc_el is not None:
                    formula = calc_el.get("formula", "")
            if formula:
                name = col.get("caption") or col.get("name", "unknown")
                name = name.lstrip("[").rstrip("]")
                dtype = col.get("datatype", "unknown")
                calcs.append(CalcField(name=name, formula=formula.strip(), data_type=dtype))
    except Exception as exc:
        log.debug("TWB calc field parse error: %s", exc)
    return calcs


# ── Main entry point ───────────────────────────────────────────────────────────

def extract_from_workbook(
    workbook_luid:  str,
    workbook_name:  str,
    tableau_server,                    # tableauserverclient Server instance (signed in)
    sample_rows:    int = 5,
) -> Optional[HyperSchema]:
    """
    Download the .twbx, extract the .hyper file, build and return a HyperSchema.
    Returns None if the workbook has no extract or if download fails.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            # 1. Download workbook
            dl_path = tableau_server.workbooks.download(
                workbook_luid,
                filepath=os.path.join(tmpdir, "workbook"),
                include_extract=True,
            )
            log.info("Downloaded workbook to %s", dl_path)
        except Exception as exc:
            log.warning("Hyper: workbook download failed for '%s': %s", workbook_name, exc)
            return None

        try:
            # 2. Unzip .twbx
            with zipfile.ZipFile(dl_path, "r") as z:
                names = z.namelist()

                # Find .hyper file
                hyper_files = [n for n in names if n.endswith(".hyper")]
                twb_files   = [n for n in names if n.endswith(".twb")]

                if not hyper_files:
                    log.info("Hyper: no .hyper extract in '%s' — live connection only", workbook_name)
                    return None

                # Extract both
                for fn in hyper_files + twb_files:
                    z.extract(fn, tmpdir)

                hyper_path = os.path.join(tmpdir, hyper_files[0])
                twb_path   = os.path.join(tmpdir, twb_files[0]) if twb_files else None

        except Exception as exc:
            log.warning("Hyper: unzip failed for '%s': %s", workbook_name, exc)
            return None

        # 3. Read .hyper tables
        log.info("Hyper: reading extract %s", hyper_files[0])
        tables = _read_hyper(hyper_path, sample_rows=sample_rows)
        if not tables:
            log.warning("Hyper: no tables read from extract")
            return None

        # 4. Parse calculated fields from .twb
        calc_fields: list[CalcField] = []
        if twb_path and os.path.exists(twb_path):
            try:
                twb_content = Path(twb_path).read_text(encoding="utf-8", errors="ignore")
                calc_fields = _parse_calc_fields(twb_content)
                log.info("Hyper: found %d calculated fields in TWB", len(calc_fields))
            except Exception as exc:
                log.debug("Hyper: TWB parse error: %s", exc)

        schema = HyperSchema(
            workbook_name=workbook_name,
            tables=tables,
            calc_fields=calc_fields,
        )
        log.info(
            "Hyper: extracted %d tables, %d raw columns, %s rows, %d calc fields",
            len(tables), schema.total_columns, f"{schema.total_rows:,}", len(calc_fields),
        )
        return schema
