"""Minimal foreground process supervisor for ``make dev`` and the E2E stack.

Each child runs in its own process group with a fixed argument list. Output is prefixed per
service. When any child exits or the user interrupts, every remaining group is terminated.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO


@dataclass(slots=True)
class ServiceSpec:
    name: str
    args: list[str]
    env: dict[str, str]
    cwd: Path
    log_file: Path | None = None


@dataclass(slots=True)
class Supervisor:
    echo: bool = True
    _procs: dict[str, subprocess.Popen[bytes]] = field(default_factory=dict)
    _threads: list[threading.Thread] = field(default_factory=list)

    def start(self, spec: ServiceSpec) -> subprocess.Popen[bytes]:
        proc = subprocess.Popen(  # noqa: S603 - fixed executables, argument arrays
            spec.args,
            cwd=spec.cwd,
            env=spec.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self._procs[spec.name] = proc
        log = spec.log_file.open("ab") if spec.log_file else None
        thread = threading.Thread(
            target=self._pump, args=(spec.name, proc.stdout, log), daemon=True
        )
        thread.start()
        self._threads.append(thread)
        return proc

    def _pump(self, name: str, stream: IO[bytes] | None, log: IO[bytes] | None) -> None:
        if stream is None:
            return
        prefix = f"[{name}] ".encode()
        try:
            for line in iter(stream.readline, b""):
                if log is not None:
                    log.write(line)
                    log.flush()
                if self.echo:
                    sys.stdout.buffer.write(prefix + line)
                    sys.stdout.flush()
        finally:
            if log is not None:
                log.close()

    def exited(self) -> tuple[str, int] | None:
        for name, proc in self._procs.items():
            code = proc.poll()
            if code is not None:
                return name, code
        return None

    def wait_any(self) -> tuple[str, int]:
        """Block until a child exits or SIGINT/SIGTERM arrives; returns (name, exit code)."""
        interrupted = threading.Event()

        def _handler(signum: int, frame: object) -> None:
            interrupted.set()

        previous = {sig: signal.signal(sig, _handler) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            while not interrupted.is_set():
                if (result := self.exited()) is not None:
                    return result
                time.sleep(0.3)
            return "interrupt", 130
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

    def stop_all(self, timeout: float = 10) -> None:
        for proc in self._procs.values():
            if proc.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        for proc in self._procs.values():
            remaining = max(0.1, deadline - time.monotonic())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
        for thread in self._threads:
            thread.join(timeout=2)
