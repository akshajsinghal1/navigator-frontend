"""
CLI entry point — run the blueprint generator on a Tableau inventory JSON.

Usage
─────
  python run_blueprint.py                         # latest inventory in output/
  python run_blueprint.py path/to/inventory.json  # specific file
  python run_blueprint.py path/to/inventory.json "Sales Leader"  # persona override

Requires
────────
  ANTHROPIC_API_KEY in .env (or environment)
  TABLEAU_* vars are not needed — the inventory JSON is the only input.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from blueprint_pipeline import BlueprintGenerator


def _latest_inventory(output_dir: str = "output") -> Path:
    candidates = sorted(
        Path(output_dir).glob("inventory_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"no inventory_*.json found in {output_dir}/")
    return candidates[0]


def main() -> int:
    load_dotenv()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.stderr.write(
            "missing required env var: ANTHROPIC_API_KEY\n"
            "Add it to .env:  ANTHROPIC_API_KEY=sk-ant-...\n"
        )
        return 2

    inventory_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _latest_inventory()
    persona_id = sys.argv[2] if len(sys.argv) > 2 else None

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger(__name__)

    if not inventory_path.exists():
        sys.stderr.write(f"inventory file not found: {inventory_path}\n")
        return 1

    log.info("loading inventory from %s", inventory_path)
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))

    generator = BlueprintGenerator()
    blueprint = generator.generate(inventory, persona_id=persona_id)
    path = generator.write_to_json(blueprint)

    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
