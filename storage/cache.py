"""
storage/cache.py
─────────────────
Redis cache for Intelligence Config responses.

TTL: configurable via REDIS_CACHE_TTL env var (default 900s / 15 min)
Key: "nav:config:{company_id}"

Falls back gracefully to no-op when Redis is unavailable.
Logs once on first failure, then stays silent to avoid log spam.

Usage:
    from storage.cache import ConfigCache

    cache = ConfigCache()
    config = cache.get("acme_corp")
    if config is None:
        config = expensive_db_query()
        cache.set("acme_corp", config)

    # On data refresh — ALWAYS invalidate so next request gets fresh config:
    cache.invalidate("acme_corp")

    # Warm cache immediately after pipeline so first load is instant:
    cache.warm("acme_corp", config_dict)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_DEFAULT_TTL    = int(os.environ.get("REDIS_CACHE_TTL", "900"))   # 15 min default
_KEY_PREFIX     = "nav:config:"
_WARNED_UNAVAIL = False   # warn once, then silent


def _get_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")


class ConfigCache:
    """
    Redis-backed cache for Intelligence Config JSON.
    Thread-safe via connection pool.
    Falls back to no-op if Redis is unavailable.
    """

    def __init__(self, ttl: int = _DEFAULT_TTL) -> None:
        self._ttl  = ttl
        self._pool = None   # lazy-init

    def _client(self):
        global _WARNED_UNAVAIL
        try:
            import redis
            if self._pool is None:
                self._pool = redis.ConnectionPool.from_url(
                    _get_redis_url(),
                    decode_responses       = True,
                    max_connections        = 10,
                    socket_timeout         = 2,
                    socket_connect_timeout = 2,
                )
            client = redis.Redis(connection_pool=self._pool)
            client.ping()
            return client
        except Exception as exc:
            if not _WARNED_UNAVAIL:
                log.warning("Redis unavailable (%s) — caching disabled", exc)
                _WARNED_UNAVAIL = True
            return _NoOpClient()

    @staticmethod
    def _key(company_id: str) -> str:
        return f"{_KEY_PREFIX}{company_id.lower()}"

    # ── Core operations ────────────────────────────────────────────────────

    def get(self, company_id: str) -> dict[str, Any] | None:
        try:
            raw = self._client().get(self._key(company_id))
            if raw:
                log.debug("Cache HIT: %s", company_id)
                return json.loads(raw)
        except Exception as exc:
            log.debug("Cache get error: %s", exc)
        return None

    def set(self, company_id: str, config: dict[str, Any]) -> None:
        try:
            self._client().setex(
                self._key(company_id),
                self._ttl,
                json.dumps(config, ensure_ascii=False),
            )
            log.debug("Cache SET: %s (TTL=%ds)", company_id, self._ttl)
        except Exception as exc:
            log.debug("Cache set error: %s", exc)

    def invalidate(self, company_id: str) -> None:
        """Remove cached config. Call after ANY data or schema refresh."""
        try:
            deleted = self._client().delete(self._key(company_id))
            if deleted:
                log.info("Cache invalidated: %s", company_id)
        except Exception as exc:
            log.debug("Cache invalidate error: %s", exc)

    def warm(self, company_id: str, config: dict[str, Any]) -> None:
        """
        Warm cache immediately after pipeline completes.
        Ensures first dashboard request hits cache, not DB or file.
        """
        self.set(company_id, config)
        log.info("Cache warmed: %s", company_id)

    def invalidate_all(self) -> None:
        """Remove all Navigator config entries (use on full redeploy)."""
        try:
            client = self._client()
            keys   = client.keys(f"{_KEY_PREFIX}*")
            if keys:
                client.delete(*keys)
                log.info("Cache invalidated all (%d entries)", len(keys))
        except Exception as exc:
            log.debug("Cache invalidate_all error: %s", exc)

    def is_available(self) -> bool:
        try:
            self._client().ping()
            return True
        except Exception:
            return False


class _NoOpClient:
    """Silent fallback when Redis is unavailable."""
    def get(self, *a, **kw):    return None
    def set(self, *a, **kw):    pass
    def setex(self, *a, **kw):  pass
    def delete(self, *a, **kw): return 0
    def keys(self, *a, **kw):   return []
    def ping(self):              raise ConnectionError("NoOp")
