"""
cache_manager.py

Centralized cache manager with per-key TTL, atomic writes and
hourly cleanup task. All cache access in the project must go through
this singleton instead of raw file reads/writes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from src.utils.paths import CACHE_DIR

logger = logging.getLogger(__name__)

# Metadata file that stores TTL and timestamp for each cache key
CACHE_META_FILE = CACHE_DIR / "_meta.json"


class CacheManager:
    """
    File-based cache manager with TTL support.

    Keys map to JSON files under data/cache/{key}.json.
    Metadata (timestamps, TTLs) is stored in data/cache/_meta.json.

    Usage:
        cache = CacheManager()
        await cache.set("snapshot", data, ttl_minutes=30)
        data = await cache.get("snapshot")   # None if expired or missing
    """

    def __init__(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._meta: dict[str, dict] = self._load_meta()
        self._cleanup_task: asyncio.Task[Any] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get(self, key: str) -> dict | None:
        """
        Return cached data for key if it exists and has not expired.
        Returns None if missing, expired or corrupted.
        """
        meta = self._meta.get(key)
        if not meta:
            logger.debug("[CACHE] Miss (no meta) | key=%s", key)
            return None

        # Check TTL
        stored_at = meta.get("stored_at", 0.0)
        ttl_seconds = meta.get("ttl_minutes", 0) * 60
        age_seconds = time.time() - stored_at

        if age_seconds > ttl_seconds:
            logger.debug(
                "[CACHE] Expired | key=%s | age=%.1fmin | ttl=%smin",
                key,
                age_seconds / 60,
                meta.get("ttl_minutes"),
            )
            return None

        # Load data file
        cache_file = self._key_to_path(key)
        if not cache_file.exists():
            logger.debug("[CACHE] Miss (file missing) | key=%s", key)
            return None

        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            logger.debug(
                "[CACHE] Hit | key=%s | age=%.1fmin",
                key,
                age_seconds / 60,
            )
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[CACHE] Read error | key=%s | error=%s", key, exc)
            return None

    async def set(self, key: str, data: dict, ttl_minutes: int) -> None:
        """
        Store data under key with the given TTL.
        Writes atomically via a temp file to avoid partial reads.
        """
        cache_file = self._key_to_path(key)

        try:
            # Atomic write: write to .tmp then rename
            tmp = cache_file.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.rename(cache_file)

            # Update metadata
            self._meta[key] = {
                "stored_at": time.time(),
                "ttl_minutes": ttl_minutes,
                "file": cache_file.name,
            }
            self._save_meta()

            logger.debug("[CACHE] Set | key=%s | ttl=%smin", key, ttl_minutes)
        except OSError as exc:
            logger.error("[CACHE] Write error | key=%s | error=%s", key, exc)

    async def invalidate(self, key: str) -> None:
        """
        Remove a specific key from cache (data file + metadata).
        No-op if the key does not exist.
        """
        cache_file = self._key_to_path(key)

        if cache_file.exists():
            try:
                cache_file.unlink()
            except OSError as exc:
                logger.warning(
                    "[CACHE] Delete error | key=%s | error=%s", key, exc
                )

        if key in self._meta:
            del self._meta[key]
            self._save_meta()
            logger.debug("[CACHE] Invalidated | key=%s", key)

    async def cleanup_expired(self) -> int:
        """
        Delete all expired cache entries (data files + metadata).
        Returns the number of entries removed.
        """
        now = time.time()
        removed = 0
        expired_keys = []

        for key, meta in self._meta.items():
            stored_at = meta.get("stored_at", 0.0)
            ttl_seconds = meta.get("ttl_minutes", 0) * 60
            if (now - stored_at) > ttl_seconds:
                expired_keys.append(key)

        for key in expired_keys:
            await self.invalidate(key)
            removed += 1

        if removed:
            logger.info(
                "[CACHE] Cleanup | removed=%s expired entries", removed
            )

        return removed

    def get_age_minutes(self, key: str) -> float | None:
        """
        Return the age in minutes of a cached key, or None if not found.
        Used for logging cache hit age without loading the full payload.
        """
        meta = self._meta.get(key)
        if not meta:
            return None
        return (time.time() - meta.get("stored_at", 0.0)) / 60

    def start_cleanup_task(self) -> None:
        """
        Register a background asyncio task that calls cleanup_expired()
        every hour. Must be called after the event loop is running
        (i.e. inside on_startup).
        """
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(), name="cache_cleanup_loop"
        )
        logger.info("[CACHE] Hourly cleanup task started")

    def stop(self) -> None:
        """Cancel the cleanup background task on shutdown."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            logger.info("[CACHE] Cleanup task stopped")

    # ------------------------------------------------------------------
    # Convenience wrappers for known cache keys
    # ------------------------------------------------------------------

    async def get_snapshot(self) -> dict | None:
        """Return the full fetcher snapshot if not expired (TTL: 30min)."""
        return await self.get("full_snapshot")

    async def set_snapshot(self, data: dict) -> None:
        """Cache the full fetcher snapshot for 30 minutes."""
        await self.set("full_snapshot", data, ttl_minutes=30)

    async def get_digest(self, digest_id: str) -> dict | None:
        """Return a specific digest from cache if not expired (TTL: 24h)."""
        return await self.get(f"digest_{digest_id}")

    async def set_digest(self, digest_id: str, data: dict) -> None:
        """Cache a digest for 24 hours."""
        await self.set(f"digest_{digest_id}", data, ttl_minutes=1440)

    async def invalidate_snapshot(self) -> None:
        """Force-expire the snapshot cache (e.g. before a manual digest)."""
        await self.invalidate("full_snapshot")

    def get_latest_digest_id(self) -> str | None:
        """
        Return the digest_id (without prefix) of the most recently stored digest
        in cache, or None if no digest keys exist. Used for /ask context.
        """
        prefix = "digest_"
        candidates = [
            (key, meta.get("stored_at", 0.0))
            for key, meta in self._meta.items()
            if key.startswith(prefix)
        ]
        if not candidates:
            return None
        latest_key = max(candidates, key=lambda x: x[1])[0]
        return latest_key[len(prefix) :]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _cleanup_loop(self) -> None:
        """Run cleanup_expired() every hour indefinitely."""
        while True:
            await asyncio.sleep(3600)
            try:
                removed = await self.cleanup_expired()
                logger.info(
                    "[CACHE] Hourly cleanup complete | removed=%s", removed
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "[CACHE] Cleanup loop error: %s", exc, exc_info=True
                )

    def _key_to_path(self, key: str) -> Path:
        """Convert a cache key to its file path under CACHE_DIR."""
        # Sanitize key to avoid path traversal
        safe_key = key.replace("/", "_").replace("..", "_")
        return CACHE_DIR / f"{safe_key}.json"

    def _load_meta(self) -> dict:
        """Load metadata from disk, returning empty dict on any error."""
        if not CACHE_META_FILE.exists():
            return {}
        try:
            return json.loads(CACHE_META_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_meta(self) -> None:
        """Persist metadata to disk atomically."""
        try:
            tmp = CACHE_META_FILE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._meta, indent=2),
                encoding="utf-8",
            )
            tmp.rename(CACHE_META_FILE)
        except OSError as exc:
            logger.warning("[CACHE] Meta save error: %s", exc)


# Module-level singleton — import this everywhere
cache = CacheManager()
