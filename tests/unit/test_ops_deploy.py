"""Deployment, CI and packaging files stay consistent with how the code works."""

import ast
import json
import re
from pathlib import Path
from typing import Any

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(name: str) -> Any:
    return yaml.safe_load((REPO_ROOT / name).read_text())


def _env_list(service: dict[str, Any]) -> dict[str, str]:
    env = service.get("environment", [])
    if isinstance(env, dict):
        return {k: str(v) for k, v in env.items()}
    return dict(item.split("=", 1) for item in env)


class TestCompose:
    @pytest.fixture
    def compose(self) -> Any:
        return _load_yaml("docker-compose.yml")

    def test_postgres_is_optional(self, compose: Any) -> None:
        services = compose["services"]
        assert services["postgres"]["profiles"] == ["postgres"]
        assert "postgres" not in services["api"]["depends_on"]
        assert "POSTGRES_URI" not in _env_list(services["api"])
        # Rendering must not demand a PostgreSQL password when the profile is off
        text = (REPO_ROOT / "docker-compose.yml").read_text()
        assert "POSTGRES_PASSWORD:?" not in text

    def test_api_waits_only_for_neo4j(self, compose: Any) -> None:
        depends = compose["services"]["api"]["depends_on"]
        assert depends["neo4j"]["condition"] == "service_healthy"
        for optional in ("elasticsearch", "redis"):
            assert depends[optional]["required"] is False
            assert depends[optional]["condition"] == "service_started"

    def test_every_port_is_local(self, compose: Any) -> None:
        for name, service in compose["services"].items():
            for port in service.get("ports", []):
                assert str(port).startswith("127.0.0.1:"), (name, port)

    def test_neo4j_bounds_runaway_queries(self, compose: Any) -> None:
        env = _env_list(compose["services"]["neo4j"])
        assert env["NEO4J_db_transaction_timeout"]
        assert env["NEO4J_db_memory_transaction_max"]
        assert not any("dbms_memory" in key for key in env)  # deprecated in Neo4j 5

    def test_passwords_reach_the_code_unencoded(self, compose: Any) -> None:
        # The code URL-encodes passwords when it builds the URIs; compose must not
        # splice raw passwords into URIs itself
        env = _env_list(compose["services"]["api"])
        assert not any(key.endswith("_URI") and "${" in value for key, value in env.items())
        assert env["ELASTICSEARCH_HOST"] == "elasticsearch"
        assert env["REDIS_HOST"] == "redis"

    def test_env_file_settings_reach_the_api(self, compose: Any) -> None:
        # The container never reads an env file, so settings that the config/
        # templates document for compose must be passed through explicitly
        env = _env_list(compose["services"]["api"])
        for key in (
            "API_KEY",
            "CORS_ORIGINS",
            "RATE_LIMIT_ENABLED",
            "RATE_LIMIT_REQUESTS",
            "RATE_LIMIT_WINDOW_SECONDS",
            "SENTRY_DSN",
            "GRAPH_QUERY_ENABLED",
        ):
            assert env[key].startswith(f"${{{key}:-"), key


class TestProductionOverlay:
    @pytest.fixture
    def overlay(self) -> Any:
        return _load_yaml("docker-compose.production.yml")

    def test_runs_the_published_image(self, overlay: Any) -> None:
        repository, _, tag = overlay["services"]["api"]["image"].partition(":")
        # Registry names must be lowercase; this is what release.yml pushes
        assert repository == "ghcr.io/kase1111-hash/lexicon"
        assert tag == "${VERSION:-latest}"

    def test_api_key_is_mandatory(self, overlay: Any) -> None:
        env = _env_list(overlay["services"]["api"])
        assert env["API_KEY"].startswith("${API_KEY:?")
        assert env["ENVIRONMENT"] == "production"

    def test_no_unimplemented_settings(self, overlay: Any) -> None:
        text = (REPO_ROOT / "docker-compose.production.yml").read_text()
        assert "SECRETS_" not in text
        es_env = _env_list(overlay["services"]["elasticsearch"])
        assert "xpack.security.http.ssl.enabled" not in es_env  # no TLS without certificates
        assert "curl" not in json.dumps(overlay["services"]["api"]["healthcheck"])


class TestImageAndDependencies:
    def test_image_uses_the_tested_python(self) -> None:
        froms = re.findall(r"^FROM\s+(\S+)", (REPO_ROOT / "Dockerfile").read_text(), re.M)
        assert froms and all(ref.startswith("python:3.11-") for ref in froms)
        dependabot = _load_yaml(".github/dependabot.yml")
        docker = next(u for u in dependabot["updates"] if u["package-ecosystem"] == "docker")
        assert {"dependency-name": "python"}.items() <= docker["ignore"][0].items()

    def test_no_unused_runtime_dependencies(self) -> None:
        requirements = (REPO_ROOT / "requirements.txt").read_text().lower()
        for unused in ("pywikibot", "lxml", "tqdm", "email-validator"):
            assert not re.search(rf"^{unused}\b", requirements, re.M), unused

        imported: set[str] = set()
        for path in (REPO_ROOT / "src").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
        assert imported, "no imports found under src/"
        assert not imported & {"pywikibot", "lxml", "tqdm", "email_validator"}


class TestCI:
    def test_workflows_do_not_hide_failures(self) -> None:
        for workflow in ("ci.yml", "release.yml"):
            text = (REPO_ROOT / ".github" / "workflows" / workflow).read_text()
            assert "|| true" not in text, workflow

    def test_db_backed_tests_run_in_ci(self) -> None:
        jobs = _load_yaml(".github/workflows/ci.yml")["jobs"]
        assert jobs["test-db"]["services"]["neo4j"]["image"] == "neo4j:5.9"
        assert jobs["test-db"]["env"]["TEST_NEO4J_URI"].startswith("bolt://")
        assert any("docker build" in step.get("run", "") for step in jobs["docker"]["steps"])

    def test_release_pushes_a_lowercase_image(self) -> None:
        text = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text()
        assert "${GITHUB_REPOSITORY,,}" in text
        assert "ghcr.io/${{ github.repository }}" not in text


class TestPostgresSchemaSource:
    def test_alembic_is_the_only_schema(self) -> None:
        assert "prepend_sys_path = ." in (REPO_ROOT / "alembic.ini").read_text()
        assert "DatabaseConfig" in (REPO_ROOT / "migrations" / "env.py").read_text()
        init_sql = (REPO_ROOT / "scripts" / "init-db.sql").read_text().upper()
        assert "CREATE TABLE" not in init_sql
        setup = (REPO_ROOT / "scripts" / "setup_databases.sh").read_text()
        assert "docker-compose " not in setup and "milvus" not in setup
        assert "CREATE TABLE" not in setup.upper()


class TestSampleCorpus:
    def test_corpus_documents_are_dated(self) -> None:
        corpus = REPO_ROOT / "data" / "corpus"
        documents = sorted(corpus.glob("*.txt"))
        assert len(documents) >= 3
        for document in documents:
            meta = json.loads(document.with_suffix(".json").read_text())
            assert meta["title"]
            assert isinstance(meta["date"], int)

    def test_adapter_reads_the_sample(self) -> None:
        from src.adapters.corpus import CorpusAdapter

        adapter = CorpusAdapter(corpus_dir=REPO_ROOT / "data" / "corpus")
        adapter.connect()
        entries = {e.form: e for e in adapter.fetch_all()}
        adapter.disconnect()
        assert entries["microscope"].date_attested == 1898
        assert entries["beginning"].date_attested == 1611
        assert all(e.date_attested for e in entries.values())
