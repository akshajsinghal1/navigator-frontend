"""Helpers for locating Intelligence Config JSON on disk."""

from __future__ import annotations

import os
from pathlib import Path

# Pinned demo snapshot — survives new pipeline runs in output/.
DEMO_CONFIG_BASENAME = "intelligence_config_NAVIGATOR_DEMO_20260610.json"
DEMO_WORKBOOK_ALIASES = frozenset({
    "navigator_demo",
    "navigator-demo",
    "demo",
})


def demo_config_path(output_dir: Path) -> Path | None:
    """Return the pinned demo config path when it exists."""
    path = output_dir / "demo" / DEMO_CONFIG_BASENAME
    return path if path.is_file() else None


def _env_demo_path(output_dir: Path) -> Path | None:
    raw = os.environ.get("NAVIGATOR_DEMO_CONFIG", "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = output_dir / path
    return path if path.is_file() else None


def is_demo_workbook_key(company_key: str) -> bool:
    return company_key.lower().replace("-", "_") in DEMO_WORKBOOK_ALIASES


def resolve_intelligence_config_path(output_dir: Path, company_key: str) -> Path | None:
    """
    Resolve which intelligence_config JSON to serve for a workbook / company key.

    Priority:
      1. NAVIGATOR_DEMO_CONFIG env var (explicit path)
      2. Demo workbook alias (NAVIGATOR_DEMO, demo, …) → output/demo/
      3. Latest mtime match in output/ for company_key
    """
    env_demo = _env_demo_path(output_dir)
    if env_demo:
        return env_demo

    if is_demo_workbook_key(company_key):
        pinned = demo_config_path(output_dir)
        if pinned:
            return pinned

    return latest_intelligence_config_path(output_dir, company_key)


def latest_intelligence_config_path(output_dir: Path, company_key: str) -> Path | None:
    """
    Return the most recently modified intelligence_config JSON for company_key.

    Uses file mtime (not lexicographic name) so stale ``_l3`` suffix files do not
    win over a newer full pipeline run.
    """
    key = company_key.lower()
    candidates = [
        p for p in output_dir.glob("intelligence_config_*.json")
        if key in p.name.lower()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)
