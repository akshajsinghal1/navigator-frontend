"""
pipeline/profiler.py
────────────────────
Generic, deterministic data-profiling layer for ANY Tableau workbook.

Design principle
────────────────
Separate STRUCTURAL TRUTH (computed here, deterministically) from DOMAIN MEANING
(added later by the LLM agents). Nothing in this module is hardcoded to an
industry, schema, workbook, or use case. Every decision is statistical or
structural:

  • column typing            → parse-ratio tests
  • dimension vs measure     → cardinality + numeric ratio
  • entity resolution        → cross-view value-set clustering (Jaccard)
  • label normalization      → string similarity (no dictionaries)
  • relationship discovery   → combinatorial arithmetic (A+B≈C, A/B≈pct)
  • degenerate breakdowns    → within-group variance
  • suspicious uniformity    → coefficient of variation
  • quality flags            → null rate, constants, single-row, outliers

Input  : {view_name: [row dicts]}   (raw CSV rows, all strings is fine)
Output : WorkbookProfile (JSON-serializable) consumed by the orchestrator.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field, asdict
from difflib import SequenceMatcher
from typing import Any, Optional

# ── Tunables (all generic, not domain) ──────────────────────────────────────────
NUMERIC_RATIO_MIN   = 0.80   # ≥80% of values parse as numbers → numeric column
MEASURE_CARD_MIN    = 3      # numeric col needs ≥3 distinct values to be a measure
ENTITY_JACCARD_MIN  = 0.50   # value-set overlap to call two dim columns the same entity
ENTITY_CONTAIN_MIN  = 0.80   # or one value-set ⊆ the other by this much
LABEL_SIM_MIN       = 0.84   # string similarity to merge two labels within an entity
REL_TOLERANCE       = 0.02   # 2% tolerance for arithmetic relationship discovery
UNIFORM_CV_MAX      = 0.10   # category totals with CV below this = suspiciously uniform
HIGH_NULL_RATE      = 0.30   # >30% null → quality flag
OUTLIER_Z           = 4.0


# ── parsing helpers (generic) ───────────────────────────────────────────────────

def parse_num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s in ("", "N/A", "n/a", "null", "None", "-"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    s = re.sub(r"[,$€£¥₹₩%\s]", "", s)
    # scale suffixes
    mult = 1.0
    m = re.match(r"^(-?\d*\.?\d+)([KkMmBbTt])$", s)
    if m:
        s = m.group(1)
        mult = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}[m.group(2).lower()]
    try:
        n = float(s) * mult
        return -n if neg else n
    except ValueError:
        return None


_DATE_PATTERNS = [
    r"^\d{4}-\d{1,2}-\d{1,2}",                       # ISO
    r"^\d{1,2}/\d{1,2}/\d{2,4}$",                    # m/d/y
    r"^[A-Z][a-z]+ \d{1,2}, \d{4}$",                 # April 1, 2026
    r"^[A-Z][a-z]+ \d{4}$",                          # April 2026
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)",
    r"^(January|February|March|April|May|June|July|August|September|October|November|December)$",
    r"^Q[1-4]",                                      # quarters
]

def looks_temporal(values: list[str]) -> bool:
    if not values:
        return False
    hits = 0
    for v in values[:50]:
        s = str(v).strip()
        if any(re.match(p, s) for p in _DATE_PATTERNS):
            hits += 1
    return hits / min(len(values), 50) >= 0.6


def norm_label(s: Any) -> str:
    """Normalize a value/column for fuzzy comparison (lowercase, alnum-spaced)."""
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


# Universal calendar vocabulary (NOT industry-specific) — used to spot date-part columns
_TIME_WORDS = {"day", "days", "week", "weeks", "month", "months", "year", "years",
               "quarter", "quarters", "hour", "hours", "date", "dates", "minute", "period"}
# Generic analytics vocabulary for rate-like measures (summing these is meaningless)
_RATE_WORDS = {"rate", "ratio", "pct", "percent", "percentage", "share", "utilization"}

def name_is_temporal(name: str, values: list[str]) -> bool:
    """Tableau date-part convention ('Day of X', 'Month of Y') or bare date values."""
    norm = norm_label(name)
    first = norm.split()[0] if norm.split() else ""
    if first in _TIME_WORDS and (" of " in norm or looks_temporal(values)):
        return True
    return False

def name_is_identifier(name: str) -> bool:
    norm = norm_label(name)
    toks = norm.split()
    return bool(toks) and toks[-1] in {"id", "ids", "code", "codes", "key", "uuid", "guid"}

def name_is_rate(name: str) -> bool:
    norm = norm_label(name)
    return "%" in str(name) or bool(set(norm.split()) & _RATE_WORDS)


# Generic connector stopwords (language-level, not domain) — removed before label matching
_STOPWORDS = {"and", "the", "of", "a", "an", "for", "to", "in", "&", "st", "de", "la", "el"}

def _label_tokens(s: str) -> list[str]:
    return [t for t in norm_label(s).split() if t not in _STOPWORDS]

def labels_match(a: str, b: str) -> bool:
    """
    CONSERVATIVE: only merge two labels when one is clearly a variant of the other.
    Merges:  'Speech' ⊂ 'Speech Therapy';  'Occ Therapy' ~ 'Occupational Therapy'
             (abbreviation-aligned);  'Cardiolgy' ~ 'Cardiology' (single-token typo).
    Does NOT merge two distinct multi-word names that merely share connector words
    (e.g. 'Bosnia and Herzegovina' vs 'St. Vincent and the Grenadines').
    """
    ta, tb = _label_tokens(a), _label_tokens(b)
    if not ta or not tb:
        return False
    sa, sb = set(ta), set(tb)
    if sa == sb:
        return True
    # subset: one is the other plus qualifier words  (Speech ⊂ Speech Therapy)
    if sa <= sb or sb <= sa:
        return True
    # abbreviation alignment: same token count, each token prefix-matches a partner
    if len(ta) == len(tb):
        long, short = (ta, tb) if len(ta) >= len(tb) else (tb, ta)
        used, ok = set(), True
        for stk in short:
            cand = next((i for i, lt in enumerate(long)
                         if i not in used and (lt.startswith(stk) or stk.startswith(lt))), None)
            if cand is None:
                ok = False; break
            used.add(cand)
        if ok:
            return True
    # single-token near-identical (typo) — both must be one distinctive token
    if len(ta) == 1 and len(tb) == 1 and SequenceMatcher(None, ta[0], tb[0]).ratio() >= 0.90:
        return True
    return False


# ── data classes ─────────────────────────────────────────────────────────────────

@dataclass
class ColumnProfile:
    name: str
    view: str
    dtype: str                    # "numeric" | "temporal" | "boolean" | "categorical"
    role: str                     # "measure" | "dimension"
    rows: int
    nonnull: int
    null_rate: float
    distinct: int
    # measure stats
    min: Optional[float] = None
    max: Optional[float] = None
    mean: Optional[float] = None
    std: Optional[float] = None
    total: Optional[float] = None
    cv: Optional[float] = None     # coefficient of variation
    is_rate: bool = False          # percentage/ratio measure — not summable
    # dimension info
    sample_values: list[str] = field(default_factory=list)
    constant: bool = False


@dataclass
class Entity:
    name: str                                 # canonical entity name (most common col name)
    columns: list[str]                        # "view::col" members
    canonical_values: list[str]               # normalized, deduped value roster
    raw_value_count: int                      # before normalization
    aliases: dict[str, str] = field(default_factory=dict)   # raw label -> canonical


@dataclass
class Relationship:
    kind: str                                 # "sum" | "ratio"
    expr: str                                 # human-readable, e.g. "Occupied + Available ≈ Staffed"
    confidence: float


@dataclass
class QualityFlag:
    severity: str                             # "warn" | "info"
    code: str
    where: str
    message: str


@dataclass
class WorkbookProfile:
    total_views: int
    data_views: int
    views: dict[str, dict] = field(default_factory=dict)
    columns: list[ColumnProfile] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    flags: list[QualityFlag] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_views": self.total_views,
            "data_views": self.data_views,
            "views": self.views,
            "columns": [asdict(c) for c in self.columns],
            "entities": [asdict(e) for e in self.entities],
            "relationships": [asdict(r) for r in self.relationships],
            "flags": [asdict(f) for f in self.flags],
        }


# ── column profiling ─────────────────────────────────────────────────────────────

def profile_column(view: str, name: str, values: list[Any]) -> ColumnProfile:
    nonnull = [v for v in values if v not in (None, "", "null", "N/A", "n/a")]
    distinct_vals = list(dict.fromkeys(str(v) for v in nonnull))  # preserve order, unique
    nums = [parse_num(v) for v in nonnull]
    valid_nums = [n for n in nums if n is not None]
    numeric_ratio = len(valid_nums) / max(1, len(nonnull))
    null_rate = 1 - (len(nonnull) / max(1, len(values)))

    cp = ColumnProfile(
        name=name, view=view,
        dtype="categorical", role="dimension",
        rows=len(values), nonnull=len(nonnull), null_rate=round(null_rate, 3),
        distinct=len(distinct_vals),
        constant=(len(distinct_vals) <= 1),
    )

    is_bool = set(norm_label(x) for x in distinct_vals) <= {"yes", "no", "true", "false", "0", "1", "y", "n"}
    temporal = looks_temporal(distinct_vals) or name_is_temporal(name, distinct_vals)
    is_id    = name_is_identifier(name)

    if temporal:
        # date-part columns ("Day of X" = 1..31) are an ordinal/time axis, NOT a measure
        cp.dtype = "temporal"; cp.role = "dimension"
        cp.sample_values = distinct_vals[:25]
    elif is_bool and len(distinct_vals) <= 2:
        cp.dtype = "boolean"; cp.role = "dimension"
        cp.sample_values = distinct_vals
    elif is_id:
        cp.dtype = "categorical"; cp.role = "dimension"
        cp.sample_values = distinct_vals[:25]
    elif numeric_ratio >= NUMERIC_RATIO_MIN and valid_nums:
        # A numeric column is a measure — including single-value scalar KPI views
        # (1 distinct value). Cardinality is NOT required; scalars are real measures.
        cp.dtype = "numeric"; cp.role = "measure"
        cp.min = round(min(valid_nums), 4); cp.max = round(max(valid_nums), 4)
        cp.mean = round(statistics.mean(valid_nums), 4)
        cp.std = round(statistics.pstdev(valid_nums), 4) if len(valid_nums) > 1 else 0.0
        cp.total = round(sum(valid_nums), 2)
        cp.cv = round(cp.std / abs(cp.mean), 4) if cp.mean else None
        cp.is_rate = name_is_rate(name) and (cp.max is None or abs(cp.max) <= 100)
    else:
        cp.dtype = "categorical"; cp.role = "dimension"
        cp.sample_values = distinct_vals[:25]

    return cp


# ── entity resolution (cross-view) ───────────────────────────────────────────────

def _value_set(rows: list[dict], col: str) -> set[str]:
    return {norm_label(r[col]) for r in rows if r.get(col) not in (None, "", "null")}


def resolve_entities(views: dict[str, list[dict]], columns: list[ColumnProfile]) -> list[Entity]:
    # candidate dimension columns (categorical/boolean, low-ish cardinality, not temporal)
    dims = [c for c in columns if c.role == "dimension" and c.dtype in ("categorical", "boolean")
            and 1 < c.distinct <= 200]
    # build value sets
    vsets: dict[str, set[str]] = {}
    raw_values: dict[str, list[str]] = {}
    for c in dims:
        key = f"{c.view}::{c.name}"
        vsets[key] = _value_set(views[c.view], c.name)
        raw_values[key] = list(dict.fromkeys(str(r[c.name]) for r in views[c.view]
                                             if r.get(c.name) not in (None, "", "null")))

    keys = [k for k in vsets if vsets[k]]
    # union-find clustering by Jaccard / containment
    parent = {k: k for k in keys}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b):
        parent[find(a)] = find(b)

    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = vsets[keys[i]], vsets[keys[j]]
            if not a or not b:
                continue
            inter = len(a & b)
            jac = inter / len(a | b)
            contain = inter / min(len(a), len(b))
            if jac >= ENTITY_JACCARD_MIN or contain >= ENTITY_CONTAIN_MIN:
                union(keys[i], keys[j])

    clusters: dict[str, list[str]] = {}
    for k in keys:
        clusters.setdefault(find(k), []).append(k)

    entities: list[Entity] = []
    for members in clusters.values():
        if len(members) < 2:
            continue  # entity = a dimension shared across ≥2 views
        # canonical name = most frequent column name among members
        names = [m.split("::", 1)[1] for m in members]
        canon_name = max(set(names), key=names.count)
        # collect raw values PER column, normalize near-duplicates across columns only
        per_col_values = [raw_values[m] for m in members]
        all_raw = [v for lst in per_col_values for v in lst]
        canon_values, aliases = _normalize_labels(per_col_values)
        entities.append(Entity(
            name=canon_name, columns=sorted(members),
            canonical_values=sorted(canon_values),
            raw_value_count=len(set(all_raw)),
            aliases=aliases,
        ))
    return entities


def _normalize_labels(per_col_values: list[list[str]]) -> tuple[list[str], dict[str, str]]:
    """
    Cluster near-duplicate labels ACROSS columns only. Two values that co-occur as
    DISTINCT values in the SAME column are axiomatically different entities (a column
    never lists one entity under two names) — so they are never merged. This is what
    keeps 'Niger'/'Nigeria' and 'Guinea'/'Equatorial Guinea' separate while still
    merging 'Occ Therapy'/'Occupational Therapy' (which live in different views).
    """
    all_vals = [v for lst in per_col_values for v in lst]
    uniq = list(dict.fromkeys(all_vals))
    freq = {u: all_vals.count(u) for u in uniq}
    col_sets = [set(lst) for lst in per_col_values]
    def cooccur(a: str, b: str) -> bool:
        return any(a in s and b in s for s in col_sets)
    parent = {u: u for u in uniq}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for i in range(len(uniq)):
        for j in range(i + 1, len(uniq)):
            if labels_match(uniq[i], uniq[j]) and not cooccur(uniq[i], uniq[j]):
                parent[find(uniq[i])] = find(uniq[j])
    groups: dict[str, list[str]] = {}
    for u in uniq:
        groups.setdefault(find(u), []).append(u)
    canon, aliases = [], {}
    for members in groups.values():
        # canonical = most frequent, tie-break longest (assume longest = least abbreviated)
        best = sorted(members, key=lambda m: (freq[m], len(m)), reverse=True)[0]
        canon.append(best)
        for m in members:
            if m != best:
                aliases[m] = best
    return canon, aliases


# ── relationship discovery (measures) ────────────────────────────────────────────

def discover_relationships(views: dict[str, list[dict]], columns: list[ColumnProfile]) -> list[Relationship]:
    """
    Two tiers, both guarded against coincidence:
      1. WITHIN-VIEW, ROW-VALIDATED  — A+B≈C must hold on (almost) every row → real identity.
      2. CROSS-SCALAR (single-row views) — only with a TIGHT tolerance; marked low-confidence
         candidates, because a single data point can coincide.
    """
    rels: list[Relationship] = []
    col_by_view: dict[str, list[ColumnProfile]] = {}
    for c in columns:
        col_by_view.setdefault(c.view, []).append(c)

    # ── Tier 1: within-view row-validated sums ──────────────────────────────────
    for v, rows in views.items():
        meas = [c for c in col_by_view.get(v, []) if c.role == "measure" and not c.is_rate]
        names = [c.name for c in meas]
        if len(names) < 3 or len(rows) < 3:
            continue
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                for k in range(len(names)):
                    if k in (i, j):
                        continue
                    A, B, C = names[i], names[j], names[k]
                    ok = tot = 0
                    for r in rows:
                        a, b, c = parse_num(r.get(A)), parse_num(r.get(B)), parse_num(r.get(C))
                        if None in (a, b, c) or c == 0:
                            continue
                        tot += 1
                        # both addends must contribute meaningfully (guards "big + negligible = big",
                        # e.g. Population + Latitude ≈ Population)
                        if abs(a) < 0.01 * abs(c) or abs(b) < 0.01 * abs(c):
                            continue
                        if abs((a + b) - c) / abs(c) <= REL_TOLERANCE:
                            ok += 1
                    if tot >= 3 and ok / tot >= 0.95:
                        rels.append(Relationship("sum",
                            f"{A} + {B} = {C}  [holds on {ok}/{tot} rows of '{v}']", 0.97))

    # ── Tier 2: cross-scalar sums (tight tolerance, candidate) ──────────────────
    TIGHT = 0.005
    scal = {c.name: c.total for c in columns
            if c.role == "measure" and not c.is_rate and c.rows == 1 and c.total is not None}
    sitems = list(scal.items())
    for i in range(len(sitems)):
        for j in range(i + 1, len(sitems)):
            for k in range(len(sitems)):
                if k in (i, j):
                    continue
                a, b, c = sitems[i][1], sitems[j][1], sitems[k][1]
                if c == 0:
                    continue
                trio = [abs(a), abs(b), abs(c)]
                if min(trio) > 0 and max(trio) / min(trio) > 50:
                    continue
                if abs((a + b) - c) / abs(c) <= TIGHT:
                    rels.append(Relationship("sum_candidate",
                        f"{sitems[i][0]} + {sitems[j][0]} ~= {sitems[k][0]}  "
                        f"({a:g}+{b:g}~={c:g}) [scalar candidate — verify]", 0.55))

    # ── Ratio: rate ≈ countA / countB (within-view row-validated where possible) ─
    for v, rows in views.items():
        cps = col_by_view.get(v, [])
        rates = [c for c in cps if c.role == "measure" and c.is_rate]
        counts = [c for c in cps if c.role == "measure" and not c.is_rate]
        for rt in rates:
            for ca in counts:
                for cb in counts:
                    if ca.name == cb.name:
                        continue
                    ok = tot = 0
                    for r in rows:
                        p, x, y = parse_num(r.get(rt.name)), parse_num(r.get(ca.name)), parse_num(r.get(cb.name))
                        if None in (p, x, y) or y == 0:
                            continue
                        tot += 1
                        scale = 100 if p > 1.5 else 1
                        if abs((scale * x / y) - p) / max(abs(p), 1e-6) <= REL_TOLERANCE:
                            ok += 1
                    if tot >= 3 and ok / tot >= 0.95:
                        rels.append(Relationship("ratio",
                            f"{rt.name} = {ca.name} / {cb.name}  [holds on {ok}/{tot} rows of '{v}']", 0.9))

    seen, out = set(), []
    for r in rels:
        if r.expr not in seen:
            seen.add(r.expr); out.append(r)
    return out


# ── quality flags ─────────────────────────────────────────────────────────────────

def detect_flags(views: dict[str, list[dict]], columns: list[ColumnProfile],
                 entities: list[Entity], relationships: list[Relationship]) -> list[QualityFlag]:
    flags: list[QualityFlag] = []
    col_by_view: dict[str, list[ColumnProfile]] = {}
    for c in columns:
        col_by_view.setdefault(c.view, []).append(c)

    # constant columns / single-row views / high null
    for c in columns:
        if c.constant and c.rows > 1:
            flags.append(QualityFlag("info", "constant_column", f"{c.view}::{c.name}",
                f"Column '{c.name}' is constant ('{c.sample_values[:1]}') — no information."))
        if c.null_rate > HIGH_NULL_RATE:
            flags.append(QualityFlag("warn", "high_null", f"{c.view}::{c.name}",
                f"Column '{c.name}' is {c.null_rate*100:.0f}% null."))
    for v, rows in views.items():
        if len(rows) == 1:
            flags.append(QualityFlag("info", "single_row_view", v,
                f"View '{v}' has a single row — it is a scalar KPI, not a series. Use kpi_card/gauge."))

    # degenerate breakdown: 2-dim view where measure is constant across one dim within the other
    for v, rows in views.items():
        cps = col_by_view.get(v, [])
        dims = [c for c in cps if c.role == "dimension" and c.dtype in ("categorical", "boolean")]
        meas = [c for c in cps if c.role == "measure"]
        if len(dims) == 2 and meas:
            d1, d2 = dims[0].name, dims[1].name
            m = meas[0].name
            for da, db in [(d1, d2), (d2, d1)]:
                # group by da; within each group, does m vary across db?
                groups: dict[str, list[float]] = {}
                for r in rows:
                    val = parse_num(r.get(m))
                    if val is None:
                        continue
                    groups.setdefault(str(r.get(da)), []).append(val)
                variances = [statistics.pstdev(g) for g in groups.values() if len(g) > 1]
                if variances and max(variances) < 1e-6:
                    flags.append(QualityFlag("warn", "degenerate_breakdown", f"{v}",
                        f"In '{v}', measure '{m}' does NOT vary across '{db}' (constant within each '{da}'). "
                        f"The '{db}' breakdown is non-informative — do not chart {m} by {db}."))
                    break

    # suspiciously uniform categorical distribution.
    # Use a COUNT-like measure (rates can't be summed); flag once per dimension-name.
    seen_uniform: set[str] = set()
    for v, rows in views.items():
        cps = col_by_view.get(v, [])
        dims = [c for c in cps if c.role == "dimension" and c.dtype == "categorical" and 4 <= c.distinct <= 30]
        count_meas = [c for c in cps if c.role == "measure" and not c.is_rate]
        if not count_meas:
            continue  # nothing summable → skip (avoids the meaningless-rate-sum noise)
        mname = count_meas[0].name
        for d in dims:
            if d.name in seen_uniform:
                continue
            totals: dict[str, float] = {}
            for r in rows:
                val = parse_num(r.get(mname))
                if val is None:
                    continue
                totals[str(r.get(d.name))] = totals.get(str(r.get(d.name)), 0) + val
            vals = list(totals.values())
            if len(vals) >= 4 and statistics.mean(vals):
                cv = abs(statistics.pstdev(vals) / statistics.mean(vals))
                if cv < UNIFORM_CV_MAX:
                    seen_uniform.add(d.name)
                    flags.append(QualityFlag("warn", "suspicious_uniform", f"{v}::{d.name}",
                        f"'{d.name}' ({mname}) is near-uniform (CV={cv:.2f}) across {len(vals)} categories — "
                        f"possibly synthetic/random. Do not headline a 'largest segment'."))

    # label inconsistency (from entity aliases)
    for e in entities:
        if e.aliases:
            pairs = "; ".join(f"'{k}'→'{v}'" for k, v in list(e.aliases.items())[:6])
            flags.append(QualityFlag("warn", "inconsistent_labels", e.name,
                f"Entity '{e.name}' has inconsistent labels across views: {pairs}"))

    # unreconciled rate/percent measures (flag once per measure name)
    ratio_exprs = " ".join(r.expr for r in relationships if r.kind == "ratio")
    seen_pct: set[str] = set()
    for c in columns:
        if c.role == "measure" and c.is_rate and c.name not in seen_pct:
            seen_pct.add(c.name)
            if c.name not in ratio_exprs:
                flags.append(QualityFlag("info", "unreconciled_rate", c.name,
                    f"Rate/percentage metric '{c.name}' (~{c.mean}) could not be reconciled to any "
                    f"discoverable ratio of other measures — verify it means what it claims."))

    return flags


# ── top-level ─────────────────────────────────────────────────────────────────────

def profile_workbook(views_raw: dict[str, list[dict]], total_views: Optional[int] = None) -> WorkbookProfile:
    """
    views_raw: {view_name: [row dicts]} — empty lists allowed (dashboards/filters).
    """
    data_views = {k: v for k, v in views_raw.items() if v}
    columns: list[ColumnProfile] = []
    view_summ: dict[str, dict] = {}

    for vname, rows in views_raw.items():
        if not rows:
            view_summ[vname] = {"rows": 0, "kind": "empty (dashboard/filter container)"}
            continue
        cols = list(rows[0].keys())
        cps = [profile_column(vname, c, [r.get(c) for r in rows]) for c in cols]
        columns.extend(cps)
        view_summ[vname] = {
            "rows": len(rows),
            "dimensions": [c.name for c in cps if c.role == "dimension"],
            "measures":   [c.name for c in cps if c.role == "measure"],
            "grain": "scalar" if len(rows) == 1 else "series",
        }

    entities = resolve_entities(data_views, columns)
    relationships = discover_relationships(data_views, columns)
    flags = detect_flags(data_views, columns, entities, relationships)

    return WorkbookProfile(
        total_views=total_views if total_views is not None else len(views_raw),
        data_views=len(data_views),
        views=view_summ,
        columns=columns,
        entities=entities,
        relationships=relationships,
        flags=flags,
    )
