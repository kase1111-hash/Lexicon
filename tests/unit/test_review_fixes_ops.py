"""Deployment and test-suite fixes: proxies, pins, versions, compose, run-api-prod."""

import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from uvicorn.main import main as uvicorn_cli

from src.api import middleware as middleware_module
from src.api.main import configure_middleware
from src.config import APIConfig, ErrorTrackingConfig, Settings
from src.utils.db import DatabaseConfig

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION = (REPO_ROOT / "VERSION").read_text().strip()
# A reverse proxy on the host reaches the api container through the published
# port, so the container sees the ls-network gateway as the peer
GATEWAY = "172.19.0.1"


def _minimal_env(**extra: str) -> dict[str, str]:
    """An environment for tools run as subprocesses, without this process's
    settings (conftest points every store at unreachable addresses)."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/")}
    env.update(extra)
    return env


# =============================================================================
# Rate limiting behind a reverse proxy
# =============================================================================


class _MidWindowClock:
    """The rate limiter's `time` module with the wall clock mid-window."""

    def time(self) -> float:
        return 1_800_000_030.0

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


def _uvicorn_command(source: str) -> list[str]:
    """The uvicorn command line the image or the dev override runs."""
    if source == "Dockerfile":
        cmd = next(
            line
            for line in (REPO_ROOT / "Dockerfile").read_text().splitlines()
            if line.startswith("CMD ")
        )
        args = json.loads(cmd.removeprefix("CMD "))
    else:
        override = yaml.safe_load((REPO_ROOT / source).read_text())
        args = shlex.split(override["services"]["api"]["command"])
    assert args[0] == "uvicorn"
    return list(args[1:])


