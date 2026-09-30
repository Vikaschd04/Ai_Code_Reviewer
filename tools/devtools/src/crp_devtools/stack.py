"""Local stacks: the persistent development stack (``make dev``) and the isolated E2E stack."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx

from crp_core.local_secrets import generate_token, write_secret_file
from crp_devtools.dbtasks import migrate_and_provision, settings_from_env
from crp_devtools.infra import (
    LOOPBACK,
    InfraError,
    PostgresCluster,
    TemporalDevServer,
    free_port,
    port_open,
)
from crp_devtools.localenv import (
    DATABASE_NAME,
    DevPorts,
    child_env,
    dev_postgres,
    dev_service_env,
    dev_temporal,
    ensure_secrets,
    service_env,
    write_env_file,
)
from crp_devtools.paths import DevPaths
from crp_devtools.supervisor import ServiceSpec, Supervisor
from crp_devtools.testing.fake_ai import MODEL as FAKE_AI_MODEL
from crp_devtools.testing.fake_ai import FakeAiProvider
from crp_devtools.testing.fixture_projects import prepare_fixture, zip_directory

WEB_PACKAGE = "@crp/web"


def pnpm() -> str:
    exe = shutil.which("pnpm")
    if exe is None:
        raise InfraError("pnpm not found on PATH; install pnpm 11 (see docs/INSTALLATION.md)")
    return exe


def wait_http(url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise InfraError(f"{url} did not respond within {timeout:g}s")


# -- persistent development infrastructure ------------------------------------------------------


def bootstrap_local(paths: DevPaths, ports: DevPorts) -> list[str]:
    created = ensure_secrets(paths)
    cluster = dev_postgres(paths, ports)
    if not cluster.initialized:
        cluster.init()
        created.append("postgres cluster")
    write_env_file(paths, dev_service_env(paths, ports))
    return created


def infra_up(paths: DevPaths, ports: DevPorts) -> tuple[bool, bool]:
    """Start dev PostgreSQL and Temporal if needed. Returns which ones this call started."""
    bootstrap_local(paths, ports)
    cluster = dev_postgres(paths, ports)
    started_pg = not cluster.running()
    cluster.start()
    if not cluster.database_exists(DATABASE_NAME):
        cluster.create_database(DATABASE_NAME)
    temporal = dev_temporal(paths, ports)
    started_temporal = not temporal.running()
    if started_temporal:
        temporal.start(detach=True)
    return started_pg, started_temporal


def infra_down(
    paths: DevPaths, ports: DevPorts, *, postgres: bool = True, temporal: bool = True
) -> None:
    if temporal:
        dev_temporal(paths, ports).stop()
    if postgres and paths.postgres_password_file.exists():
        dev_postgres(paths, ports).stop()


def infra_status(paths: DevPaths, ports: DevPorts) -> dict[str, str]:
    cluster_state = "not initialized"
    if paths.postgres_password_file.exists():
        cluster = dev_postgres(paths, ports)
        if cluster.initialized:
            cluster_state = (
                f"running on {LOOPBACK}:{ports.postgres}" if cluster.running() else "stopped"
            )
    temporal = dev_temporal(paths, ports)
    return {
        "postgres": cluster_state,
        "temporal": f"running on {temporal.address}" if temporal.running() else "stopped",
        "api": f"listening on {LOOPBACK}:{ports.api}" if port_open(ports.api) else "stopped",
        "web": f"listening on {LOOPBACK}:{ports.web}" if port_open(ports.web) else "stopped",
    }


def _service_specs(
    repo: Path, env: dict[str, str], *, api_port: int, web_args: list[str], logs: Path | None
) -> list[ServiceSpec]:
    full_env = child_env(env)
    web_env = child_env({"CRP_API_ORIGIN": f"http://{LOOPBACK}:{api_port}"})
    return [
        ServiceSpec(
            "api", [sys.executable, "-m", "crp_api"], full_env, repo, logs and logs / "api.log"
        ),
        ServiceSpec(
            "worker",
            [sys.executable, "-m", "crp_worker"],
            full_env,
            repo,
            logs and logs / "worker.log",
        ),
        ServiceSpec("web", web_args, web_env, repo, logs and logs / "web.log"),
    ]


def run_dev(paths: DevPaths, ports: DevPorts, *, keep_infra: bool) -> int:
    started_pg, started_temporal = infra_up(paths, ports)
    env = dev_service_env(paths, ports)
    revision, _ = migrate_and_provision(settings_from_env(env))
    print(f"crp-dev: database migrated to {revision}")
    supervisor = Supervisor()
    web_args = [
        pnpm(),
        "--filter",
        WEB_PACKAGE,
        "exec",
        "vite",
        "--host",
        LOOPBACK,
        "--port",
        str(ports.web),
        "--strictPort",
    ]
    try:
        for spec in _service_specs(
            paths.repo, env, api_port=ports.api, web_args=web_args, logs=None
        ):
            supervisor.start(spec)
        wait_http(f"http://{LOOPBACK}:{ports.api}/v1/health/live", 60)
        print(
            "\ncrp-dev: services started (loopback only)\n"
            f"  Web UI       http://{LOOPBACK}:{ports.web}\n"
            f"  API docs     http://{LOOPBACK}:{ports.api}/v1/docs\n"
            f"  Temporal UI  http://{LOOPBACK}:{ports.temporal_ui}\n"
            f"  Sign-in token: {paths.token_file.relative_to(paths.repo)} (crp-dev token --show)\n"
            "  Press Ctrl-C to stop.\n",
            flush=True,
        )
        name, code = supervisor.wait_any()
        if name != "interrupt":
            print(f"crp-dev: service '{name}' exited with code {code}; stopping the stack")
        return 0 if name == "interrupt" else (code or 1)
    finally:
        supervisor.stop_all()
        if not keep_infra:
            infra_down(paths, ports, postgres=started_pg, temporal=started_temporal)


# -- isolated E2E stack -------------------------------------------------------------------------


def _e2e_fixtures(directory: Path) -> dict[str, Path]:
    """Synthetic upload archives for browser tests (fixture projects and a traversal attack)."""
    directory.mkdir(parents=True)
    archives: dict[str, Path] = {}
    for key, name in (
        ("seeded", "seeded-mixed"),
        ("security", "security-mixed"),
        ("graph", "graph-mixed"),
    ):
        archives[key] = directory / f"{name}.zip"
        archives[key].write_bytes(zip_directory(prepare_fixture(name, directory / name)))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("src/Main.java", "class Main {}")
        zf.writestr("../../escape.sh", "echo escaped")
    malicious = directory / "traversal.zip"
    malicious.write_bytes(buffer.getvalue())
    return {**archives, "malicious": malicious}


def run_e2e(paths: DevPaths, playwright_args: list[str]) -> int:
    """Start throwaway PostgreSQL/Temporal/API/worker/web instances and run Playwright."""
    work = Path(tempfile.mkdtemp(prefix="crp-e2e-"))
    logs = work / "logs"
    logs.mkdir()
    cluster = PostgresCluster(
        data_dir=work / "pg",
        port=free_port(),
        password=generate_token(),
        log_file=logs / "postgres.log",
    )
    temporal = TemporalDevServer(port=free_port(), log_file=logs / "temporal.log")
    supervisor = Supervisor(echo=False)
    fake_ai_key = generate_token()
    fake_ai = FakeAiProvider(fake_ai_key)  # labelled test double: no real model is called
    exit_code = 1
    try:
        fake_ai.start()
        cluster.init()
        cluster.start()
        cluster.create_database(DATABASE_NAME)
        temporal.start(detach=False)
        token_file = work / "secrets" / "token"
        write_secret_file(token_file, generate_token())
        api_port, web_port = free_port(), free_port()
        env = service_env(
            repo=paths.repo,
            token_file=token_file,
            database_url=cluster.url(DATABASE_NAME),
            temporal_address=temporal.address,
            api_port=api_port,
            web_origins=(f"http://{LOOPBACK}:{web_port}",),
            artifacts=work / "artifacts",
            work_root=work / "scan-work",
            log_format="json",
        )
        env.update(
            {
                "CRP_AI_PROVIDER": "openai_compatible",
                "CRP_AI_MODEL": FAKE_AI_MODEL,
                "CRP_AI_BASE_URL": fake_ai.base_url,
                "CRP_AI_API_KEY": fake_ai_key,
            }
        )
        migrate_and_provision(settings_from_env(env))
        subprocess.run([pnpm(), "--filter", WEB_PACKAGE, "build"], cwd=paths.repo, check=True)  # noqa: S603
        web_args = [
            pnpm(),
            "--filter",
            WEB_PACKAGE,
            "exec",
            "vite",
            "preview",
            "--host",
            LOOPBACK,
            "--port",
            str(web_port),
            "--strictPort",
        ]
        for spec in _service_specs(
            paths.repo, env, api_port=api_port, web_args=web_args, logs=logs
        ):
            supervisor.start(spec)
        wait_http(f"http://{LOOPBACK}:{api_port}/v1/health/live", 60)
        wait_http(f"http://{LOOPBACK}:{web_port}/", 60)
        fixtures = _e2e_fixtures(work / "fixtures")
        e2e_env = child_env(
            {
                "CRP_E2E_BASE_URL": f"http://{LOOPBACK}:{web_port}",
                "CRP_E2E_TOKEN_FILE": str(token_file),
                "CRP_E2E_SEEDED_ZIP": str(fixtures["seeded"]),
                "CRP_E2E_SECURITY_ZIP": str(fixtures["security"]),
                "CRP_E2E_GRAPH_ZIP": str(fixtures["graph"]),
                "CRP_E2E_MALICIOUS_ZIP": str(fixtures["malicious"]),
            }
        )
        result = subprocess.run(  # noqa: S603
            [pnpm(), "--filter", WEB_PACKAGE, "exec", "playwright", "test", *playwright_args],
            cwd=paths.repo,
            env=e2e_env,
            check=False,
        )
        exit_code = result.returncode
    finally:
        supervisor.stop_all()
        fake_ai.stop()
        temporal.stop()
        cluster.stop()
        if exit_code != 0:
            kept = paths.logs / f"e2e-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
            kept.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(logs, kept)
            print(f"crp-dev: E2E failed; service logs copied to {kept.relative_to(paths.repo)}")
        shutil.rmtree(work, ignore_errors=True)
    return exit_code


# -- packaging ------------------------------------------------------------------------------------


def package(paths: DevPaths) -> Path:
    dist = paths.repo / "dist"
    shutil.rmtree(dist, ignore_errors=True)
    uv = shutil.which("uv")
    if uv is None:
        raise InfraError("uv not found on PATH")
    subprocess.run(  # noqa: S603
        [uv, "build", "--all-packages", "--out-dir", str(dist / "python")],
        cwd=paths.repo,
        check=True,
    )
    subprocess.run([pnpm(), "--filter", WEB_PACKAGE, "build"], cwd=paths.repo, check=True)  # noqa: S603
    shutil.copytree(paths.repo / "apps" / "web" / "dist", dist / "web")
    manifest = {
        str(path.relative_to(dist)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(dist.rglob("*"))
        if path.is_file()
    }
    (dist / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return dist
