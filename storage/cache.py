"""
storage/cache.py
─────────────────
Redis cache for Intelligence Config responses.

TTL: 15 minutes (900 seconds)

Pattern: cache key = "config:{company_id}"

Usage:
    from storage.cache import ConfigCache

    cache = ConfigCache()
    config = cache.get("acme_corp")
    if config is None:
        config = expensive_db_query()
        cache.set("acme_corp", config)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_DEFAULT_TTL = 900  # 15 minutes


class ConfigCache:
    """Redis-backed cache for Intelligence Config JSON."""

    def __init__(self, ttl: int = _DEFAULT_TTL) -> None:
        self._ttl    = ttl
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import redis
                url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
                self._client = redis.from_url(url, decode_responses=True)
                self._client.ping()
                log.info("Redis connected at %s", url)
            except Exception as exc:
                log.warning("Redis unavailable (%s) — caching disabled", exc)
                self._client = _NoOpCache()
        return self._client

    def get(self, company_id: str) -> dict[str, Any] | None:
        """Return cached config dict, or None if not found / expired."""
        client = self._get_client()
        try:
            raw = client.get(self._key(company_id))
            if raw:
                log.debug("Cache HIT for company %s", company_id)
                return json.loads(raw)
        except Exception as exc:
            log.warning("Cache get error: %s", exc)
        return None

    def set(self, company_id: str, config: dict[str, Any]) -> None:
        """Store config dict with TTL."""
        client = self._get_client()
        try:
            client.setex(
                self._key(company_id),
                self._ttl,
                json.dumps(config),
            )
            log.debug("Cache SET for company %s (TTL=%ds)", company_id, self._ttl)
        except Exception as exc:
            log.warning("Cache set error: %s", exc)

    def invalidate(self, company_id: str) -> None:
        """Remove cached config for a company."""
        client = self._get_client()
        try:
            client.delete(self._key(company_id))
            log.info("Cache invalidated for company %s", company_id)
        except Exception as exc:
            log.warning("Cache invalidate error: %s", exc)

    @staticmethod
    def _key(company_id: str) -> str:
        return f"config:{company_id}"


class _NoOpCache:
    """Fallback when Redis is unavailable — all operations are no-ops."""

    def get(self, *args, **kwargs):
        return None

    def set(self, *args, **kwargs):
        pass

    def setex(self, *args, **kwargs):
        pass

    def delete(self, *args, **kwargs):
        pass

    def ping(self):
        raise ConnectionError("NoOp cache")
