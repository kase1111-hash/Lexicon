"""Database connection utilities for all storage backends."""

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any


logger = logging.getLogger(__name__)


class DatabaseConfig:
    """Configuration for database connections loaded from environment."""

    def __init__(self) -> None:
        self.neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.neo4j_user = os.getenv("NEO4J_USER", "neo4j")
        self.neo4j_password = os.getenv("NEO4J_PASSWORD", "password")

        self.postgres_uri = os.getenv(
            "POSTGRES_URI", "postgresql://ls_user:password@localhost/linguistic_stratigraphy"
        )

        self.elasticsearch_uri = os.getenv("ELASTICSEARCH_URI", "http://localhost:9200")

        self.redis_uri = os.getenv("REDIS_URI", "redis://localhost:6379")

        self.milvus_host = os.getenv("MILVUS_HOST", "localhost")
        self.milvus_port = int(os.getenv("MILVUS_PORT", "19530"))


class DatabaseManager:
    """
    Manage connections to all database systems.

    This class provides a unified interface for connecting to and querying
    the various databases used by the linguistic stratigraphy system:
    - Neo4j: Graph storage for LSRs and relationships
    - PostgreSQL: Relational metadata
    - Elasticsearch: Full-text search
    - Redis: Caching
    - Milvus: Vector storage for embeddings
    """

    def __init__(self, config: DatabaseConfig | None = None):
        """Initialize the database manager."""
        self.config = config or DatabaseConfig()

        self._neo4j_driver: Any = None
        self._postgres_pool: Any = None
        self._elasticsearch_client: Any = None
        self._redis_client: Any = None
        self._milvus_client: Any = None

        self._connected = False

    async def connect_all(self) -> None:
        """Connect to all database systems."""
        await self.connect_neo4j()
        await self.connect_postgres()
        await self.connect_elasticsearch()
        await self.connect_redis()
        await self.connect_milvus()
        self._connected = True
        logger.info("Connected to all databases")

    async def connect_neo4j(self) -> None:
        """Connect to Neo4j graph database."""
        try:
            from neo4j import AsyncGraphDatabase

            self._neo4j_driver = AsyncGraphDatabase.driver(
                self.config.neo4j_uri,
                auth=(self.config.neo4j_user, self.config.neo4j_password),
            )
            # Verify connection
            async with self._neo4j_driver.session() as session:
                await session.run("RETURN 1")
            logger.info("Connected to Neo4j")
        except ImportError:
            logger.warning("neo4j package not installed, Neo4j connection disabled")
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j: {e}")

    async def connect_postgres(self) -> None:
        """Connect to PostgreSQL database."""
        try:
            import asyncpg

            self._postgres_pool = await asyncpg.create_pool(
                self.config.postgres_uri,
                min_size=5,
                max_size=20,
            )
            logger.info("Connected to PostgreSQL")
        except ImportError:
            logger.warning("asyncpg package not installed, PostgreSQL connection disabled")
        except Exception as e:
            logger.error(f"Failed to connect to PostgreSQL: {e}")

    async def connect_elasticsearch(self) -> None:
        """Connect to Elasticsearch."""
        try:
            from elasticsearch import AsyncElasticsearch

            self._elasticsearch_client = AsyncElasticsearch([self.config.elasticsearch_uri])
            # Verify connection
            await self._elasticsearch_client.info()
            logger.info("Connected to Elasticsearch")
        except ImportError:
            logger.warning("elasticsearch package not installed, Elasticsearch connection disabled")
        except Exception as e:
            logger.error(f"Failed to connect to Elasticsearch: {e}")

    async def connect_redis(self) -> None:
        """Connect to Redis."""
        try:
            import redis.asyncio as redis

            self._redis_client = redis.from_url(self.config.redis_uri)
            # Verify connection
            await self._redis_client.ping()
            logger.info("Connected to Redis")
        except ImportError:
            logger.warning("redis package not installed, Redis connection disabled")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")

    async def connect_milvus(self) -> None:
        """Connect to Milvus vector database."""
        try:
            from pymilvus import connections

            connections.connect(
                alias="default",
                host=self.config.milvus_host,
                port=self.config.milvus_port,
            )
            logger.info("Connected to Milvus")
        except ImportError:
            logger.warning("pymilvus package not installed, Milvus connection disabled")
        except Exception as e:
            logger.error(f"Failed to connect to Milvus: {e}")

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
            await self._redis_client.close()
            logger.info("Closed Redis connection")

        if self._milvus_client:
            from pymilvus import connections

            connections.disconnect("default")
            logger.info("Closed Milvus connection")

        self._connected = False
        logger.info("Closed all database connections")

    @asynccontextmanager
    async def neo4j_session(self) -> AsyncGenerator[Any, None]:
        """Get a Neo4j session as context manager."""
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

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
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


async def close_db() -> None:
    """Close the global database manager."""
    global _db_manager
    if _db_manager is not None:
        await _db_manager.close_all()
        _db_manager = None
