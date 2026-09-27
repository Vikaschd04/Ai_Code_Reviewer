"""Loopback-only PostgreSQL clusters and Temporal dev servers for development and tests.

Every process is started from a fixed executable with an argument list (no shell strings). The
development instances persist under ``.local/``; tests create throwaway instances in temporary
directories on free ports so they never touch developer data.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import psycopg

LOOPBACK = "127.0.0.1"
_DB_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_PG_BIN_CANDIDATES = (
    "/opt/homebrew/opt/postgresql@18/bin",
    "/usr/local/opt/postgresql@18/bin",
    "/usr/lib/postgresql/18/bin",
)


class InfraError(RuntimeError):
    pass


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((LOOPBACK, 0))
        return int(sock.getsockname()[1])


def port_open(port: int, host: str = LOOPBACK, timeout: float = 0.5) -> bool:
    with contextlib.suppress(OSError), socket.create_connection((host, port), timeout=timeout):
        return True
    return False


def find_pg_bin_dir() -> Path:
    """Resolve PostgreSQL server binaries: CRP_PG_BIN_DIR, then PATH, then known install paths."""
    override = os.environ.get("CRP_PG_BIN_DIR")
    candidates = [Path(override)] if override else []
    on_path = shutil.which("pg_ctl")
    if on_path:
        candidates.append(Path(on_path).parent)
    candidates.extend(Path(p) for p in _PG_BIN_CANDIDATES)
    for candidate in candidates:
        if (candidate / "pg_ctl").is_file() and (candidate / "initdb").is_file():
            return candidate
    raise InfraError(
        "PostgreSQL server binaries (initdb, pg_ctl) not found; install PostgreSQL 18 "
        "(macOS: brew install postgresql@18) or set CRP_PG_BIN_DIR"
    )


def find_temporal_cli() -> Path:
    override = os.environ.get("CRP_TEMPORAL_CLI")
    found = override or shutil.which("temporal") or "/opt/homebrew/bin/temporal"
    if not Path(found).is_file():
        raise InfraError(
            "Temporal CLI not found; install it (macOS: brew install temporal) "
            "or set CRP_TEMPORAL_CLI"
        )
    return Path(found)


def _run(args: list[str], *, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed executables, argument arrays
        args, capture_output=True, text=True, timeout=timeout, check=False
    )


@dataclass(slots=True)
class PostgresCluster:
    data_dir: Path
    port: int
    password: str
    log_file: Path
    bin_dir: Path = field(default_factory=find_pg_bin_dir)
    user: str = "crp"

    def _bin(self, name: str) -> str:
        return str(self.bin_dir / name)

    @property
    def initialized(self) -> bool:
        return (self.data_dir / "PG_VERSION").is_file()

    def init(self) -> None:
        if self.initialized:
            return
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.chmod(0o700)
        pwfile = self.data_dir.parent / f".{self.data_dir.name}.pw"
        pwfile.write_text(self.password + "\n", encoding="utf-8")
        pwfile.chmod(0o600)
        try:
            result = _run(
                [
                    self._bin("initdb"),
                    "--pgdata",
                    str(self.data_dir),
                    "--username",
                    self.user,
                    "--pwfile",
                    str(pwfile),
                    "--auth",
                    "scram-sha-256",
                    "--encoding",
                    "UTF8",
                    "--locale",
                    "C",
                    "--no-instructions",
                ]
            )
        finally:
            pwfile.unlink(missing_ok=True)
        if result.returncode != 0:
            raise InfraError(f"initdb failed: {result.stderr.strip()[-2000:]}")
        with (self.data_dir / "postgresql.conf").open("a", encoding="utf-8") as conf:
            conf.write(
                "\n# code-review-platform: loopback TCP only, no unix sockets\n"
                f"listen_addresses = '{LOOPBACK}'\n"
                f"port = {self.port}\n"
                "unix_socket_directories = ''\n"
                "fsync = on\n"
            )

    def running(self) -> bool:
        if not self.initialized:
            return False
        return _run([self._bin("pg_ctl"), "status", "--pgdata", str(self.data_dir)]).returncode == 0

    def start(self) -> None:
        if self.running():
            return
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        result = _run(
            [
                self._bin("pg_ctl"),
                "start",
                "--pgdata",
                str(self.data_dir),
                "--log",
                str(self.log_file),
                "--wait",
                "--timeout",
                "60",
            ]
        )
        if result.returncode != 0:
            raise InfraError(
                f"PostgreSQL failed to start (see {self.log_file.name}): {result.stderr.strip()}"
            )

    def stop(self) -> None:
        if not self.running():
            return
        _run(
            [
                self._bin("pg_ctl"),
                "stop",
                "--pgdata",
                str(self.data_dir),
                "--mode",
                "fast",
                "--wait",
            ]
        )

    def url(self, database: str) -> str:
        return (
            f"postgresql+psycopg://{self.user}:{quote(self.password, safe='')}"
            f"@{LOOPBACK}:{self.port}/{database}"
        )

    def _connect(self, database: str = "postgres") -> psycopg.Connection[tuple[object, ...]]:
        return psycopg.connect(
            host=LOOPBACK,
            port=self.port,
            user=self.user,
            password=self.password,
            dbname=database,
            autocommit=True,
            connect_timeout=5,
        )

    def database_exists(self, name: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        return row is not None

    def create_database(self, name: str, *, template: str | None = None) -> None:
        for identifier in (name, template):
            if identifier is not None and not _DB_NAME.fullmatch(identifier):
                raise InfraError(f"invalid database name: {identifier!r}")
        statement = f'CREATE DATABASE "{name}"'
        if template:
            statement += f' TEMPLATE "{template}"'
        with self._connect() as conn:
            conn.execute(statement.encode())

    def drop_database(self, name: str) -> None:
        if not _DB_NAME.fullmatch(name):
            raise InfraError(f"invalid database name: {name!r}")
        with self._connect() as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'.encode())


@dataclass(slots=True)
class TemporalDevServer:
    """``temporal server start-dev`` bound to loopback; optional SQLite persistence."""

    port: int
    log_file: Path
    db_file: Path | None = None
    ui_port: int | None = None
    pid_file: Path | None = None
    cli: Path = field(default_factory=find_temporal_cli)
    _process: subprocess.Popen[bytes] | None = None

    @property
    def address(self) -> str:
        return f"{LOOPBACK}:{self.port}"

    def _args(self) -> list[str]:
        args = [
            str(self.cli),
            "server",
            "start-dev",
            "--ip",
            LOOPBACK,
            "--port",
            str(self.port),
            "--log-level",
            "warn",
        ]
        if self.db_file is not None:
            self.db_file.parent.mkdir(parents=True, exist_ok=True)
            args += ["--db-filename", str(self.db_file)]
        if self.ui_port is None:
            args.append("--headless")
        else:
            args += ["--ui-port", str(self.ui_port), "--ui-disable-news-fetch"]
        return args

    def recorded_pid(self) -> int | None:
        if self.pid_file is None or not self.pid_file.is_file():
            return None
        try:
            pid = int(self.pid_file.read_text().strip())
            os.kill(pid, 0)
        except ValueError, ProcessLookupError, PermissionError:
            return None
        return pid

    def running(self) -> bool:
        return port_open(self.port)

    def start(self, *, detach: bool, timeout: float = 60) -> None:
        if self.running():
            raise InfraError(f"port {self.port} is already in use; is Temporal already running?")
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        log = self.log_file.open("ab")
        try:
            self._process = subprocess.Popen(  # noqa: S603 - fixed executable, argument array
                self._args(),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=detach,
            )
        finally:
            log.close()
        if self.pid_file is not None:
            self.pid_file.parent.mkdir(parents=True, exist_ok=True)
            self.pid_file.write_text(str(self._process.pid))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise InfraError(
                    f"Temporal dev server exited with {self._process.returncode}; "
                    f"see {self.log_file.name}"
                )
            if port_open(self.port):
                return
            time.sleep(0.25)
        self.stop()
        raise InfraError(f"Temporal dev server did not open port {self.port} within {timeout}s")

    def stop(self, timeout: float = 15) -> None:
        pid = self._process.pid if self._process is not None else self.recorded_pid()
        if pid is None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process is not None:
                if self._process.poll() is not None:
                    break
            else:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
            time.sleep(0.2)
        else:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        if self._process is not None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                self._process.wait(timeout=5)
        if self.pid_file is not None:
            self.pid_file.unlink(missing_ok=True)
