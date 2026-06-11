"""Load Hyper table rows from a local .twbx for demo audit / L3 refresh."""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TWBX = ROOT / "output" / "wb_download.twbx"


def load_hyper_view_cache(twbx_path: Path | None = None) -> dict[str, list[dict]]:
    """Return {``[TABLE] name`` → rows} from the workbook extract."""
    path = twbx_path or DEFAULT_TWBX
    if not path.is_file():
        log.warning("Hyper cache: no twbx at %s", path)
        return {}

    try:
        from pipeline.hyper_extractor import _read_hyper
    except ImportError:
        log.warning("Hyper cache: hyper_extractor unavailable")
        return {}

    import tempfile

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            with zipfile.ZipFile(path) as zf:
                hyper_files = [n for n in zf.namelist() if n.endswith(".hyper")]
                if not hyper_files:
                    return {}
                zf.extract(hyper_files[0], tmpdir)
            hyper_path = tmp / hyper_files[0]
            tables = _read_hyper(str(hyper_path), sample_rows=5, max_full_rows=100_000)
    except Exception as exc:
        log.warning("Hyper cache: failed to read %s: %s", path, exc)
        return {}

    cache = {f"[TABLE] {t.table_name}": t.full_rows for t in tables}
    log.info("Hyper cache: %d tables from %s", len(cache), path.name)
    return cache
