"""``crp-runner``: user-invoked local runner.

``ping`` verifies connectivity and authentication. ``capture`` snapshots an explicitly selected
folder and **uploads its source to the platform API** (after skipping policy-excluded paths such
as secrets, VCS metadata and dependency/build output on this machine). It never modifies the
folder, never runs its scripts and never follows symlinks. Nothing is sent without confirmation.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from crp_runner import __version__
from crp_runner.api import ApiCallError, PlatformClient
from crp_runner.capture import Capture, CaptureError, Policy, capture_folder

DEFAULT_API_URL = "http://127.0.0.1:8710"


def _read_token(path: Path) -> str:
    info = path.lstat()
    if info.st_mode & 0o077:
        raise PermissionError(f"token file {path.name} is readable by other users; chmod 600 it")
    return path.read_text(encoding="utf-8").strip()


def ping(api_url: str, token: str | None, client: httpx.Client) -> int:
    """Check liveness, then (with a token) authenticated readiness. Returns a process exit code."""
    base = api_url.rstrip("/")
    try:
        live = client.get(f"{base}/v1/health/live")
    except httpx.HTTPError as exc:
        print(f"crp-runner: API unreachable at {base}: {type(exc).__name__}", file=sys.stderr)
        return 3
    if live.status_code != 200:
        print(f"crp-runner: liveness returned HTTP {live.status_code}", file=sys.stderr)
        return 3
    print(f"crp-runner: API alive (version {live.json().get('version', '?')})")
    if token is None:
        print("crp-runner: no token supplied; skipped authenticated readiness check")
        return 0
    ready = client.get(f"{base}/v1/health/ready", headers={"Authorization": f"Bearer {token}"})
    if ready.status_code == 401:
        print("crp-runner: token rejected (HTTP 401)", file=sys.stderr)
        return 4
    body = ready.json()
    for check in body.get("checks", []):
        print(f"  {check.get('name')}: {check.get('status')} - {check.get('summary')}")
    print(f"crp-runner: platform {body.get('status')}")
    return 0 if ready.status_code == 200 else 5


def _describe(capture: Capture, folder: Path, api_url: str) -> list[str]:
    reasons: dict[str, int] = {}
    for item in capture.excluded:
        reasons[str(item["reason"])] = reasons.get(str(item["reason"]), 0) + 1
    lines = [
        f"Folder:        {folder}",
        f"Files to send: {len(capture.files)} ({capture.total_bytes:,} bytes)",
        f"Manifest:      sha256 {capture.digest()}",
        "Skipped here:  "
        + (
            ", ".join(f"{count} {reason}" for reason, count in sorted(reasons.items())) or "nothing"
        ),
    ]
    for item in capture.excluded[:15]:
        lines.append(
            f"  - {item['path']}{'/' if item['kind'] == 'directory' else ''} ({item['reason']})"
        )
    if len(capture.excluded) > 15:
        lines.append(f"  … and {len(capture.excluded) - 15} more")
    lines.append(
        f"Destination:   {api_url} — this uploads the listed source files; they leave this machine."
    )
    return lines


def capture_and_upload(
    folder: Path,
    project_id: str,
    platform: PlatformClient,
    *,
    api_url: str,
    dry_run: bool,
    assume_yes: bool,
    start_scan: bool,
    display_name: str | None = None,
    out: Callable[[str], None] = print,
    confirm: Callable[[str], str] = input,
) -> dict[str, Any] | None:
    policy_doc = platform.get("/v1/intake-policy")
    policy = Policy.from_document(policy_doc)
    limits = policy_doc["limits"]
    capture = capture_folder(
        folder,
        policy,
        max_files=int(limits["max_entries"]),
        max_bytes=int(limits["max_expanded_bytes"]),
    )
    try:
        for line in _describe(capture, folder.resolve(), api_url):
            out(line)
        if dry_run:
            out("Dry run: nothing was uploaded.")
            return None
        if (
            not assume_yes
            and confirm("Upload these files? Type 'yes' to continue: ").strip().lower() != "yes"
        ):
            out("Aborted: nothing was uploaded.")
            return None
        intake = platform.post(
            f"/v1/projects/{project_id}/intakes",
            {
                "mode": "local_runner",
                "display_name": display_name or capture.root_label or "local folder",
            },
        )
        platform.put_json(
            f"/v1/intakes/{intake['id']}/client-manifest",
            capture.client_manifest(__version__, policy.version),
        )
        platform.put_file(f"/v1/intakes/{intake['id']}/content", capture.archive)
        platform.post(f"/v1/intakes/{intake['id']}/finalize")
        result = platform.wait_intake(intake["id"])
        out(f"Intake {result['id']}: {result['state']}")
        if result["state"] != "READY":
            out(f"  {result.get('error_code')}: {result.get('error_message')}")
            return result
        out(f"Snapshot {result['snapshot_id']} frozen (manifest sha256 {capture.digest()})")
        if start_scan:
            scan = platform.post(
                f"/v1/projects/{project_id}/scans", {"snapshot_id": result["snapshot_id"]}
            )
            out(f"Scan {scan['id']} started ({scan['state']})")
            result = {**result, "scan_id": scan["id"]}
        return result
    finally:
        capture.archive.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="crp-runner", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--version", action="version", version=f"crp-runner {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--api-url", default=os.environ.get("CRP_RUNNER_API_URL", DEFAULT_API_URL))
    common.add_argument(
        "--token-file",
        type=Path,
        default=os.environ.get("CRP_RUNNER_TOKEN_FILE"),
        help="file containing the API token (tokens are never accepted as arguments)",
    )
    sub.add_parser("ping", parents=[common], help="verify API connectivity and authentication")
    cap = sub.add_parser("capture", parents=[common], help="snapshot a folder and upload it")
    cap.add_argument("folder", type=Path, help="the folder to capture (read-only)")
    cap.add_argument("--project-id", required=True)
    cap.add_argument("--name", help="display name for the source (default: folder name)")
    cap.add_argument(
        "--dry-run", action="store_true", help="show what would be uploaded; send nothing"
    )
    cap.add_argument("--yes", action="store_true", help="skip the interactive upload confirmation")
    cap.add_argument(
        "--scan", action="store_true", help="start a baseline scan after the snapshot is ready"
    )
    args = parser.parse_args(argv)
    token = _read_token(args.token_file) if args.token_file else None
    with httpx.Client(timeout=30.0) as client:
        if args.command == "ping":
            return ping(args.api_url, token, client)
        if token is None:
            print("crp-runner: --token-file is required for capture", file=sys.stderr)
            return 2
        try:
            result = capture_and_upload(
                args.folder,
                args.project_id,
                PlatformClient(client, args.api_url, token),
                api_url=args.api_url,
                dry_run=args.dry_run,
                assume_yes=args.yes,
                start_scan=args.scan,
                display_name=args.name,
            )
        except (CaptureError, OSError) as exc:
            print(f"crp-runner: capture failed: {exc}", file=sys.stderr)
            return 6
        except (ApiCallError, httpx.HTTPError) as exc:
            print(f"crp-runner: API error: {exc}", file=sys.stderr)
            return 7
    if result is not None and result.get("state") not in (None, "READY"):
        return 8
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
