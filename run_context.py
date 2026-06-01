"""
run_context.py
──────────────
Thread-local run ID — shared between the pipeline, agents, and log handler.

Why this exists:
  When multiple pipeline runs happen concurrently, each run's RunLogHandler
  must only capture log records from ITS OWN threads (main + sub-threads).
  threading.local() holds the run_id per-thread, but ThreadPoolExecutor
  sub-threads don't inherit parent locals — so base.py explicitly propagates
  it when submitting to the pool.

Usage:
  set_run_id("abc-123")   ← call at thread start
  get_run_id()            ← returns "abc-123" or None
"""

from __future__ import annotations
import threading

_local = threading.local()


def get_run_id() -> str | None:
    return getattr(_local, "run_id", None)


def set_run_id(run_id: str | None) -> None:
    _local.run_id = run_id
