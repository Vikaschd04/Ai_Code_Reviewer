"""Bounded subprocess execution for approved analyzer executables.

Fixed executable + argument list (never a shell string), own process group, scrubbed environment
(no platform secrets), wall-clock timeout, capped stdout/stderr, cooperative cancellation that
terminates the whole process group. CPU/memory caps rely on tool flags (JVM -Xmx, Node heap);
there is no cgroup/sandbox isolation on local macOS development hosts.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from crp_analysis.engines.base import CancelToken, Heartbeat

_BASE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
_TAIL = 8192


@dataclass(frozen=True, slots=True)
class ProcessResult:
    exit_code: int | None
    stdout_tail: str
    stderr_tail: str
    timed_out: bool
    cancelled: bool
    output_truncated: bool
    duration_ms: int


def scrubbed_env(
    extra_path: list[Path], home: Path, extra: dict[str, str] | None = None
) -> dict[str, str]:
    path = ":".join([*(str(p) for p in extra_path), _BASE_PATH])
    env = {
        "PATH": path,
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TMPDIR": str(home),
    }
    env.update(extra or {})
    return env


def _tail(handle: IO[bytes], limit: int) -> tuple[str, bool]:
    handle.flush()
    size = handle.seek(0, os.SEEK_END)
    handle.seek(max(0, size - _TAIL))
    return handle.read().decode("utf-8", errors="replace"), size > limit


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    for sig, wait in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 5.0)):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, sig)
        try:
            proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            continue
        return


def run_bounded(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: float,
    max_output_bytes: int,
    cancel: CancelToken,
    heartbeat: Heartbeat,
    label: str,
) -> ProcessResult:
    started = time.monotonic()
    timed_out = cancelled = False
    with (
        tempfile.TemporaryFile(dir=env.get("TMPDIR")) as out,
        tempfile.TemporaryFile(dir=env.get("TMPDIR")) as err,
    ):
        proc = subprocess.Popen(  # noqa: S603 - approved executable, argument list, scrubbed env
            args,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            process_group=0,
        )
        last_beat = 0.0
        while proc.poll() is None:
            now = time.monotonic()
            if now - started > timeout_seconds:
                timed_out = True
                _terminate(proc)
                break
            if cancel.cancelled:
                cancelled = True
                _terminate(proc)
                break
            if out.tell() + err.tell() > max_output_bytes:
                _terminate(proc)
                break
            if now - last_beat > 5:
                heartbeat(f"{label} running {int(now - started)}s")
                last_beat = now
            time.sleep(0.1)
        stdout_tail, out_big = _tail(out, max_output_bytes)
        stderr_tail, err_big = _tail(err, max_output_bytes)
    return ProcessResult(
        exit_code=proc.returncode,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        timed_out=timed_out,
        cancelled=cancelled,
        output_truncated=out_big or err_big,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
