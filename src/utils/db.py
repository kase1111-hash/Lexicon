"""Database connection utilities for all storage backends."""

import asyncio
import logging
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)


def _dotenv_values() -> dict[str, str]:
    """Read the project's .env file (path overridable via ENV_FILE)."""
    path = Path(os.getenv("ENV_FILE", ".env"))
    if not path.is_file():
        return {}
    from dotenv import dotenv_values

    return {k: v for k, v in dotenv_values(path).items() if v is not None}


class DatabaseConfig:
    """Configuration for database connections.

    Values come from the process environment first, then the .env file
    (the same one docker compose reads), so host-side commands such as
    ingestion reach the compose services without extra exports. When a
    URI is not given it is derived from the compose credentials
    (NEO4J_PASSWORD, POSTGRES_*, ELASTICSEARCH_PASSWORD, REDIS_PASSWORD),
    URL-encoding the passwords; ELASTICSEARCH_HOST / REDIS_HOST /
    POSTGRES_HOST name the server (default localhost, as seen from the host).

    PostgreSQL is optional and nothing reads or writes it yet: the API only
    connects to it (and /health only reports it) when POSTGRES_URI is set.
    Migrations and scripts/load_initial_data.py use the derived URI.
    """

    def __init__(self) -> None:
        dotenv = _dotenv_values()

        def env(key: str, default: str = "") -> str:
            value = os.getenv(key)
            if value is None:
                value = dotenv.get(key)
            return value if value else default

        self.neo4j_uri = env("NEO4J_URI", "bolt://localhost:7687")
        self.neo4j_user = env("NEO4J_USER", "neo4j")
        self.neo4j_password = env("NEO4J_PASSWORD", "password")

        pg_user = quote(env("POSTGRES_USER", "ls_user"), safe="")
        pg_password = quote(env("POSTGRES_PASSWORD", "password"), safe="")
        pg_host = env("POSTGRES_HOST", "localhost")
        pg_port = env("POSTGRES_PORT", "5432")
        pg_db = env("POSTGRES_DB", "linguistic_stratigraphy")
        # Only an explicit POSTGRES_URI makes the API use PostgreSQL
        self.postgres_configured = bool(env("POSTGRES_URI"))
        self.postgres_uri = env(
            "POSTGRES_URI",
            f"postgresql://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{pg_db}",
        )

        es_password = env("ELASTICSEARCH_PASSWORD")
        es_host = env("ELASTICSEARCH_HOST", "localhost")
        # Whether the user pointed us at an Elasticsearch at all; optional
        # consumers (e.g. ingestion) skip it entirely when not configured.
        self.elasticsearch_configured = bool(env("ELASTICSEARCH_URI") or es_password)
        es_default = (
            f"http://elastic:{quote(es_password, safe='')}@{es_host}:9200"
            if es_password
            else f"http://{es_host}:9200"
        )
        self.elasticsearch_uri = env("ELASTICSEARCH_URI", es_default)

        redis_password = env("REDIS_PASSWORD")
        redis_host = env("REDIS_HOST", "localhost")
        redis_default = (
            f"redis://:{quote(redis_password, safe='')}@{redis_host}:6379"
            if redis_password
            else f"redis://{redis_host}:6379"
        )
        self.redis_uri = env("REDIS_URI", redis_default)
        # Like Elasticsearch, Redis is optional: only used when pointed at
        self.redis_configured = bool(env("REDIS_URI") or redis_password)


