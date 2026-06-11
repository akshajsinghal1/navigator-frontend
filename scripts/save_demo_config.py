"""Deprecated wrapper — use scripts/build_demo_snapshot.py instead."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    script = ROOT / "scripts" / "build_demo_snapshot.py"
    cmd = [sys.executable, str(script), *sys.argv[1:]]
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
