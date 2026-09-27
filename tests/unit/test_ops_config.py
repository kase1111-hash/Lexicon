"""Operational configuration: optional PostgreSQL, derived URIs, /health, ls-api."""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import src.api.main as api_main
from src.config import Settings
from src.utils.db import DatabaseConfig, DatabaseManager

_DB_PREFIXES = ("NEO4J_", "POSTGRES_", "REDIS_", "ELASTICSEARCH_")


def _clean_db_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """The current environment without database settings and without a .env."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(_DB_PREFIXES)}
    env["ENV_FILE"] = str(tmp_path / "missing.env")
    env.update(extra)
    return env


class TestDatabaseConfig:
    def test_postgres_not_configured_by_default(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, _clean_db_env(tmp_path), clear=True):
            config = DatabaseConfig()
        assert config.postgres_configured is False
        # Migrations and the loader still get a usable URI
        assert config.postgres_uri.startswith("postgresql://ls_user:")

    def test_compose_credentials_do_not_enable_postgres(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("POSTGRES_PASSWORD=pw\nPOSTGRES_DB=lex\n")
        with patch.dict(os.environ, _clean_db_env(tmp_path, ENV_FILE=str(env_file)), clear=True):
            config = DatabaseConfig()
        assert config.postgres_configured is False
        assert config.postgres_uri == "postgresql://ls_user:pw@localhost:5432/lex"

    def test_postgres_uri_in_env_file_enables_postgres(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("POSTGRES_URI=postgresql://u:p@db:5432/x\n")
        with patch.dict(os.environ, _clean_db_env(tmp_path, ENV_FILE=str(env_file)), clear=True):
            config = DatabaseConfig()
        assert config.postgres_configured is True
        assert config.postgres_uri == "postgresql://u:p@db:5432/x"

    def test_derived_uris_encode_passwords_and_use_hosts(self, tmp_path: Path) -> None:
        secret = "Abc#12/x@y"
        env = _clean_db_env(
            tmp_path,
            ELASTICSEARCH_PASSWORD=secret,
            ELASTICSEARCH_HOST="elasticsearch",
            REDIS_PASSWORD=secret,
            REDIS_HOST="redis",
            POSTGRES_PASSWORD=secret,
            POSTGRES_HOST="postgres",
            POSTGRES_PORT="5433",
        )
        with patch.dict(os.environ, env, clear=True):
            config = DatabaseConfig()
        encoded = "Abc%2312%2Fx%40y"
        assert config.elasticsearch_uri == f"http://elastic:{encoded}@elasticsearch:9200"
        assert config.redis_uri == f"redis://:{encoded}@redis:6379"
        assert config.postgres_uri == (
            f"postgresql://ls_user:{encoded}@postgres:5433/linguistic_stratigraphy"
        )

        from urllib.parse import unquote, urlsplit

        for uri, host in (
            (config.elasticsearch_uri, "elasticsearch"),
            (config.redis_uri, "redis"),
            (config.postgres_uri, "postgres"),
        ):
            parts = urlsplit(uri)
            assert parts.hostname == host
            assert unquote(parts.password or "") == secret


class _Answering:
    async def verify_connectivity(self) -> None:
        return None

    async def ping(self) -> bool:
        return True


class TestOptionalPostgres:
    def _manager(self, tmp_path: Path, **env: str) -> DatabaseManager:
        with patch.dict(os.environ, _clean_db_env(tmp_path, **env), clear=True):
            return DatabaseManager()

    def test_connect_all_skips_unconfigured_postgres(self, tmp_path: Path) -> None:
        manager = self._manager(tmp_path, ELASTICSEARCH_PASSWORD="x", REDIS_PASSWORD="y")
        calls: list[str] = []

        async def fake(name: str) -> bool:
            calls.append(name)
            return False

        async def neo4j() -> bool:
            return await fake("neo4j")

        async def postgres() -> bool:
            return await fake("postgres")

        async def elasticsearch() -> bool:
            return await fake("elasticsearch")

        async def redis() -> bool:
            return await fake("redis")

        manager.connect_neo4j = neo4j  # type: ignore[method-assign]
        manager.connect_postgres = postgres  # type: ignore[method-assign]
        manager.connect_elasticsearch = elasticsearch  # type: ignore[method-assign]
        manager.connect_redis = redis  # type: ignore[method-assign]
        asyncio.run(manager.connect_all())
        assert calls == ["neo4j", "elasticsearch", "redis"]
        status = manager.get_connection_status()
        assert status["postgres"]["configured"] is False
        assert status["neo4j"]["configured"] is True

    def test_neo4j_only_setup_connects_and_probes_only_neo4j(self, tmp_path: Path) -> None:
        """Without ES/Redis settings they are not_configured, not 'down'."""
        manager = self._manager(tmp_path)
        calls: list[str] = []

        async def connect(name: str) -> bool:
            calls.append(name)
            return True

        manager.connect_neo4j = lambda: connect("neo4j")  # type: ignore[method-assign]
        manager.connect_elasticsearch = lambda: connect("elasticsearch")  # type: ignore[method-assign]
        manager.connect_redis = lambda: connect("redis")  # type: ignore[method-assign]
        asyncio.run(manager.connect_all())
        assert calls == ["neo4j"]
        status = manager.get_connection_status()
        assert status["elasticsearch"]["configured"] is False
        assert status["redis"]["configured"] is False
        manager._neo4j_driver = _Answering()
        assert asyncio.run(manager.ping(timeout=0.5)) == {"neo4j": True}

    def test_ping_leaves_out_unconfigured_postgres(self, tmp_path: Path) -> None:
        manager = self._manager(tmp_path, ELASTICSEARCH_PASSWORD="x", REDIS_PASSWORD="y")
        manager._neo4j_driver = _Answering()
        manager._elasticsearch_client = _Answering()
        manager._redis_client = _Answering()
        assert asyncio.run(manager.ping(timeout=0.5)) == {
            "neo4j": True,
            "elasticsearch": True,
            "redis": True,
        }

    def test_ping_retries_configured_postgres(self, tmp_path: Path) -> None:
        manager = self._manager(tmp_path, POSTGRES_URI="postgresql://u:p@127.0.0.1:1/x")
        attempts: list[int] = []

        async def failing_connect() -> bool:
            attempts.append(1)
            return False

        manager.connect_postgres = failing_connect  # type: ignore[method-assign]
        manager._neo4j_driver = _Answering()
        result = asyncio.run(manager.ping(timeout=0.5))
        assert attempts == [1]
        assert result["postgres"] is False


class _FakePingDB:
    def __init__(self, reachable: dict[str, bool]):
        self.reachable = reachable

    async def ping(self) -> dict[str, bool]:
        return dict(self.reachable)


class TestHealthReportsUnconfiguredPostgres:
    def _health(self, monkeypatch: pytest.MonkeyPatch, reachable: dict[str, bool]) -> Any:
        async def fake_get_db() -> _FakePingDB:
            return _FakePingDB(reachable)

        monkeypatch.setattr(api_main, "get_db", fake_get_db)
        return TestClient(api_main.app).get("/health")

    def test_not_configured_is_not_degraded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        response = self._health(monkeypatch, {"neo4j": True, "elasticsearch": True, "redis": True})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy"
        assert body["databases"] == {
            "neo4j": "connected",
            "postgres": "not_configured",
            "elasticsearch": "connected",
            "redis": "connected",
        }

    def test_configured_postgres_down_is_degraded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        response = self._health(
            monkeypatch,
            {"neo4j": True, "postgres": False, "elasticsearch": True, "redis": True},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
        assert response.json()["databases"]["postgres"] == "disconnected"


class TestProductionValidation:
    def _errors(self, **env: str) -> list[str]:
        base = {
            "ENVIRONMENT": "production",
            "API_KEY": "k" * 32,
            "NEO4J_PASSWORD": "neo4j-pw",
            "CORS_ORIGINS": "https://example.org",
            "RATE_LIMIT_ENABLED": "true",
        }
        clean = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(_DB_PREFIXES) and k not in {"API_KEY", "DEBUG", "CORS_ORIGINS"}
        }
        with patch.dict(os.environ, {**clean, **base, **env}, clear=True):
            return Settings(_env_file=None).validate_required_for_production()

    def test_postgres_password_not_required_without_postgres(self) -> None:
        assert self._errors() == []

    def test_password_inside_postgres_uri_is_enough(self) -> None:
        assert self._errors(POSTGRES_URI="postgresql://u:secret@db/x") == []

    def test_configured_postgres_without_password_is_rejected(self) -> None:
        errors = self._errors(POSTGRES_URI="postgresql://u@db/x")
        assert any("POSTGRES_URI" in e for e in errors)


class TestLsApiEntryPoint:
    def _run(self, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> dict[str, Any]:
        import uvicorn

        captured: dict[str, Any] = {}

        def fake_run(app: str, **kwargs: Any) -> None:
            captured.update(app=app, **kwargs)

        monkeypatch.setattr(uvicorn, "run", fake_run)
        monkeypatch.setattr(sys, "argv", ["ls-api", *argv])
        api_main.run()
        return captured

    def test_uses_settings_without_reload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(api_main.settings.api, "api_host", "127.0.0.1")
        monkeypatch.setattr(api_main.settings.api, "api_port", 8105)
        monkeypatch.setattr(api_main.settings.api, "api_workers", 2)
        captured = self._run(monkeypatch, [])
        assert captured == {
            "app": "src.api.main:app",
            "host": "127.0.0.1",
            "port": 8105,
            "workers": 2,
            "reload": False,
        }

    def test_reload_is_opt_in(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = self._run(monkeypatch, ["--reload", "--port", "9000"])
        assert captured["reload"] is True
        assert captured["workers"] is None
        assert captured["port"] == 9000


class _LateStoresDB:
    """Just enough of DatabaseManager for _connect_late_stores."""

    def __init__(self, es_configured: bool, es_up_after: int):
        self.config = type(
            "Cfg",
            (),
            {
                "elasticsearch_configured": es_configured,
                "redis_configured": True,
                "elasticsearch_uri": "http://127.0.0.1:9",
                "redis_uri": "redis://127.0.0.1:9",
            },
        )()
        self.es_up_after = es_up_after
        self.es_attempts = 0
        self.es_connected = False

    def get_connection_status(self) -> dict[str, dict[str, Any]]:
        return {
            "elasticsearch": {"connected": self.es_connected},
            "redis": {"connected": True},
        }

    async def connect_redis(self, quiet: bool = False) -> bool:
        return True

    async def connect_elasticsearch(self, quiet: bool = False) -> bool:
        assert quiet
        self.es_attempts += 1
        self.es_connected = self.es_attempts >= self.es_up_after
        return self.es_connected


class TestLateOptionalStores:
    """The API connects Elasticsearch/Redis that come up after it started."""

    def _run(self, monkeypatch: pytest.MonkeyPatch, db: _LateStoresDB) -> list[str]:
        prepared: list[str] = []

        async def port_open(uri: str, default_port: int) -> bool:
            return True

        async def prepare(manager: Any) -> None:
            prepared.append("index")

        async def wait_for_cluster(manager: Any) -> None:
            prepared.append("ready")

        monkeypatch.setattr(api_main, "_LATE_CONNECT_INTERVAL_SECONDS", 0)
        monkeypatch.setattr(api_main, "_port_open", port_open)
        monkeypatch.setattr(api_main, "_wait_for_search_cluster", wait_for_cluster)
        monkeypatch.setattr(api_main, "_prepare_search_index", prepare)
        asyncio.run(api_main._connect_late_stores(db))  # type: ignore[arg-type]
        return prepared

    def test_elasticsearch_connected_when_it_comes_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = _LateStoresDB(es_configured=True, es_up_after=3)
        assert self._run(monkeypatch, db) == ["ready", "index"]
        assert db.es_attempts == 3

    def test_unconfigured_elasticsearch_is_not_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = _LateStoresDB(es_configured=False, es_up_after=1)
        assert self._run(monkeypatch, db) == []
        assert db.es_attempts == 0
