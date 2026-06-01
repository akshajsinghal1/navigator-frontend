"""
api/pipeline_status.py
───────────────────────
In-memory pipeline run status store.

Tracks every run that starts in this process — no DB or Redis required.
Used by the demo frontend to poll agent-level progress messages.

Thread-safe: all mutations are protected by a lock.

Persistence: when a run completes or fails, save_run_log() writes a JSON
snapshot to output/logs/<run_id>.json so logs survive server restarts.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from run_context import get_run_id

log = logging.getLogger(__name__)

# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class StatusMessage:
    time:    str    # "HH:MM:SS"
    agent:   str    # "Orchestrator" | "Domain: Sales" | "Chart" | "Pipeline" | "Tableau"
    message: str
    level:   str = "info"   # "info" | "success" | "warning" | "error"


@dataclass
class RunStatus:
    run_id:      str
    company_id:  str
    workbook:    str
    status:      Literal["queued", "running", "completed", "failed"] = "queued"
    stage:       str  = ""
    progress_pct: int = 0
    messages:    list[StatusMessage] = field(default_factory=list)
    error:       str | None  = None
    started_at:  str | None  = None
    completed_at: str | None = None


# ── Module-level store ────────────────────────────────────────────────────────

_RUNS: dict[str, RunStatus] = {}
_LOCK = threading.Lock()
_MAX_MESSAGES = 200   # cap per run to avoid unbounded growth


# ── Public API ────────────────────────────────────────────────────────────────

def create_run(run_id: str, company_id: str, workbook: str) -> RunStatus:
    run = RunStatus(
        run_id     = run_id,
        company_id = company_id,
        workbook   = workbook,
        started_at = _now(),
    )
    with _LOCK:
        _RUNS[run_id] = run
    return run


def get_run(run_id: str) -> RunStatus | None:
    with _LOCK:
        return _RUNS.get(run_id)


def get_run_for_company(company_id: str) -> RunStatus | None:
    """Return the most recent run for a company."""
    with _LOCK:
        runs = [r for r in _RUNS.values() if r.company_id == company_id]
    if not runs:
        return None
    return sorted(runs, key=lambda r: r.started_at or "", reverse=True)[0]


def emit(
    run_id:   str,
    agent:    str,
    message:  str,
    level:    str = "info",
    progress: int | None = None,
    stage:    str | None = None,
) -> None:
    """Append a status message and optionally update progress/stage."""
    with _LOCK:
        run = _RUNS.get(run_id)
        if run is None:
            return
        msg = StatusMessage(time=_now(), agent=agent, message=message, level=level)
        run.messages.append(msg)
        # Cap message history
        if len(run.messages) > _MAX_MESSAGES:
            run.messages = run.messages[-_MAX_MESSAGES:]
        if progress is not None:
            run.progress_pct = progress
        if stage is not None:
            run.stage = stage


def set_status(
    run_id:   str,
    status:   Literal["queued", "running", "completed", "failed"],
    progress: int | None = None,
    error:    str | None = None,
) -> None:
    with _LOCK:
        run = _RUNS.get(run_id)
        if run is None:
            return
        run.status = status
        if progress is not None:
            run.progress_pct = progress
        if error is not None:
            run.error = error
        if status in ("completed", "failed"):
            run.completed_at = _now()


def to_dict(run: RunStatus) -> dict[str, Any]:
    return {
        "run_id":       run.run_id,
        "company_id":   run.company_id,
        "workbook":     run.workbook,
        "status":       run.status,
        "stage":        run.stage,
        "progress_pct": run.progress_pct,
        "error":        run.error,
        "started_at":   run.started_at,
        "completed_at": run.completed_at,
        "messages": [
            {"time": m.time, "agent": m.agent, "message": m.message, "level": m.level}
            for m in run.messages
        ],
    }


# ── Logging handler that routes to the status store ──────────────────────────

class RunLogHandler(logging.Handler):
    """
    Attaches to the root logger for the duration of a pipeline run.
    Routes log records from agents.* and pipeline.* into the status store.
    """

    # Map logger name prefixes → display agent names
    _AGENT_MAP = {
        "agents.orchestrator":  "Orchestrator",
        "agents.base":          "Agent",
        "agents.domain_agent":  "Domain Agent",
        "agents.chart_agent":   "Chart Agent",
        "pipeline.runner":      "Pipeline",
        "pipeline.eda":         "EDA",
        "pipeline.l1_refresher":"L1 Refresh",
        "tableau.connector":    "Tableau",
        "tableau_inventory_extractor": "Inventory",
    }

    # Messages to always skip (too low-level for the demo UI)
    _SKIP_PREFIXES = (
        "HTTP Request:",
        "Signed in to",
        "Signed out",
        "Cache ",
        "DB ",
    )

    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id

    def emit(self, record: logging.LogRecord) -> None:  # type: ignore[override]
        # Isolate: only capture records emitted by THIS run's threads.
        # Prevents two concurrent pipeline runs from cross-contaminating each other's logs.
        if get_run_id() != self.run_id:
            return

        # Only capture our own loggers
        name = record.name
        agent = None
        for prefix, label in self._AGENT_MAP.items():
            if name.startswith(prefix):
                agent = label
                break
        if agent is None:
            return

        msg = record.getMessage()

        # Skip noise
        if any(msg.startswith(p) for p in self._SKIP_PREFIXES):
            return
        if not msg.strip():
            return

        level = "error" if record.levelno >= logging.ERROR else \
                "warning" if record.levelno >= logging.WARNING else "info"

        emit(self.run_id, agent, msg, level=level)


def save_run_log(run_id: str) -> Path | None:
    """
    Write the complete run snapshot to output/logs/<run_id>.json.
    Call this when a run reaches 'completed' or 'failed'.

    Returns the path written, or None on error.
    """
    with _LOCK:
        run = _RUNS.get(run_id)
        if run is None:
            return None
        data = to_dict(run)

    try:
        log_dir = Path("output") / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"{run_id}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("Run log saved → %s", path)
        return path
    except Exception as exc:
        log.warning("Could not save run log: %s", exc)
        return None


def _now() -> str:
    from datetime import timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%H:%M:%S")
