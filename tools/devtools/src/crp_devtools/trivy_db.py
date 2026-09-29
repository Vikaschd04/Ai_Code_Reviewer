"""Offline Trivy vulnerability DB for hosted deployments: baked copy, refresh and atomic swap.

The analyzers read ``<data>/trivy-cache``, a symlink to the active DB copy: either the copy baked
into the image (``/app/.local/engines/trivy-cache``, available immediately after every start,
also on hosts without a persistent disk) or a newer ``<data>/trivy-db-<timestamp>`` generation
downloaded by the background refresh. A refresh downloads into a new generation directory and
then replaces the symlink atomically, so a running scan never sees a half-written DB (directories
inside image layers cannot be renamed, which is why a symlink is swapped instead). Scans stay
offline; the refresh is the only network use.
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path

from crp_devtools.engines import download_trivy_db
from crp_devtools.infra import InfraError

ACTIVE_NAME = "trivy-cache"
GENERATION_PREFIX = "trivy-db-"
REFRESH_AFTER = timedelta(hours=24)
CHECK_INTERVAL_SECONDS = 6 * 3600


def db_updated_at(cache_dir: Path) -> datetime | None:
    """``UpdatedAt`` of a complete DB copy, or ``None`` when missing or unreadable."""
    meta = cache_dir / "db" / "metadata.json"
    if not meta.is_file() or not (cache_dir / "db" / "trivy.db").is_file():
        return None
    try:
        stamp = json.loads(meta.read_text(encoding="utf-8"))["UpdatedAt"]
        return datetime.fromisoformat(stamp[:26] + "+00:00")
    except ValueError, KeyError, TypeError, OSError:
        return None


def is_fresh(cache_dir: Path, now: datetime | None = None) -> bool:
    updated = db_updated_at(cache_dir)
    return updated is not None and (now or datetime.now(UTC)) - updated < REFRESH_AFTER


def _generations(data: Path) -> list[Path]:
    return sorted(
        p for p in data.glob(f"{GENERATION_PREFIX}*") if p.is_dir() and not p.is_symlink()
    )


def _point(link: Path, target: Path) -> None:
    """Atomically make ``link`` a symlink to ``target``."""
    temporary = link.with_name(f".{link.name}.next")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target, target_is_directory=True)
    temporary.replace(link)


def activate(data: Path, baked: Path | None) -> Path | None:
    """At startup, point ``<data>/trivy-cache`` at the newest complete DB copy.

    Adopts a legacy real ``trivy-cache`` directory as a generation, removes incomplete or older
    generations (never the baked copy) and returns the active copy, or ``None`` when there is no
    DB yet (Trivy then reports UNAVAILABLE until the refresh has downloaded one).
    """
    link = data / ACTIVE_NAME
    if link.is_dir() and not link.is_symlink():
        if db_updated_at(link) is None:
            shutil.rmtree(link)
        else:
            link.rename(data / f"{GENERATION_PREFIX}{datetime.now(UTC):%Y%m%dT%H%M%S%f}")
    candidates = [p for p in _generations(data) if db_updated_at(p) is not None]
    if baked is not None and db_updated_at(baked) is not None:
        candidates.append(baked)
    best = max(
        candidates, key=lambda p: db_updated_at(p) or datetime.min.replace(tzinfo=UTC), default=None
    )
    if best is not None:
        _point(link, best)
    elif link.is_symlink():
        link.unlink()
    for generation in _generations(data):
        if generation != best:
            shutil.rmtree(generation, ignore_errors=True)
    return best


def refresh(
    trivy_home: Path, data: Path, lock: threading.Lock | None = None
) -> tuple[dict[str, object], Path | None]:
    """Download a new generation and make it active.

    With ``lock`` (the analyzer's DB lock, when scans run in this process) the previous generation
    is deleted immediately under the lock. Without it (scans in another process) the previous
    generation is returned so the caller can delete it after a grace period; files a running scan
    still has open stay readable either way.
    """
    link = data / ACTIVE_NAME
    target = data / f"{GENERATION_PREFIX}{datetime.now(UTC):%Y%m%dT%H%M%S%f}"
    try:
        metadata = download_trivy_db(trivy_home, target)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    previous = link.resolve() if link.is_symlink() else None
    retired = previous if previous is not None and previous.parent == data.resolve() else None
    with lock or nullcontext():
        _point(link, target)
        if lock is not None and retired is not None:
            shutil.rmtree(retired, ignore_errors=True)
            retired = None
    return metadata, retired


def keep_fresh(
    trivy_home: Path, data: Path, stop: threading.Event, lock: threading.Lock | None = None
) -> None:
    """Refresh daily; a failed refresh keeps the current copy (scans report its age)."""
    retired: list[Path] = []
    while not stop.is_set():
        for stale in retired:
            shutil.rmtree(stale, ignore_errors=True)  # one check interval after it was replaced
        retired.clear()
        if not is_fresh(data / ACTIVE_NAME):
            try:
                metadata, old = refresh(trivy_home, data, lock)
                print(f"crp-hosted: Trivy DB updated {metadata.get('UpdatedAt')}", flush=True)
                if old is not None:
                    retired.append(old)
            except (InfraError, OSError) as exc:
                print(f"crp-hosted: Trivy DB refresh failed: {exc}", file=sys.stderr, flush=True)
        stop.wait(CHECK_INTERVAL_SECONDS)


def start_refresh_thread(
    trivy_home: Path, data: Path, stop: threading.Event, lock: threading.Lock | None = None
) -> threading.Thread:
    thread = threading.Thread(
        target=keep_fresh, args=(trivy_home, data, stop, lock), name="trivy-db-refresh", daemon=True
    )
    thread.start()
    return thread