def _served_like(source: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A rate-limited app (1 request per window) wrapped the way uvicorn
    wraps it when started with `source`'s command line."""
    monkeypatch.setattr(middleware_module, "time", _MidWindowClock())
    # Count in process: a Redis the test run may have connected would share
    # counters between tests, which all fall in the same fixed window
    monkeypatch.setattr(middleware_module, "peek_db", lambda: None)
    params = uvicorn_cli.make_context("uvicorn", _uvicorn_command(source)).params
    app = FastAPI()

    @app.get("/api/v1/lsr/search")
    async def search() -> dict:
        return {"ok": True}

    api = APIConfig(_env_file=None, rate_limit_enabled=True, rate_limit_requests=1)
    configure_middleware(app, Settings(_env_file=None, api=api))
    config = uvicorn.Config(
        app,
        proxy_headers=params["proxy_headers"],
        forwarded_allow_ips=params["forwarded_allow_ips"],
        log_config=None,
    )
    config.load()
    return config.loaded_app


def _statuses(app: Any, forwarded_for: list[str]) -> list[int]:
    client = TestClient(app, client=(GATEWAY, 40000))
    return [
        client.get("/api/v1/lsr/search", headers={"X-Forwarded-For": ip}).status_code
        for ip in forwarded_for
    ]


@pytest.mark.parametrize("source", ["Dockerfile", "docker-compose.override.yml"])
def test_clients_behind_a_trusted_proxy_get_their_own_budgets(
    source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", GATEWAY)
    app = _served_like(source, monkeypatch)
    clients = ["203.0.113.1", "203.0.113.2", "203.0.113.1", "203.0.113.3"]
    assert _statuses(app, clients) == [200, 200, 429, 200]


@pytest.mark.parametrize("source", ["Dockerfile", "docker-compose.override.yml"])
def test_forwarded_for_from_an_untrusted_peer_is_ignored(
    source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without FORWARDED_ALLOW_IPS naming the proxy, the peer is the client,
    so a client cannot pick an address to escape its budget."""
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    app = _served_like(source, monkeypatch)
    assert _statuses(app, ["203.0.113.1", "203.0.113.2"]) == [200, 429]


# =============================================================================
# docker compose
# =============================================================================


def _compose_config(tmp_path: Path, settings: dict[str, str], *files: str | Path) -> Any:
    """`docker compose config` for the files, with `settings` as the env file."""
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    env_file = tmp_path / "compose.env"
    env_file.write_text("".join(f"{key}={value}\n" for key, value in settings.items()))
    command = ["docker", "compose", "--project-directory", str(REPO_ROOT)]
    command += ["--env-file", str(env_file)]
    for name in files:
        command += ["-f", str(REPO_ROOT / name)]
    result = subprocess.run(
        [*command, "config", "--format", "json"],
        env=_minimal_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0 and "compose" in result.stderr and "not a docker" in result.stderr:
        pytest.skip("docker compose is not installed")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


_PASSWORDS = {
    "NEO4J_PASSWORD": "neo4j-pw-123",
    "ELASTICSEARCH_PASSWORD": "es-pw",
    "REDIS_PASSWORD": "redis-pw",
    "API_KEY": "key",
}
_PRODUCTION = ("docker-compose.yml", "docker-compose.production.yml")


def test_compose_passes_the_trusted_proxy_addresses(tmp_path: Path) -> None:
    config = _compose_config(tmp_path, _PASSWORDS, *_PRODUCTION)
    assert config["services"]["api"]["environment"]["FORWARDED_ALLOW_IPS"] == "127.0.0.1"
    settings = {**_PASSWORDS, "FORWARDED_ALLOW_IPS": f"{GATEWAY},10.0.0.0/8"}
    config = _compose_config(tmp_path, settings, *_PRODUCTION)
    environment = config["services"]["api"]["environment"]
    assert environment["FORWARDED_ALLOW_IPS"] == f"{GATEWAY},10.0.0.0/8"


class _BumpedImage(ErrorTrackingConfig):
    """The settings of an image built after `make version-bump-*`."""

    app_version: str = "99.0.0"


def test_compose_reports_the_version_of_the_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without APP_VERSION in the env file, the api reports its own version."""
    config = _compose_config(tmp_path, _PASSWORDS, *_PRODUCTION)
    environment = config["services"]["api"]["environment"]
    monkeypatch.setenv("APP_VERSION", environment["APP_VERSION"])
    assert _BumpedImage().app_version == "99.0.0"
    assert ErrorTrackingConfig().app_version == VERSION
    # An explicit value still overrides it
    config = _compose_config(tmp_path, {**_PASSWORDS, "APP_VERSION": "9.9.9"}, *_PRODUCTION)
    assert config["services"]["api"]["environment"]["APP_VERSION"] == "9.9.9"


def test_bundled_neo4j_always_uses_the_neo4j_user(tmp_path: Path) -> None:
    """The neo4j image exits for any other admin user; a NEO4J_USER meant for
    another server must not reach the bundled one or the api's login to it."""
    config = _compose_config(tmp_path, {**_PASSWORDS, "NEO4J_USER": "alice"}, *_PRODUCTION)
    neo4j_env = config["services"]["neo4j"]["environment"]
    api_env = config["services"]["api"]["environment"]
    assert neo4j_env["NEO4J_AUTH"] == "neo4j/neo4j-pw-123"
    assert api_env["NEO4J_USER"] == "neo4j"
    assert api_env["NEO4J_URI"] == "bolt://neo4j:7687"


def _managed_overlay_example() -> str:
    """The overlay that docker-compose.production.yml's comment describes."""
    lines = (REPO_ROOT / "docker-compose.production.yml").read_text().splitlines()
    comment = [line.strip() for line in lines]
    start = comment.index("#   services:")
    snippet = [comment[start]]
    for line in comment[start + 1 :]:
        if not line.startswith("#     "):
            break
        snippet.append(line)
    return "\n".join(line.removeprefix("#   ") for line in snippet) + "\n"


def test_managed_database_overlay_from_the_production_comment_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay = tmp_path / "managed.yml"
    overlay.write_text(_managed_overlay_example())
    example = yaml.safe_load(overlay.read_text().replace("!reset", ""))
    managed = dict(item.split("=", 1) for item in example["services"]["api"]["environment"])
    assert set(managed) == {"NEO4J_URI", "ELASTICSEARCH_URI", "REDIS_URI"}

    # A host-side NEO4J_URI in the env file does not leak into the container
    settings = {**_PASSWORDS, "NEO4J_URI": "bolt://localhost:7687"}
    config = _compose_config(tmp_path, settings, *_PRODUCTION)
    assert config["services"]["api"]["environment"]["NEO4J_URI"] == "bolt://neo4j:7687"

    config = _compose_config(tmp_path, settings, *_PRODUCTION, overlay)
    api = config["services"]["api"]
    assert not api.get("depends_on")  # `up -d api` starts no bundled database
    for key, value in managed.items():
        assert api["environment"][key] == value

    # The api connects to the managed services, not the bundled ones
    for key in list(os.environ):
        if key.startswith(("NEO4J_", "ELASTICSEARCH_", "REDIS_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "missing.env"))
    for key, value in api["environment"].items():
        monkeypatch.setenv(key, value)
    db = DatabaseConfig()
    assert db.neo4j_uri == managed["NEO4J_URI"]
    assert db.neo4j_password == "neo4j-pw-123"
    assert db.elasticsearch_uri == managed["ELASTICSEARCH_URI"]
    assert db.redis_uri == managed["REDIS_URI"]
    assert db.elasticsearch_configured and db.redis_configured


# =============================================================================
# Pinned dependencies
# =============================================================================


def test_sentry_initializes_with_the_installed_packages(tmp_path: Path) -> None:
    """sentry-sdk's integrations must accept the pinned strawberry-graphql and
    starlette; otherwise SENTRY_DSN silently leaves error tracking off. Run in a
    fresh interpreter because initializing patches those libraries."""
    code = (
        "import sys\n"
        # Like the image, which has only requirements.txt and so no jinja2:
        # sentry-sdk releases before 2.56 fail to initialize without it
        "sys.modules['jinja2'] = None\n"
        "from src.utils.error_tracking import SentryIntegration\n"
        "sys.exit(0 if SentryIntegration.init(dsn='http://public@127.0.0.1:9/1') else 1)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=_minimal_env(
            PYTHONPATH=str(REPO_ROOT), ENV_FILE=str(tmp_path / "missing.env"), LOG_LEVEL="INFO"
        ),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# =============================================================================
# The test suite itself
# =============================================================================


def _run_pytest(cwd: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=cwd,
        env=_minimal_env(**env),
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_rate_limit_tests_pass_across_a_minute_boundary(tmp_path: Path) -> None:
    """The limiter's windows follow the wall clock; its tests must not fail
    when a real minute boundary falls inside a burst of requests."""
    plugin = tmp_path / "minute_boundary.py"
    plugin.write_text(
        "import time\n"
        "import pytest\n"
        "_real_time = time.time\n"
        "@pytest.fixture(autouse=True)\n"
        "def _crossing_a_minute(monkeypatch):\n"
        "    now = [(_real_time() // 60 + 1) * 60 - 0.05]\n"
        "    def fake_time():\n"
        "        now[0] += 0.02\n"
        "        return now[0]\n"
        "    monkeypatch.setattr(time, 'time', fake_time)\n"
    )
    result = _run_pytest(
        REPO_ROOT,
        "-q",
        "-p",
        "minute_boundary",
        "tests/integration/test_api_app.py::TestRateLimiting",
        PYTHONPATH=str(tmp_path),
    )
    assert result.returncode == 0, result.stdout[-3000:]


def test_version_tests_pass_after_a_version_bump(tmp_path: Path) -> None:
    """`make version-bump-*` then a release runs the test suite; no test may
    pin the old version."""
    for name in (
        "VERSION",
        "pyproject.toml",
        "pytest.ini",
        "scripts/bump_version.py",
        "tests/__init__.py",
        "tests/conftest.py",
        "tests/unit/__init__.py",
        "tests/unit/test_config.py",
        "tests/unit/test_package_pipeline.py",
    ):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / name, tmp_path / name)
    shutil.copytree(
        REPO_ROOT / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__")
    )
    bump = subprocess.run(
        [sys.executable, "scripts/bump_version.py", "patch"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert bump.returncode == 0, bump.stdout + bump.stderr
    assert (tmp_path / "VERSION").read_text().strip() != VERSION

    result = _run_pytest(
        tmp_path,
        "-q",
        "tests/unit/test_config.py::TestErrorTrackingConfig",
        "tests/unit/test_package_pipeline.py::TestPackageImports",
        "tests/unit/test_package_pipeline.py::TestBuildSystem",
    )
    assert result.returncode == 0, result.stdout[-3000:]


def test_test_ids_are_the_same_in_every_collection() -> None:
    """pytest -n (pytest-xdist) aborts when its workers collect different
    tests, e.g. random values in parametrize ids."""

    def collect() -> list[str]:
        result = _run_pytest(REPO_ROOT, "--collect-only", "-qq", "tests")
        assert result.returncode == 0, result.stdout[-3000:]
        return [line for line in result.stdout.splitlines() if "::" in line]

    first = collect()
    assert first
    assert collect() == first


# =============================================================================
# make run-api-prod
# =============================================================================


class _FakeRedis:
    """A passwordless Redis that answers PING (and anything else with OK)."""

    def __init__(self, host: str, port: int = 0):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen()
        self.port = self._sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._answer, args=(conn,), daemon=True).start()

    @staticmethod
    def _answer(conn: socket.socket) -> None:
        with conn:
            while data := conn.recv(65536):
                lines = data.split(b"\r\n")
                # A command is an array (*N) whose first element ($len, name)
                # is the command name
                names = [lines[i + 2] for i, line in enumerate(lines) if line.startswith(b"*")]
                conn.sendall(
                    b"".join(b"+PONG\r\n" if n.upper() == b"PING" else b"+OK\r\n" for n in names)
                )

    def close(self) -> None:
        self._sock.close()


def _run_api_prod(tmp_path: Path, **env: str) -> str:
    """The uvicorn command `make run-api-prod` would run, with 4 workers asked."""
    if shutil.which("make") is None:
        pytest.skip("make is not installed")
    wrapper = tmp_path / "python"
    wrapper.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-m" ] && [ "$2" = "uvicorn" ]; then shift 2; echo "uvicorn $*"; exit 0; fi\n'
        f'exec "{sys.executable}" "$@"\n'
    )
    wrapper.chmod(0o755)
    result = subprocess.run(
        ["make", "-s", "run-api-prod", f"PYTHON={wrapper}", "API_WORKERS=4"],
        cwd=REPO_ROOT,
        env=_minimal_env(ENV_FILE=str(tmp_path / "missing.env"), **env),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return next(line for line in result.stdout.splitlines() if line.startswith("uvicorn "))


def test_run_api_prod_uses_workers_with_a_configured_redis(tmp_path: Path) -> None:
    redis = _FakeRedis("127.0.0.1")
    try:
        command = _run_api_prod(tmp_path, REDIS_URI=f"redis://127.0.0.1:{redis.port}")
    finally:
        redis.close()
    assert command.endswith("--workers 4")


def test_run_api_prod_runs_one_worker_when_redis_does_not_answer(tmp_path: Path) -> None:
    command = _run_api_prod(tmp_path, REDIS_URI="redis://127.0.0.1:1")
    assert command.endswith("--workers 1")


def test_run_api_prod_ignores_a_redis_the_api_would_not_use(tmp_path: Path) -> None:
    """A Redis answering at REDIS_HOST:6379 is not used by the API unless
    REDIS_URI or REDIS_PASSWORD is set, so the workers would share nothing."""
    try:
        redis = _FakeRedis("127.0.0.2", 6379)
    except OSError as e:
        pytest.skip(f"cannot listen on 127.0.0.2:6379: {e}")
    try:
        with socket.create_connection(("127.0.0.2", 6379), timeout=2) as probe:
            probe.sendall(b"*1\r\n$4\r\nPING\r\n")
            assert probe.recv(64) == b"+PONG\r\n"  # this Redis answers
        command = _run_api_prod(tmp_path, REDIS_HOST="127.0.0.2")
    finally:
        redis.close()
    assert command.endswith("--workers 1")
