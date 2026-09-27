"""Runner connectivity checks. The HTTP transport is a local test double of the API."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from crp_runner.cli import _read_token, ping

TOKEN = "t" * 43


def _client(ready_status: int = 200, live_status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/health/live":
            return httpx.Response(live_status, json={"status": "alive", "version": "0.1.0"})
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"code": "authentication_required"})
        return httpx.Response(
            ready_status,
            json={"status": "ready" if ready_status == 200 else "not_ready", "checks": []},
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ping_succeeds_with_valid_token() -> None:
    assert ping("http://127.0.0.1:8710", TOKEN, _client()) == 0


def test_ping_reports_rejected_token() -> None:
    assert ping("http://127.0.0.1:8710", "wrong", _client()) == 4


def test_ping_reports_not_ready_platform() -> None:
    assert ping("http://127.0.0.1:8710", TOKEN, _client(ready_status=503)) == 5


def test_ping_reports_unreachable_api() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(refuse))
    assert ping("http://127.0.0.1:1", TOKEN, client) == 3


def test_token_file_must_be_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "token"
    path.write_text(TOKEN)
    path.chmod(0o644)
    with pytest.raises(PermissionError):
        _read_token(path)
    path.chmod(0o600)
    assert _read_token(path) == TOKEN
