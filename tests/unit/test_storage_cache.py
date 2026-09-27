"""Response cache keys and invalidation after writes outside the API."""

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from src.pipelines import graph_writer
from src.utils.cache import GRAPH_CACHE_PATTERNS, invalidate_graph_caches, make_cache_key


class TestCacheKeys:
    """Values keep their type in the key (D4-17)."""

    def test_none_and_the_string_none_differ(self) -> None:
        assert make_cache_key("search", form=None) != make_cache_key("search", form="None")

    def test_numbers_and_strings_differ(self) -> None:
        assert make_cache_key("search", limit=1) != make_cache_key("search", limit="1")

    def test_keyword_order_does_not_matter(self) -> None:
        assert make_cache_key("search", form="a", language="eng") == make_cache_key(
            "search", language="eng", form="a"
        )

    def test_uuid_is_keyed_by_its_string(self) -> None:
        lsr_id = uuid4()
        assert make_cache_key("lsr", lsr_id) == make_cache_key("lsr", str(lsr_id))

    def test_prefix_kept_for_pattern_invalidation(self) -> None:
        assert make_cache_key("search", form="x").startswith("lexicon:search:")
        assert make_cache_key("lsr", "x").startswith("lexicon:lsr:")


class FakeAsyncRedis:
    """The subset of redis.asyncio.Redis used for invalidation."""

    def __init__(self, keys: list[str]) -> None:
        self.keys = set(keys)
        self.closed = False

    async def scan(self, cursor: int, match: str, count: int) -> tuple[int, list[str]]:
        prefix = match.rstrip("*")
        return 0, sorted(k for k in self.keys if k.startswith(prefix))

    async def delete(self, *keys: str) -> int:
        self.keys -= set(keys)
        return len(keys)

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        self.closed = True


_KEYS = [
    "lexicon:search:1",
    "lexicon:search:2",
    "lexicon:lsr:3",
    "lexicon:job:4",
    "lexicon:es-reindex-lock",
    "lexicon:ratelimit:5",
]


class TestGraphCacheInvalidation:
    """Ingestion clears the API's cached searches and LSR records (D4-10)."""

    def test_only_graph_responses_are_dropped(self) -> None:
        redis = FakeAsyncRedis(_KEYS)
        assert GRAPH_CACHE_PATTERNS == ("lexicon:search:*", "lexicon:lsr:*")
        assert asyncio.run(invalidate_graph_caches(redis)) == 3
        assert redis.keys == {"lexicon:job:4", "lexicon:es-reindex-lock", "lexicon:ratelimit:5"}

    def _manager(
        self, redis: Any, uri: str = "redis://127.0.0.1:1", configured: bool = True
    ) -> Any:
        return SimpleNamespace(
            _redis_client=redis,
            config=SimpleNamespace(redis_uri=uri, redis_configured=configured),
        )

    def test_unconfigured_redis_is_not_contacted(self, monkeypatch: Any) -> None:
        import redis.asyncio

        def fail(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("must not connect to an unconfigured Redis")

        monkeypatch.setattr(redis.asyncio, "from_url", fail)
        manager = self._manager(None, configured=False)
        assert asyncio.run(graph_writer._clear_api_cache(manager, owns_db=True)) is False

    def test_writer_uses_a_connected_manager_client(self) -> None:
        redis = FakeAsyncRedis(_KEYS)
        cleared = asyncio.run(graph_writer._clear_api_cache(self._manager(redis), owns_db=False))
        assert cleared and not any(k.startswith("lexicon:search:") for k in redis.keys)
        assert not redis.closed  # not ours to close

    def test_writer_connects_its_own_client(self, monkeypatch: Any) -> None:
        import redis.asyncio

        fake = FakeAsyncRedis(_KEYS)
        monkeypatch.setattr(redis.asyncio, "from_url", lambda *args, **kwargs: fake)
        cleared = asyncio.run(graph_writer._clear_api_cache(self._manager(None), owns_db=True))
        assert cleared and fake.closed
        assert "lexicon:lsr:3" not in fake.keys

    def test_unreachable_redis_is_reported_not_raised(self) -> None:
        cleared = asyncio.run(graph_writer._clear_api_cache(self._manager(None), owns_db=True))
        assert cleared is False

    def test_manager_without_redis_is_not_cleared(self) -> None:
        cleared = asyncio.run(graph_writer._clear_api_cache(self._manager(None), owns_db=False))
        assert cleared is False
