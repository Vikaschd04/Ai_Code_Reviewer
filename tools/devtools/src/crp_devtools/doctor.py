"""``crp-dev doctor``: verify prerequisites, local secrets and live service state."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

import httpx

from crp_core.config import Settings
from crp_core.db.migrate import database_revision, head_revision
from crp_core.local_secrets import SecretFileError, read_secret_file
from crp_core.workflows.gateway import WorkflowUnavailableError
from crp_core.workflows.temporal import TemporalWorkflowGateway
from crp_devtools.dbtasks import settings_from_env
from crp_devtools.infra import InfraError, find_pg_bin_dir, find_temporal_cli, port_open
from crp_devtools.localenv import DevPorts, dev_service_env
from crp_devtools.paths import DevPaths


class Level(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "FAIL"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class Finding:
    level: Level
    item: str
    detail: str


def _version(args: list[str]) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603 - fixed executables, argument arrays
            args, capture_output=True, text=True, timeout=20, check=False
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    output = (result.stdout or result.stderr).strip().splitlines()
    return output[0] if result.returncode == 0 and output else None


def _tool(name: str, args: list[str]) -> Finding:
    exe = shutil.which(args[0])
    if exe is None:
        return Finding(Level.FAIL, name, f"'{args[0]}' not found on PATH")
    version = _version([exe, *args[1:]])
    return Finding(Level.OK if version else Level.FAIL, name, version or "version check failed")


def check_prerequisites() -> list[Finding]:
    findings = [
        Finding(
            Level.OK if sys.version_info[:2] == (3, 14) else Level.FAIL,
            "python",
            sys.version.split()[0] + ("" if sys.version_info[:2] == (3, 14) else " (need 3.14)"),
        ),
        _tool("uv", ["uv", "--version"]),
        _tool("node", ["node", "--version"]),
        _tool("pnpm", ["pnpm", "--version"]),
    ]
    try:
        pg_bin = find_pg_bin_dir()
        findings.append(
            Finding(
                Level.OK, "postgresql", _version([str(pg_bin / "postgres"), "--version"]) or "?"
            )
        )
    except InfraError as exc:
        findings.append(Finding(Level.FAIL, "postgresql", str(exc)))
    try:
        cli = find_temporal_cli()
        findings.append(Finding(Level.OK, "temporal-cli", _version([str(cli), "--version"]) or "?"))
    except InfraError as exc:
        findings.append(Finding(Level.FAIL, "temporal-cli", str(exc)))
    return findings


def check_secrets(paths: DevPaths) -> list[Finding]:
    findings = []
    for label, path in (
        ("api token", paths.token_file),
        ("postgres password", paths.postgres_password_file),
    ):
        try:
            read_secret_file(path)
            findings.append(Finding(Level.OK, label, "present, owner-only permissions"))
        except SecretFileError as exc:
            findings.append(Finding(Level.FAIL, label, str(exc)))
    return findings


async def _workflow_findings(settings: Settings) -> list[Finding]:
    gateway = TemporalWorkflowGateway(settings)
    try:
        service = await gateway.describe_service()
        findings = [
            Finding(
                Level.OK,
                "temporal",
                f"namespace '{service.namespace}' reachable, server {service.server_version}",
            )
        ]
        workers = await gateway.describe_workers()
        if workers.workflow_pollers and workers.activity_pollers:
            findings.append(Finding(Level.OK, "worker", f"polling '{workers.task_queue}'"))
        else:
            findings.append(
                Finding(
                    Level.WARN,
                    "worker",
                    f"no recent poller on '{workers.task_queue}' (start with make dev)",
                )
            )
    except WorkflowUnavailableError as exc:
        return [Finding(Level.WARN, "temporal", f"not reachable: {exc}")]
    else:
        return findings
    finally:
        await gateway.close()


def check_services(paths: DevPaths, ports: DevPorts) -> list[Finding]:
    findings: list[Finding] = []
    try:
        env = dev_service_env(paths, ports)
    except SecretFileError as exc:
        return [Finding(Level.FAIL, "services", f"cannot build service settings: {exc}")]
    settings = settings_from_env(env)

    if port_open(ports.postgres):
        try:
            revision = database_revision(settings.database_url.get_secret_value())
            expected = head_revision()
            level = Level.OK if revision == expected else Level.FAIL
            detail = f"schema {revision or 'not migrated'} (head {expected})"
            findings.append(
                Finding(
                    level, "database", detail + ("" if level is Level.OK else "; run make migrate")
                )
            )
        except Exception as exc:  # noqa: BLE001 - doctor reports any connection failure
            findings.append(
                Finding(Level.FAIL, "database", f"connection failed: {type(exc).__name__}")
            )
    else:
        findings.append(
            Finding(
                Level.WARN,
                "database",
                f"not listening on 127.0.0.1:{ports.postgres} (run make infra-up or make dev)",
            )
        )

    findings.extend(asyncio.run(_workflow_findings(settings)))

    if port_open(ports.api):
        try:
            token = read_secret_file(paths.token_file)
            response = httpx.get(
                f"http://127.0.0.1:{ports.api}/v1/health/ready",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            body = response.json()
            level = Level.OK if response.status_code == 200 else Level.WARN
            failing = [c["name"] for c in body.get("checks", []) if c.get("status") != "ok"]
            findings.append(
                Finding(
                    level,
                    "api readiness",
                    body.get("status", "?")
                    + (f" (not ok: {', '.join(failing)})" if failing else ""),
                )
            )
        except (httpx.HTTPError, ValueError, SecretFileError) as exc:
            findings.append(
                Finding(Level.FAIL, "api readiness", f"request failed: {type(exc).__name__}")
            )
    else:
        findings.append(Finding(Level.WARN, "api", f"not listening on 127.0.0.1:{ports.api}"))

    findings.extend(check_analyzers(settings))
    return findings


def check_analyzers(settings: Settings) -> list[Finding]:
    from crp_analysis.engines.base import EngineAdapter
    from crp_analysis.engines.eslint import EslintAdapter
    from crp_analysis.engines.opengrep import OpengrepAdapter
    from crp_analysis.engines.pmd import APEX, PmdAdapter
    from crp_analysis.engines.trivy import TrivyAdapter

    trivy = TrivyAdapter(
        settings.trivy_home, settings.trivy_cache_dir, timeout_seconds=1, max_output_bytes=1
    )
    adapters: dict[str, EngineAdapter] = {
        "pmd": PmdAdapter(
            settings.pmd_home,
            java_heap=settings.pmd_java_heap,
            timeout_seconds=1,
            max_output_bytes=1,
        ),
        "eslint": EslintAdapter(
            settings.eslint_runner_dir,
            node_executable=settings.node_executable,
            timeout_seconds=1,
            max_output_bytes=1,
        ),
        "opengrep": OpengrepAdapter(
            settings.opengrep_home, timeout_seconds=1, max_output_bytes=1, max_target_bytes=1
        ),
        "trivy": trivy,
        "pmd-apex": PmdAdapter(
            settings.pmd_home,
            java_heap=settings.pmd_java_heap,
            timeout_seconds=1,
            max_output_bytes=1,
            ruleset=APEX,
        ),
    }
    findings = []
    for name, adapter in adapters.items():
        availability = adapter.availability()
        if availability.available:
            findings.append(
                Finding(Level.OK, f"analyzer {name}", f"{availability.version} available")
            )
        else:
            findings.append(Finding(Level.FAIL, f"analyzer {name}", f"{availability.reason}"))
    meta = trivy.db_metadata()
    if meta is not None:
        updated, next_update = meta.get("UpdatedAt"), meta.get("NextUpdate")
        stale = isinstance(next_update, str) and next_update < datetime.now(UTC).isoformat()
        findings.append(
            Finding(
                Level.WARN if stale else Level.OK,
                "trivy database",
                f"updated {updated}"
                + ("; stale: run `make engines` to refresh (scans stay offline)" if stale else ""),
            )
        )
    return findings


def render(findings: list[Finding]) -> str:
    width = max(len(f.item) for f in findings)
    return "\n".join(f"[{f.level.value:>4}] {f.item.ljust(width)}  {f.detail}" for f in findings)


def run_doctor(paths: DevPaths, ports: DevPorts) -> int:
    findings = check_prerequisites() + check_secrets(paths)
    if not any(f.level is Level.FAIL and f.item.endswith(("token", "password")) for f in findings):
        findings += check_services(paths, ports)
    print(render(findings))
    failed = [f for f in findings if f.level is Level.FAIL]
    print(
        f"\ndoctor: {len(failed)} failing, {sum(f.level is Level.WARN for f in findings)} warnings"
    )
    return 1 if failed else 0
