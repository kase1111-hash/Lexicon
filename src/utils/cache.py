"""Redis-based caching utilities for API routes."""

import hashlib
import json
import logging
from functools import wraps
from typing import Any, Callable, TypeVar

from src.config import get_settings
from src.utils.db import get_db

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Default cache TTLs (in seconds)
DEFAULT_TTL = 300  # 5 minutes
LSR_CACHE_TTL = 600  # 10 minutes
SEARCH_CACHE_TTL = 180  # 3 minutes
GRAPH_CACHE_TTL = 300  # 5 minutes


def make_cache_key(prefix: str, *args: Any, **kwargs: Any) -> str:
    """
    Generate a cache key from prefix and arguments.

    Args:
        prefix: Key prefix (e.g., 'lsr', 'search').
        *args: Positional arguments to include in key.
        **kwargs: Keyword arguments to include in key.

    Returns:
        A unique cache key string.
    """
    # Create a deterministic representation
    key_data = {
        "args": [str(a) for a in args],
        "kwargs": {k: str(v) for k, v in sorted(kwargs.items())},
    }
    key_str = json.dumps(key_data, sort_keys=True)

    # Hash for consistent length
    key_hash = hashlib.md5(key_str.encode()).hexdigest()[:16]

    return f"lexicon:{prefix}:{key_hash}"


class CacheManager:
    """
    Manager for Redis-based caching operations.

    Provides async methods for cache get/set/delete with automatic
    JSON serialization and error handling.
    """

    def __init__(self):
        """Initialize the cache manager."""
        self._enabled = True

    async def get(self, key: str) -> Any | None:
        """
        Get a value from cache.

        Args:
            key: The cache key.

        Returns:
            The cached value or None if not found/error.
        """
        if not self._enabled:
            return None

        try:
            db = await get_db()
            if not db._redis_client:
                return None

            value = await db.redis.get(key)
            if value:
                return json.loads(value)
            return None

        except Exception as e:
            logger.debug(f"Cache get error for {key}: {e}")
            return None

    async def set(
        self,
        key: str,
        value: Any,
        ttl: int = DEFAULT_TTL,
    ) -> bool:
        """
        Set a value in cache.

        Args:
            key: The cache key.
            value: The value to cache (must be JSON-serializable).
            ttl: Time-to-live in seconds.

        Returns:
            True if successful, False otherwise.
        """
        if not self._enabled:
            return False

        try:
            db = await get_db()
            if not db._redis_client:
                return False

            serialized = json.dumps(value, default=str)
            await db.redis.setex(key, ttl, serialized)
            return True

        except Exception as e:
            logger.debug(f"Cache set error for {key}: {e}")
            return False

    async def delete(self, key: str) -> bool:
        """
        Delete a value from cache.

        Args:
            key: The cache key.

        Returns:
            True if deleted, False otherwise.
        """
        if not self._enabled:
            return False

        try:
            db = await get_db()
            if not db._redis_client:
                return False

            await db.redis.delete(key)
            return True

        except Exception as e:
            logger.debug(f"Cache delete error for {key}: {e}")
            return False

    async def delete_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching a pattern.

        Args:
            pattern: Redis key pattern (e.g., 'lexicon:lsr:*').

        Returns:
            Number of keys deleted.
        """
        if not self._enabled:
            return 0

        try:
            db = await get_db()
            if not db._redis_client:
                return 0

            # Scan for matching keys
            cursor = 0
            deleted = 0

            while True:
                cursor, keys = await db.redis.scan(cursor, match=pattern, count=100)
                if keys:
                    await db.redis.delete(*keys)
                    deleted += len(keys)
                if cursor == 0:
                    break

            return deleted

        except Exception as e:
            logger.debug(f"Cache delete pattern error for {pattern}: {e}")
            return 0

    def disable(self) -> None:
        """Disable caching (useful for testing)."""
        self._enabled = False

    def enable(self) -> None:
        """Enable caching."""
        self._enabled = True


# Global cache manager instance
_cache_manager: CacheManager | None = None


async def get_cache() -> CacheManager:
    """Get the global cache manager instance."""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = CacheManager()
    return _cache_manager


def cached(
    prefix: str,
    ttl: int = DEFAULT_TTL,
    key_builder: Callable[..., str] | None = None,
):
    """
    Decorator for caching async function results.

    Args:
        prefix: Cache key prefix.
        ttl: Time-to-live in seconds.
        key_builder: Optional custom function to build cache key.

    Usage:
        @cached("lsr", ttl=600)
        async def get_lsr(lsr_id: UUID) -> dict:
            ...
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            # Build cache key
            if key_builder:
                cache_key = key_builder(*args, **kwargs)
            else:
                cache_key = make_cache_key(prefix, *args, **kwargs)

            # Try to get from cache
            cache = await get_cache()
            cached_value = await cache.get(cache_key)

            if cached_value is not None:
                logger.debug(f"Cache hit for {cache_key}")
                return cached_value

            # Call the function
            result = await func(*args, **kwargs)

            # Cache the result
            await cache.set(cache_key, result, ttl)
            logger.debug(f"Cached result for {cache_key}")

            return result

        return wrapper

    return decorator


async def invalidate_lsr_cache(lsr_id: str) -> None:
    """
    Invalidate all cache entries for an LSR.

    Args:
        lsr_id: The LSR ID to invalidate.
    """
    cache = await get_cache()
    # Delete specific LSR cache
    await cache.delete(f"lexicon:lsr:{lsr_id}")
    # Also delete related search caches (they might include this LSR)
    await cache.delete_pattern("lexicon:search:*")


async def invalidate_search_cache() -> None:
    """Invalidate all search result caches."""
    cache = await get_cache()
    await cache.delete_pattern("lexicon:search:*")


async def invalidate_all_cache() -> None:
    """Invalidate all Lexicon caches."""
    cache = await get_cache()
    await cache.delete_pattern("lexicon:*")
