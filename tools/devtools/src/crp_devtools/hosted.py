"""``crp-dev hosted``: single-user hosted deployment in one container (see docs/DEPLOYMENT.md).

Starts a Temporal dev server persisted on the data disk, migrates PostgreSQL, keeps the offline
Trivy database fresh in the background (the only network use; scans stay offline), then runs the
API and worker under the supervisor. If any process exits, everything stops and the container
exits non-zero so the platform restarts it.

The built web UI (``/app/web-dist`` in the image, or ``CRP_WEB_STATIC_DIR``) is served from the
same address, so UI and API share one origin.

Required environment: ``CRP_ACCESS_TOKEN`` (>= 32 characters; the sign-in secret),
``CRP_DATABASE_URL`` or ``DATABASE_URL``, and ``CRP_PUBLIC_HOST`` or ``RENDER_EXTERNAL_HOSTNAME``
(this service's public host name). Optional: ``CRP_ALLOWED_WEB_ORIGINS`` (default: the service's
own https origin; add others if the UI is also hosted elsewhere), ``CRP_ALLOWED_WEB_ORIGIN_REGEX``,
``CRP_EXTRA_PUBLIC_HOSTS`` (custom domains), ``CRP_DATA_DIR`` (default ``/data``),
``CRP_TRIVY_DB_AUTO_REFRESH=0`` (keep the current DB).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from crp_core.local_secrets import write_secret_file
from crp_devtools.dbtasks import migrate_and_provision, settings_from_env
from crp_devtools.engines import OPENGREP, TRIVY, download_trivy_db, pmd_home
from crp_devtools.infra import InfraError, TemporalDevServer
from crp_devtools.localenv import child_env
from crp_devtools.paths import find_repo_root
from crp_devtools.supervisor import ServiceSpec, Supervisor

logger = logging.getLogger("crp_devtools.hosted")
TEMPORAL_PORT = 7233
DB_REFRESH_AFTER = timedelta(hours=24)
DB_CHECK_INTERVAL_SECONDS = 6 * 3600
_PASS_THROUGH_PREFIXES = ("CRP_INTAKE_", "CRP_ENGINE_", "CRP_SESSION_TTL_SECONDS", "CRP_LOG_LEVEL")


def hosted_env(environ: dict[str, str], repo: Path) -> tuple[dict[str, str], Path]:
    """Translate platform environment into validated ``CRP_*`` settings for API and worker."""
    token = environ.get("CRP_ACCESS_TOKEN", "")
    if len(token) < 32:
        raise InfraError("CRP_ACCESS_TOKEN must be set to a random value of at least 32 characters")
    host = environ.get("CRP_PUBLIC_HOST") or environ.get("RENDER_EXTERNAL_HOSTNAME")
    if not host:
        raise InfraError(
            "set CRP_PUBLIC_HOST (or run on Render, which sets RENDER_EXTERNAL_HOSTNAME)"
        )
    # The container serves the UI itself, so by default the only web origin is its own address.
    origins = environ.get("CRP_ALLOWED_WEB_ORIGINS", "").strip() or f"https://{host}"
    database = environ.get("CRP_DATABASE_URL") or environ.get("DATABASE_URL")
    if not database:
        raise InfraError("set CRP_DATABASE_URL (or DATABASE_URL)")
    data = Path(environ.get("CRP_DATA_DIR", "/data"))
    engines = repo / ".local" / "engines"
    token_file = data / "secrets" / "access-token"
    write_secret_file(token_file, token, overwrite=True)  # rotation takes effect on restart
    hosts = [host, *(h.strip() for h in environ.get("CRP_EXTRA_PUBLIC_HOSTS", "").split(",") if h)]
    env = {
        "CRP_ENVIRONMENT": "hosted",
        "CRP_API_HOST": "0.0.0.0",  # noqa: S104 - behind the platform's TLS proxy (hosted mode)
        "CRP_API_PORT": environ.get("PORT", "8080"),
        "CRP_PUBLIC_HOSTS": ",".join(dict.fromkeys(h for h in hosts if h)),
        "CRP_ALLOWED_WEB_ORIGINS": origins,
        "CRP_PUBLIC_API_URL": f"https://{host}",
        "CRP_LOCAL_TOKEN_FILE": str(token_file),
        "CRP_DATABASE_URL": database,
        "CRP_TEMPORAL_ADDRESS": f"127.0.0.1:{_temporal_port(environ)}",
        "CRP_ARTIFACT_ROOT": str(data / "artifacts"),
        "CRP_WORK_ROOT": str(data / "work"),
        "CRP_PMD_HOME": str(pmd_home(engines)),
        "CRP_OPENGREP_HOME": str(engines / f"opengrep-{OPENGREP.version}"),
        "CRP_TRIVY_HOME": str(engines / f"trivy-{TRIVY.version}"),
        "CRP_TRIVY_CACHE_DIR": str(data / "trivy-cache"),
        "CRP_ESLINT_RUNNER_DIR": str(repo / "engines" / "eslint-runner"),
        "CRP_PMD_JAVA_HEAP": environ.get("CRP_PMD_JAVA_HEAP", "512m"),
        "CRP_LOG_FORMAT": "json",
    }
    static = Path(environ.get("CRP_WEB_STATIC_DIR", str(repo / "web-dist")))
    if (static / "index.html").is_file():
        env["CRP_WEB_STATIC_DIR"] = str(static)
    if regex := environ.get("CRP_ALLOWED_WEB_ORIGIN_REGEX"):
        env["CRP_ALLOWED_WEB_ORIGIN_REGEX"] = regex
    for key, value in environ.items():
        if key.startswith(_PASS_THROUGH_PREFIXES):
            env[key] = value
    return env, data


def _temporal_port(environ: dict[str, str]) -> int:
    return int(environ.get("CRP_TEMPORAL_PORT", str(TEMPORAL_PORT)))


def _db_is_fresh(cache_dir: Path) -> bool:
    meta = cache_dir / "db" / "metadata.json"
    if not meta.is_file() or not (cache_dir / "db" / "trivy.db").is_file():
        return False
    try:
        updated = datetime.fromisoformat(json.loads(meta.read_text())["UpdatedAt"][:26] + "+00:00")
    except ValueError, KeyError, OSError:
        return False
    return datetime.now(UTC) - updated < DB_REFRESH_AFTER


def keep_trivy_db_fresh(trivy_home: Path, cache_dir: Path, stop: threading.Event) -> None:
    """Refresh the offline vulnerability DB daily; failures leave the last good copy in place."""
    while not stop.is_set():
        if not _db_is_fresh(cache_dir):
            try:
                meta = download_trivy_db(trivy_home, cache_dir)
                print(f"crp-hosted: Trivy DB updated {meta.get('UpdatedAt')}", flush=True)
            except (InfraError, OSError) as exc:
                print(f"crp-hosted: Trivy DB refresh failed: {exc}", file=sys.stderr, flush=True)
        stop.wait(DB_CHECK_INTERVAL_SECONDS)


def run_hosted() -> int:
    repo = find_repo_root(Path(__file__).resolve().parent)
    env, data = hosted_env(dict(os.environ), repo)
    for directory in ("artifacts", "work", "trivy-cache", "temporal", "logs"):
        (data / directory).mkdir(parents=True, exist_ok=True, mode=0o700)
    temporal = TemporalDevServer(
        port=_temporal_port(dict(os.environ)),
        log_file=data / "logs" / "temporal.log",
        db_file=data / "temporal" / "temporal.db",
    )
    temporal.start(detach=False, timeout=120)
    print("crp-hosted: Temporal dev server ready (SQLite on the data disk)", flush=True)
    stop = threading.Event()
    supervisor = Supervisor(echo=True)
    try:
        revision, _ = migrate_and_provision(settings_from_env(env))
        print(f"crp-hosted: database migrated to {revision}", flush=True)
        if os.environ.get("CRP_TRIVY_DB_AUTO_REFRESH", "1") != "0":
            threading.Thread(
                target=keep_trivy_db_fresh,
                args=(Path(env["CRP_TRIVY_HOME"]), Path(env["CRP_TRIVY_CACHE_DIR"]), stop),
                daemon=True,
            ).start()
        else:
            print("crp-hosted: automatic Trivy DB refresh disabled", flush=True)
        full_env = child_env(env)
        supervisor.start(ServiceSpec("api", [sys.executable, "-m", "crp_api"], full_env, repo))
        supervisor.start(
            ServiceSpec("worker", [sys.executable, "-m", "crp_worker"], full_env, repo)
        )
        name, code = supervisor.wait_any()
        print(f"crp-hosted: {name} exited with {code}; stopping", file=sys.stderr, flush=True)
        return code or 1
    finally:
        stop.set()
        supervisor.stop_all()
        temporal.stop()
        time.sleep(0.1)
