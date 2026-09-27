from __future__ import annotations

import logging
from pathlib import Path

import pytest

from crp_core.domain.states import (
    SCAN_TRANSITIONS,
    InvalidTransitionError,
    MembershipRole,
    ScanState,
    require_scan_transition,
)
from crp_core.local_secrets import SecretFileError, read_secret_file, write_secret_file
from crp_core.log import RedactingFilter, redact


def test_every_scan_state_has_a_transition_entry() -> None:
    assert set(SCAN_TRANSITIONS) == set(ScanState)


@pytest.mark.parametrize(
    "state",
    [
        ScanState.SUCCEEDED,
        ScanState.PARTIAL,
        ScanState.FAILED,
        ScanState.CANCELED,
        ScanState.BUDGET_EXHAUSTED,
    ],
)
def test_terminal_states_cannot_transition(state: ScanState) -> None:
    assert state.is_terminal
    with pytest.raises(InvalidTransitionError):
        require_scan_transition(state, ScanState.RUNNING)


def test_failed_scan_cannot_become_succeeded_and_queued_cannot_skip_running() -> None:
    with pytest.raises(InvalidTransitionError):
        require_scan_transition(ScanState.FAILED, ScanState.SUCCEEDED)
    with pytest.raises(InvalidTransitionError):
        require_scan_transition(ScanState.QUEUED, ScanState.SUCCEEDED)
    assert require_scan_transition(ScanState.QUEUED, ScanState.RUNNING) is ScanState.RUNNING


def test_role_ordering() -> None:
    assert MembershipRole.OWNER.at_least(MembershipRole.ADMIN)
    assert not MembershipRole.VIEWER.at_least(MembershipRole.MEMBER)


def test_secret_files_are_created_owner_only_and_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "s" / "token"
    assert write_secret_file(path, "a" * 40) is True
    assert write_secret_file(path, "b" * 40) is False
    assert read_secret_file(path) == "a" * 40
    assert path.stat().st_mode & 0o777 == 0o600


def test_group_readable_secret_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "token"
    write_secret_file(path, "a" * 40)
    path.chmod(0o644)
    with pytest.raises(SecretFileError, match="accessible by other users"):
        read_secret_file(path)


def test_symlinked_secret_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    write_secret_file(real, "a" * 40)
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(SecretFileError, match="regular file"):
        read_secret_file(link)


def test_missing_secret_error_does_not_leak_directory(tmp_path: Path) -> None:
    with pytest.raises(SecretFileError) as info:
        read_secret_file(tmp_path / "nested" / "absent-token")
    assert str(tmp_path) not in str(info.value)


def test_redaction_masks_tokens_cookies_and_url_passwords() -> None:
    text = (
        "Authorization: Bearer abc.def-123 cookie crp_session=v1.x.y "
        "url postgresql+psycopg://crp:hunter2@127.0.0.1/db password=pw1"
    )
    redacted = redact(text)
    for secret in ("abc.def-123", "v1.x.y", "hunter2", "pw1"):
        assert secret not in redacted


def test_redacting_filter_applies_to_formatted_args() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "token=%s", ("s3cr3t",), None)
    RedactingFilter().filter(record)
    assert "s3cr3t" not in record.getMessage()