class DatabaseManager:
    """
    Manage connections to all database systems.

    This class provides a unified interface for connecting to and querying
    the various databases used by the linguistic stratigraphy system:
    - Neo4j: Graph storage for LSRs and relationships (required)
    - Elasticsearch: Full-text search (optional, falls back to Neo4j)
    - Redis: Caching and shared rate-limit/job state (optional)
    - PostgreSQL: reserved for future relational metadata; connected only
      when POSTGRES_URI is set
    """

    # Seconds to wait for a backend to answer a connection check
    connect_timeout: float = 5.0
    # Minimum seconds between attempts to reconnect an unavailable Neo4j
    reconnect_interval: float = 5.0

    def __init__(self, config: DatabaseConfig | None = None):
        """Initialize the database manager."""
        self.config = config or DatabaseConfig()

        self._neo4j_driver: Any = None
        self._postgres_pool: Any = None
        self._elasticsearch_client: Any = None
        self._redis_client: Any = None

        self._connected = False
        self._connection_errors: dict[str, str] = {}
        self._neo4j_last_attempt = float("-inf")
        self._postgres_last_attempt = float("-inf")
        # Set when Neo4j rejected our credentials; retrying would only trip
        # its failed-login lockout, so no lazy reconnects until connect_neo4j
        # is called explicitly
        self._neo4j_auth_failed = False

    async def connect_all(self) -> None:
        """Connect to all configured database systems.

        Neo4j is always tried. PostgreSQL, Elasticsearch and Redis are tried
        only when configured (POSTGRES_URI; ELASTICSEARCH_URI or
        ELASTICSEARCH_PASSWORD; REDIS_URI or REDIS_PASSWORD).
        """
        results = {"neo4j": await self.connect_neo4j()}
        if self.config.postgres_configured:
            results["postgres"] = await self.connect_postgres()
        if self.config.elasticsearch_configured:
            results["elasticsearch"] = await self.connect_elasticsearch()
        if self.config.redis_configured:
            results["redis"] = await self.connect_redis()
        self._connected = True
        failed = sorted(name for name, ok in results.items() if not ok)
        if not failed:
            logger.info("Connected to all databases")
        else:
            connected = sorted(name for name, ok in results.items() if ok)
            logger.warning(
                f"Connected to {', '.join(connected) or 'no databases'}; "
                f"unavailable: {', '.join(failed)}"
            )

    async def connect_neo4j(self) -> bool:
        """Connect to Neo4j graph database.

        The driver is only kept once the server has answered, so a failed
        attempt leaves the manager disconnected (and neo4j_session() retries).

        Returns:
            True if connection succeeded, False otherwise.
        """
        self._neo4j_last_attempt = time.monotonic()
        driver: Any = None
        try:
            from neo4j import AsyncGraphDatabase, NotificationDisabledCategory

            driver = AsyncGraphDatabase.driver(
                self.config.neo4j_uri,
                auth=(self.config.neo4j_user, self.config.neo4j_password),
                # "unknown label/property/relationship type" notices fire on
                # every query against a graph that lacks some edge type yet
                notifications_disabled_categories=[NotificationDisabledCategory.UNRECOGNIZED],
            )
            await asyncio.wait_for(driver.verify_connectivity(), self.connect_timeout)
        except ImportError:
            msg = "neo4j package not installed"
            logger.warning(f"{msg}, Neo4j connection disabled")
            self._connection_errors["neo4j"] = msg
            return False
        except Exception as e:
            msg = str(e) or type(e).__name__
            logger.error(f"Failed to connect to Neo4j: {msg}")
            self._connection_errors["neo4j"] = msg
            self._neo4j_auth_failed = str(getattr(e, "code", "")).startswith(
                "Neo.ClientError.Security."
            )
            if driver is not None:
                try:
                    await driver.close()
                except Exception as close_error:
                    logger.debug(f"Error closing failed Neo4j driver: {close_error}")
            return False

        if self._neo4j_driver is not None:
            await self._neo4j_driver.close()
        self._neo4j_driver = driver
        self._neo4j_auth_failed = False
        self._connection_errors.pop("neo4j", None)
        logger.info("Connected to Neo4j")
        return True

    async def _reconnect_neo4j_if_needed(self) -> None:
        """Retry an unavailable Neo4j, at most once per reconnect_interval.

        Covers a Neo4j that was down when the API started; once connected, the
        driver itself re-establishes connections dropped by server restarts.
        Rejected credentials are not retried.
        """
        if self._neo4j_driver is not None or self._neo4j_auth_failed:
            return
        if time.monotonic() - self._neo4j_last_attempt < self.reconnect_interval:
            return
        # connect_neo4j stamps the attempt time before its first await, so
        # concurrent callers skip instead of piling up reconnects
        await self.connect_neo4j()

    async def connect_postgres(self) -> bool:
        """Connect to PostgreSQL database.

        Returns:
            True if connection succeeded, False otherwise.
        """
        self._postgres_last_attempt = time.monotonic()
        try:
            import asyncpg

            # Nothing queries PostgreSQL yet, so keep the pool small
            self._postgres_pool = await asyncpg.create_pool(
                self.config.postgres_uri,
                min_size=1,
                max_size=5,
                timeout=self.connect_timeout,
            )
            self._connection_errors.pop("postgres", None)
            logger.info("Connected to PostgreSQL")
            return True
        except ImportError:
            msg = "asyncpg package not installed"
            logger.warning(f"{msg}, PostgreSQL connection disabled")
            self._connection_errors["postgres"] = msg
            return False
        except Exception as e:
            msg = str(e) or type(e).__name__
            logger.error(f"Failed to connect to PostgreSQL: {msg}")
            self._connection_errors["postgres"] = msg
            return False

    async def connect_elasticsearch(self, quiet: bool = False) -> bool:
        """Connect to Elasticsearch.

        Args:
            quiet: Log a failure at DEBUG instead of ERROR (for retries).

        Returns:
            True if connection succeeded, False otherwise.
        """
        try:
            from elasticsearch import AsyncElasticsearch

            self._elasticsearch_client = AsyncElasticsearch([self.config.elasticsearch_uri])
            # Verify connection
            await self._elasticsearch_client.info()
            self._connection_errors.pop("elasticsearch", None)
            logger.info("Connected to Elasticsearch")
            return True
        except ImportError:
            msg = "elasticsearch package not installed"
            logger.warning(f"{msg}, Elasticsearch connection disabled")
            self._connection_errors["elasticsearch"] = msg
            return False
        except Exception as e:
            msg = str(e) or type(e).__name__
            logger.log(
                logging.DEBUG if quiet else logging.ERROR,
                f"Failed to connect to Elasticsearch: {msg}",
            )
            self._connection_errors["elasticsearch"] = msg
            if self._elasticsearch_client:
                try:
                    await self._elasticsearch_client.close()
                except Exception as close_error:
                    logger.debug(f"Error closing failed Elasticsearch client: {close_error}")
                self._elasticsearch_client = None
            return False

    async def connect_redis(self, quiet: bool = False) -> bool:
        """Connect to Redis.

        Args:
            quiet: Log a failure at DEBUG instead of ERROR (for retries).

        Returns:
            True if connection succeeded, False otherwise.
        """
        try:
            import redis.asyncio as redis

            self._redis_client = redis.from_url(
                self.config.redis_uri,
                socket_connect_timeout=self.connect_timeout,
                socket_timeout=self.connect_timeout,
            )
            # Verify connection
            await self._redis_client.ping()
            self._connection_errors.pop("redis", None)
            logger.info("Connected to Redis")
            return True
        except ImportError:
            msg = "redis package not installed"
            logger.warning(f"{msg}, Redis connection disabled")
            self._connection_errors["redis"] = msg
            return False
        except Exception as e:
            msg = str(e) or type(e).__name__
            logger.log(
                logging.DEBUG if quiet else logging.ERROR, f"Failed to connect to Redis: {msg}"
            )
            self._connection_errors["redis"] = msg
            if self._redis_client:
                try:
                    await self._redis_client.aclose()
                except Exception as close_error:
                    logger.debug(f"Error closing failed Redis client: {close_error}")
                self._redis_client = None
            return False

    async def close_all(self) -> None:
        """Close all database connections."""
        if self._neo4j_driver:
            await self._neo4j_driver.close()
            logger.info("Closed Neo4j connection")

        if self._postgres_pool:
            await self._postgres_pool.close()
            logger.info("Closed PostgreSQL connection")

        if self._elasticsearch_client:
            await self._elasticsearch_client.close()
            logger.info("Closed Elasticsearch connection")

        if self._redis_client:
            await self._redis_client.aclose()
            logger.info("Closed Redis connection")

        self._neo4j_driver = None
        self._postgres_pool = None
        self._elasticsearch_client = None
        self._redis_client = None
        self._connected = False
        self._connection_errors.clear()
        logger.info("Closed all database connections")

    def get_connection_status(self) -> dict[str, dict[str, Any]]:
        """Get the status of all database connections.

        Returns:
            Dictionary with connection status for each database; "configured"
            is False for an optional store the API was not asked to use.
        """
        return {
            "neo4j": {
                "connected": self._neo4j_driver is not None,
                "configured": True,
                "error": self._connection_errors.get("neo4j"),
            },
            "postgres": {
                "connected": self._postgres_pool is not None,
                "configured": self.config.postgres_configured,
                "error": self._connection_errors.get("postgres"),
            },
            "elasticsearch": {
                "connected": self._elasticsearch_client is not None,
                "configured": self.config.elasticsearch_configured,
                "error": self._connection_errors.get("elasticsearch"),
            },
            "redis": {
                "connected": self._redis_client is not None,
                "configured": self.config.redis_configured,
                "error": self._connection_errors.get("redis"),
            },
        }

    async def ping(self, timeout: float = 2.0) -> dict[str, bool]:
        """Actively check every configured backend.

        Unlike get_connection_status(), which only reports whether a client
        exists, this round-trips to each server (concurrently, each bounded by
        `timeout` seconds). An unavailable Neo4j, or a configured PostgreSQL
        without a pool, is reconnected first (throttled).

        Returns:
            Dictionary mapping database name to whether it answered. Stores
            that are not configured are left out.
        """
        await self._reconnect_neo4j_if_needed()
        if (
            self.config.postgres_configured
            and self._postgres_pool is None
            and time.monotonic() - self._postgres_last_attempt >= self.reconnect_interval
        ):
            await self.connect_postgres()

        async def es_ping(client: Any) -> None:
            if not await client.ping():
                raise ConnectionError("Elasticsearch did not answer ping")

        checks: dict[str, tuple[Any, Any]] = {
            "neo4j": (self._neo4j_driver, lambda c: c.verify_connectivity()),
            "postgres": (self._postgres_pool, lambda c: c.fetchval("SELECT 1")),
            "elasticsearch": (self._elasticsearch_client, es_ping),
            "redis": (self._redis_client, lambda c: c.ping()),
        }
        if not self.config.postgres_configured:
            del checks["postgres"]
        if not self.config.elasticsearch_configured:
            del checks["elasticsearch"]
        if not self.config.redis_configured:
            del checks["redis"]

        async def check(name: str, client: Any, probe: Any) -> bool:
            if client is None:
                return False
            try:
                await asyncio.wait_for(probe(client), timeout)
                return True
            except Exception as e:
                logger.warning(f"{name} health check failed: {str(e) or type(e).__name__}")
                return False

        results = await asyncio.gather(
            *(check(name, client, probe) for name, (client, probe) in checks.items())
        )
        return dict(zip(checks, results, strict=True))

    def get_connection_errors(self) -> dict[str, str]:
        """Get all connection errors.

        Returns:
            Dictionary mapping database name to error message.
        """
        return self._connection_errors.copy()

    @asynccontextmanager
    async def neo4j_session(self) -> AsyncGenerator[Any, None]:
        """Get a Neo4j session as context manager.

        If Neo4j was unavailable, a reconnect is attempted first (throttled).
        """
        await self._reconnect_neo4j_if_needed()
        if not self._neo4j_driver:
            raise RuntimeError("Neo4j not connected")
        async with self._neo4j_driver.session() as session:
            yield session

    @asynccontextmanager
    async def postgres_connection(self) -> AsyncGenerator[Any, None]:
        """Get a PostgreSQL connection from pool as context manager."""
        if not self._postgres_pool:
            raise RuntimeError("PostgreSQL not connected")
        async with self._postgres_pool.acquire() as connection:
            yield connection

    @property
    def elasticsearch(self) -> Any:
        """Get the Elasticsearch client."""
        if not self._elasticsearch_client:
            raise RuntimeError("Elasticsearch not connected")
        return self._elasticsearch_client

    @property
    def redis(self) -> Any:
        """Get the Redis client."""
        if not self._redis_client:
            raise RuntimeError("Redis not connected")
        return self._redis_client

    async def __aenter__(self) -> "DatabaseManager":
        """Async context manager entry."""
        await self.connect_all()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async context manager exit."""
        await self.close_all()


# Global database manager instance
_db_manager: DatabaseManager | None = None


async def get_db() -> DatabaseManager:
    """Get the global database manager instance."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
        await _db_manager.connect_all()
    return _db_manager


def peek_db() -> DatabaseManager | None:
    """Return the global database manager if it exists, without connecting."""
    return _db_manager


async def close_db() -> None:
    """Close the global database manager."""
    global _db_manager
    if _db_manager is not None:
        await _db_manager.close_all()
        _db_manager = None
