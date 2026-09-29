"""Hosted Trivy DB handling: baked copy activation, refresh and atomic swap (no network)."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from crp_analysis.engines.trivy import TrivyAdapter
from crp_devtools import trivy_db
from crp_devtools.infra import InfraError

HOME = Path("/nonexistent/trivy-home")


def _write_db(cache: Path, updated: datetime) -> Path:
    (cache / "db").mkdir(parents=True, exist_ok=True)
    (cache / "db" / "trivy.db").write_bytes(b"bolt")
    stamp = updated.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    (cache / "db" / "metadata.json").write_text(json.dumps({"UpdatedAt": stamp}))
    return cache


def _days_ago(days: float) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


@pytest.fixture
def fake_download(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    downloads: list[Path] = []

    def download(home: Path, cache_dir: Path) -> dict[str, object]:
        downloads.append(cache_dir)
        _write_db(cache_dir, datetime.now(UTC))
        return {"UpdatedAt": "now"}

    monkeypatch.setattr(trivy_db, "download_trivy_db", download)
    return downloads


def test_baked_copy_is_active_immediately(tmp_path: Path) -> None:
    baked = _write_db(tmp_path / "image" / "trivy-cache", _days_ago(1))
    data = tmp_path / "data"
    data.mkdir()
    assert trivy_db.activate(data, baked) == baked
    link = data / trivy_db.ACTIVE_NAME
    assert link.is_symlink() and link.resolve() == baked.resolve()
    adapter = TrivyAdapter(HOME, link, timeout_seconds=60, max_output_bytes=1024)
    assert adapter.db_metadata() is not None  # the analyzer reads through the symlink


def test_newest_copy_wins_and_older_generations_are_removed(tmp_path: Path) -> None:
    baked = _write_db(tmp_path / "image" / "trivy-cache", _days_ago(3))
    data = tmp_path / "data"
    legacy = _write_db(data / trivy_db.ACTIVE_NAME, _days_ago(1))  # pre-symlink layout
    older = _write_db(data / "trivy-db-20260101T000000000000", _days_ago(10))
    partial = data / "trivy-db-20260102T000000000000"
    partial.mkdir()  # interrupted download
    active = trivy_db.activate(data, baked)
    assert active is not None and active.name.startswith(trivy_db.GENERATION_PREFIX)
    assert legacy.is_symlink() and legacy.resolve() == active.resolve()  # adopted, then linked
    assert not older.exists() and not partial.exists()
    assert (baked / "db" / "trivy.db").is_file()  # the baked copy is never deleted

    # After a redeploy with a fresher image, the baked copy replaces the older generation.
    _write_db(baked, datetime.now(UTC))
    assert trivy_db.activate(data, baked) == baked
    assert not active.exists()


def test_no_db_leaves_trivy_unavailable(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / trivy_db.ACTIVE_NAME).mkdir(parents=True)  # empty legacy directory
    assert trivy_db.activate(data, tmp_path / "missing") is None
    assert not (data / trivy_db.ACTIVE_NAME).exists()


def test_refresh_swaps_atomically_and_keeps_the_baked_copy(
    tmp_path: Path, fake_download: list[Path]
) -> None:
    baked = _write_db(tmp_path / "image" / "trivy-cache", _days_ago(2))
    data = tmp_path / "data"
    data.mkdir()
    trivy_db.activate(data, baked)
    lock = threading.Lock()
    _, retired = trivy_db.refresh(HOME, data, lock)
    link = data / trivy_db.ACTIVE_NAME
    first = fake_download[0]
    assert retired is None and link.resolve() == first.resolve()
    assert (baked / "db" / "trivy.db").is_file()
    assert trivy_db.is_fresh(link)

    # With the in-process lock, the replaced generation is deleted at once.
    trivy_db.refresh(HOME, data, lock)
    assert not first.exists() and link.resolve() == fake_download[1].resolve()
    assert not lock.locked()

    # Without it (scans in another process), the caller deletes it after a grace period.
    _, retired = trivy_db.refresh(HOME, data)
    assert retired is not None and retired.resolve() == fake_download[1].resolve()
    assert retired.exists()


def test_failed_refresh_keeps_the_current_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baked = _write_db(tmp_path / "image" / "trivy-cache", _days_ago(2))
    data = tmp_path / "data"
    data.mkdir()
    trivy_db.activate(data, baked)

    def failing(home: Path, cache_dir: Path) -> dict[str, object]:
        (cache_dir / "db").mkdir(parents=True)
        raise InfraError("Trivy DB download failed: network unreachable")

    monkeypatch.setattr(trivy_db, "download_trivy_db", failing)
    with pytest.raises(InfraError):
        trivy_db.refresh(HOME, data)
    assert (data / trivy_db.ACTIVE_NAME).resolve() == baked.resolve()
    assert list(data.glob(f"{trivy_db.GENERATION_PREFIX}*")) == []


class _OneRound(threading.Event):
    def wait(self, timeout: float | None = None) -> bool:
        self.set()
        return True


def test_keep_fresh_refreshes_only_a_stale_copy(tmp_path: Path, fake_download: list[Path]) -> None:
    data = tmp_path / "data"
    data.mkdir()
    fresh = _write_db(tmp_path / "image" / "trivy-cache", _days_ago(0.5))
    trivy_db.activate(data, fresh)
    trivy_db.keep_fresh(HOME, data, _OneRound())
    assert fake_download == []

    _write_db(fresh, _days_ago(2))
    trivy_db.keep_fresh(HOME, data, _OneRound())
    assert len(fake_download) == 1
    assert (data / trivy_db.ACTIVE_NAME).resolve() == fake_download[0].resolve()
