"""Minimal authenticated client for the platform intake/scan API (same protocol as the web UI)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx


class ApiCallError(RuntimeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{message} (HTTP {status}, {code})")
        self.status = status
        self.code = code


class PlatformClient:
    def __init__(self, client: httpx.Client, api_url: str, token: str) -> None:
        self._client = client
        self._base = api_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}

    def _check(self, response: httpx.Response) -> Any:
        if response.is_success:
            return response.json() if response.content else None
        try:
            body = response.json()
            code, message = str(body.get("code")), str(body.get("message"))
        except ValueError:
            code, message = "http_error", response.text[:200]
        raise ApiCallError(response.status_code, code, message)

    def get(self, path: str) -> Any:
        return self._check(self._client.get(self._base + path, headers=self._headers))

    def post(self, path: str, body: dict[str, object] | None = None) -> Any:
        return self._check(self._client.post(self._base + path, json=body, headers=self._headers))

    def put_json(self, path: str, body: dict[str, object]) -> Any:
        return self._check(self._client.put(self._base + path, json=body, headers=self._headers))

    def put_file(self, path: str, file: Path) -> Any:
        def chunks() -> Iterator[bytes]:
            with file.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    yield chunk

        headers = {
            **self._headers,
            "Content-Type": "application/zip",
            "Content-Length": str(file.stat().st_size),
        }
        return self._check(
            self._client.put(self._base + path, content=chunks(), headers=headers, timeout=600)
        )

    def wait_intake(self, intake_id: str, timeout: float = 600) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            intake: dict[str, Any] = self.get(f"/v1/intakes/{intake_id}")
            if intake["state"] in {"READY", "REJECTED", "FAILED", "CANCELED"}:
                return intake
            if time.monotonic() > deadline:
                raise ApiCallError(0, "timeout", "Timed out waiting for intake validation")
            time.sleep(1)
