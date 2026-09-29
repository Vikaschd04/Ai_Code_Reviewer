"""Smoke-test a running backend container over HTTP (stdlib only; used by CI).

Usage: python deploy/smoke_test.py http://127.0.0.1:8080 <access-token> [origin]
Checks liveness, readiness, the web UI at /, the Host allowlist, a ticket-authorized upload with
CORS, snapshot freezing and a real scan. Trivy may be UNAVAILABLE while the offline DB is not
downloaded yet, unless SMOKE_REQUIRE_TRIVY=1 (the image bakes the DB in). SMOKE_TIMEOUT_SECONDS
(default 300) bounds each wait; slow hosts such as a 0.1-CPU free instance need more. The slowest
API response observed while waiting is reported as ``max_api_seconds``.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile
from typing import Any
from urllib.parse import urlsplit

FILES = {
    "pom.xml": "<project><modelVersion>4.0.0</modelVersion><groupId>ci</groupId>"
    "<artifactId>smoke</artifactId><version>1</version></project>\n",
    "src/main/java/ci/Report.java": "package ci;\n\npublic class Report {\n"
    '    public boolean paid(String status) {\n        return status == "PAID";\n    }\n}\n',
    "web/src/app.ts": "export function run(input: string): unknown {\n  return eval(input);\n}\n",
}


TIMEOUT = float(os.environ.get("SMOKE_TIMEOUT_SECONDS", "300"))
REQUIRE_TRIVY = os.environ.get("SMOKE_REQUIRE_TRIVY", "0") == "1"
slowest = [0.0]


def call(
    base: str, method: str, path: str, token: str | None, body: Any = None, **headers: str
) -> tuple[int, dict[str, str], Any]:
    started = time.monotonic()
    try:
        return _call(base, method, path, token, body, **headers)
    finally:
        slowest[0] = max(slowest[0], time.monotonic() - started)


def _call(
    base: str, method: str, path: str, token: str | None, body: Any = None, **headers: str
) -> tuple[int, dict[str, str], Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)  # noqa: S310
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        request.add_header(key.replace("_", "-"), value)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            raw = response.read()
            return response.status, dict(response.headers), json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, dict(exc.headers), json.loads(raw) if raw else None


def check(condition: bool, detail: object) -> None:
    if not condition:
        raise SystemExit(f"smoke test failed: {detail}")


def wait(base: str, path: str, token: str, done: set[str]) -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        status, _, body = call(base, "GET", path, token)
        if status == 200 and body["state"] in done:
            return dict(body)
        time.sleep(1)
    raise SystemExit(f"timed out waiting for {path}")


def main() -> int:
    base, token = sys.argv[1].rstrip("/"), sys.argv[2]
    origin = sys.argv[3] if len(sys.argv) > 3 else "https://ci.example.test"
    deadline = time.monotonic() + TIMEOUT
    while True:
        try:
            if call(base, "GET", "/v1/health/live", None)[0] == 200:
                break
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise SystemExit("backend did not become live")
        time.sleep(2)
    for _ in range(int(TIMEOUT / 2)):
        status, _, ready = call(base, "GET", "/v1/health/ready", token)
        if status == 200:
            break
        time.sleep(2)
    check(status == 200, ready)
    with urllib.request.urlopen(base + "/", timeout=30) as response:  # noqa: S310
        page = response.read().decode()
        check('id="root"' in page, "web UI is not served at /")
        check(
            "default-src 'self'" in response.headers.get("content-security-policy", ""),
            "web UI is served without a Content-Security-Policy",
        )
    blocked = call(base, "GET", "/v1/auth/me", token, Host="evil.example.com")[0]
    check(blocked == 403, f"unknown Host header answered with {blocked}")
    status, _, me = call(base, "GET", "/v1/auth/me", token)
    check(status == 200, me)
    workspace = me["workspaces"][0]["workspace_id"]
    _, _, project = call(
        base, "POST", "/v1/projects", token, {"workspace_id": workspace, "name": "CI smoke"}
    )
    _, _, intake = call(
        base,
        "POST",
        f"/v1/projects/{project['id']}/intakes",
        token,
        {"mode": "zip_upload", "display_name": "smoke.zip"},
    )
    _, _, ticket = call(base, "POST", f"/v1/intakes/{intake['id']}/upload-ticket", token)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, text in FILES.items():
            zf.writestr(name, text)
    target = urlsplit(ticket["upload_url"])
    request = urllib.request.Request(  # noqa: S310
        f"{base}{target.path}?{target.query}", data=buffer.getvalue(), method="PUT"
    )
    request.add_header("Content-Type", "application/zip")
    request.add_header("Origin", origin)
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        check(response.status == 200, "response.status == 200")
        check(
            response.headers["access-control-allow-origin"] == origin,
            "response.headers['access-control-allow-origin'] == origin",
        )
    call(base, "POST", f"/v1/intakes/{intake['id']}/finalize", token)
    snapshot = wait(base, f"/v1/intakes/{intake['id']}", token, {"READY", "REJECTED", "FAILED"})
    check(snapshot["state"] == "READY", snapshot)
    _, _, scan = call(
        base,
        "POST",
        f"/v1/projects/{project['id']}/scans",
        token,
        {"snapshot_id": snapshot["snapshot_id"]},
    )
    done = wait(
        base, f"/v1/scans/{scan['id']}", token, {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELED"}
    )
    engines = {e["engine"]: e["state"] for e in done["engines"]}
    print(
        json.dumps(
            {
                "scan": done["state"],
                "engines": engines,
                "findings": done["summary"]["findings"],
                "max_api_seconds": round(slowest[0], 2),
            }
        )
    )
    for engine in ("structure", "graph", "pmd", "eslint", "opengrep"):
        check(engines[engine] == "SUCCEEDED", engines)
    check(
        engines["trivy"] in ({"SUCCEEDED"} if REQUIRE_TRIVY else {"SUCCEEDED", "UNAVAILABLE"}),
        engines,
    )
    _, _, page = call(base, "GET", f"/v1/scans/{scan['id']}/findings?limit=50", token)
    rules = {f["rule_id"] for f in page["items"]}
    check({"UseEqualsToCompareStrings", "no-eval", "crp.js.code-injection.eval"} <= rules, rules)
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
