"""``crp-dev``: trusted-development command line (wrapped by the Makefile targets)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from crp_core.config import describe_settings_error
from crp_core.local_secrets import SecretFileError, read_secret_file
from crp_devtools import context, stack
from crp_devtools.dbtasks import migrate_and_provision, seed_fixtures, settings_from_env
from crp_devtools.doctor import run_doctor
from crp_devtools.infra import InfraError
from crp_devtools.localenv import DevPorts, dev_service_env
from crp_devtools.paths import DevPaths, find_repo_root

CONTRACT_PATH = Path("packages") / "contracts" / "openapi.json"


def _paths() -> DevPaths:
    return DevPaths(find_repo_root())


def cmd_bootstrap_local(args: argparse.Namespace) -> int:
    created = stack.bootstrap_local(_paths(), DevPorts())
    print("crp-dev: local state ready" + (f" (created: {', '.join(created)})" if created else ""))
    return 0


def cmd_infra(args: argparse.Namespace) -> int:
    paths, ports = _paths(), DevPorts()
    if args.action == "up":
        stack.infra_up(paths, ports)
    elif args.action == "down":
        stack.infra_down(paths, ports)
    for name, state in stack.infra_status(paths, ports).items():
        print(f"{name:9} {state}")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    paths = _paths()
    revision, identity = migrate_and_provision(
        settings_from_env(dev_service_env(paths, DevPorts()))
    )
    print(f"crp-dev: schema at {revision}; local workspace {identity.workspace_id}")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    created = seed_fixtures(settings_from_env(dev_service_env(_paths(), DevPorts())))
    print("crp-dev: synthetic fixture project " + ("created" if created else "already present"))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    return run_doctor(_paths(), DevPorts())


def cmd_dev(args: argparse.Namespace) -> int:
    return stack.run_dev(_paths(), DevPorts(), keep_infra=args.keep_infra)


def cmd_token(args: argparse.Namespace) -> int:
    paths = _paths()
    token = read_secret_file(paths.token_file)
    if args.show:
        print(token)
    else:
        print(f"Local API token file: {paths.token_file.relative_to(paths.repo)}")
        print("Print it with: uv run crp-dev token --show (keep it private)")
    return 0


def cmd_contracts(args: argparse.Namespace) -> int:
    from crp_api.openapi_export import render_openapi

    target = _paths().repo / CONTRACT_PATH
    rendered = render_openapi()
    if args.check:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != rendered:
            print(f"crp-dev: {CONTRACT_PATH} is stale; run 'make contracts'", file=sys.stderr)
            return 1
        print(f"crp-dev: {CONTRACT_PATH} matches the API")
        return 0
    target.write_text(rendered, encoding="utf-8")
    print(f"crp-dev: wrote {CONTRACT_PATH}")
    return 0


def cmd_context_map(args: argparse.Namespace) -> int:
    paths = _paths()
    result = context.build_context_map(
        paths.repo, max_files=args.max_files, max_symbols=args.max_symbols
    )
    out = paths.context_cache / f"map-{str(result['worktree_digest'])[:16]}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    truncation = result["truncation"]
    print(
        f"crp-dev: context map {out.relative_to(paths.repo)}\n"
        f"  files indexed: {result['file_count']} (listed: {len(result['files'])})\n"  # type: ignore[arg-type]
        f"  excluded: {json.dumps(result['excluded'], sort_keys=True)}\n"
        f"  truncated: {result['truncated']} {json.dumps(truncation)}"
    )
    return 0


def cmd_context_pack(args: argparse.Namespace) -> int:
    paths = _paths()
    result = context.build_context_pack(
        paths.repo,
        task=args.task,
        approved=args.path,
        out_dir=paths.context_cache / "packs",
        max_bytes=args.max_bytes,
        max_file_bytes=args.max_file_bytes,
    )
    print(
        f"crp-dev: context pack {result.path.relative_to(paths.repo)} "
        f"({'cache hit' if result.cache_hit else 'generated'}; truncated: {result.truncated})"
    )
    return 0


def cmd_engines(args: argparse.Namespace) -> int:
    from crp_devtools.engines import (
        OPENGREP,
        PMD,
        TRIVY,
        download_trivy_db,
        install_binary,
        install_pmd,
        warm_opengrep,
    )

    paths = _paths()
    engines = paths.state / "engines"
    home = install_pmd(engines)
    print(f"crp-dev: PMD {PMD.version} ready at {home.relative_to(paths.repo)} (sha256 verified)")
    opengrep_home, status = install_binary(
        engines, OPENGREP, verify_signatures=args.verify_signatures
    )
    warm_opengrep(opengrep_home)
    print(f"crp-dev: Opengrep {OPENGREP.version} ready ({status})")
    trivy_home, status = install_binary(engines, TRIVY, verify_signatures=args.verify_signatures)
    print(f"crp-dev: Trivy {TRIVY.version} ready ({status})")
    if args.skip_trivy_db:
        print("crp-dev: Trivy vulnerability DB download skipped (Trivy will report UNAVAILABLE)")
    else:
        meta = download_trivy_db(trivy_home, engines / "trivy-cache")
        print(f"crp-dev: Trivy DB updated {meta.get('UpdatedAt')} (offline scans)")
    runner = paths.repo / "engines" / "eslint-runner" / "node_modules" / "eslint"
    if not runner.is_dir():
        print("crp-dev: ESLint runner dependencies missing; run `pnpm install`", file=sys.stderr)
        return 1
    print("crp-dev: ESLint runner ready (engines/eslint-runner)")
    return 0


def cmd_benchmark(args: argparse.Namespace) -> int:
    from crp_devtools.benchmark import run_benchmark

    out = run_benchmark(_paths(), java_per_module=args.java_files, ts_per_package=args.ts_files)
    data = json.loads(out.read_text())
    print(f"crp-dev: benchmark written to {out.relative_to(_paths().repo)}")
    print(json.dumps({k: data[k] for k in ("snapshot", "intake_seconds", "graph")}, indent=2))
    for name in ("cold_scan", "warm_scan"):
        scan = data[name]
        print(
            f"{name}: {scan['state']} in {scan['wall_seconds']} s, worker tree peak "
            f"{scan['worker_tree_peak_rss_mb']} MB, {scan['findings']} findings"
        )
    print(json.dumps(data["api_latency_single_request"], indent=2))
    return 0


def cmd_test_e2e(args: argparse.Namespace) -> int:
    return stack.run_e2e(_paths(), args.playwright_args)


def cmd_package(args: argparse.Namespace) -> int:
    paths = _paths()
    dist = stack.package(paths)
    print(f"crp-dev: packages written to {dist.relative_to(paths.repo)} (see MANIFEST.json)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crp-dev", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "bootstrap-local", help="generate local secrets and the dev PostgreSQL cluster"
    ).set_defaults(func=cmd_bootstrap_local)
    infra = sub.add_parser("infra", help="manage dev PostgreSQL/Temporal")
    infra.add_argument("action", choices=["up", "down", "status"])
    infra.set_defaults(func=cmd_infra)
    sub.add_parser(
        "migrate", help="apply migrations and provision the local identity"
    ).set_defaults(func=cmd_migrate)
    sub.add_parser("seed-fixtures", help="create labelled synthetic fixtures").set_defaults(
        func=cmd_seed
    )
    sub.add_parser("doctor", help="check prerequisites and services").set_defaults(func=cmd_doctor)
    dev = sub.add_parser("dev", help="run the full local stack in the foreground")
    dev.add_argument(
        "--keep-infra", action="store_true", help="leave PostgreSQL/Temporal running on exit"
    )
    dev.set_defaults(func=cmd_dev)
    token = sub.add_parser("token", help="show where the local API token is stored")
    token.add_argument("--show", action="store_true", help="print the token value")
    token.set_defaults(func=cmd_token)
    contracts = sub.add_parser("contracts", help="export the OpenAPI contract")
    contracts.add_argument(
        "--check", action="store_true", help="fail if the committed contract is stale"
    )
    contracts.set_defaults(func=cmd_contracts)
    cmap = sub.add_parser("context-map", help="bounded development repository map")
    cmap.add_argument("--max-files", type=int, default=400)
    cmap.add_argument("--max-symbols", type=int, default=40)
    cmap.set_defaults(func=cmd_context_map)
    cpack = sub.add_parser("context-pack", help="scoped context packet for a task")
    cpack.add_argument("--task", required=True)
    cpack.add_argument("--path", action="append", required=True, help="approved path (repeatable)")
    cpack.add_argument("--max-bytes", type=int, default=60_000)
    cpack.add_argument("--max-file-bytes", type=int, default=12_000)
    cpack.set_defaults(func=cmd_context_pack)
    engines = sub.add_parser("engines", help="install pinned analysis engines and the Trivy DB")
    engines.add_argument(
        "--verify-signatures",
        action="store_true",
        help="also verify sigstore signatures with cosign",
    )
    engines.add_argument(
        "--skip-trivy-db", action="store_true", help="do not download the vulnerability DB"
    )
    engines.set_defaults(func=cmd_engines)
    bench = sub.add_parser("benchmark", help="time/memory of real scans on a medium fixture")
    bench.add_argument("--java-files", type=int, default=150, help="Java files per module (4)")
    bench.add_argument("--ts-files", type=int, default=200, help="TS files per package (2)")
    bench.set_defaults(func=cmd_benchmark)
    e2e = sub.add_parser("test-e2e", help="run Playwright against an isolated stack")
    e2e.add_argument("playwright_args", nargs=argparse.REMAINDER)
    e2e.set_defaults(func=cmd_test_e2e)
    sub.add_parser("package", help="build wheels and the web bundle into dist/").set_defaults(
        func=cmd_package
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        code: int = args.func(args)
    except ValidationError as exc:
        print(f"crp-dev {args.command}: {describe_settings_error(exc)}", file=sys.stderr)
        return 2
    except (InfraError, SecretFileError, context.ContextError) as exc:
        print(f"crp-dev {args.command}: {exc}", file=sys.stderr)
        return 2
    return code


if __name__ == "__main__":
    raise SystemExit(main())
