"""Tier 0 TypeScript type-check (P09; ADR 0017): compile-level evidence with no code executed.

The integration tests run the platform's pinned TypeScript compiler on synthetic folders,
including hostile configuration that would load code or read outside the folder if it were
followed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crp_analysis.engines.base import CancelToken
from crp_analysis.typecheck import (
    TypeChecker,
    TypeCheckResult,
    TypeDiagnostic,
    cache_key,
    compare,
    typecheck_paths,
)
from crp_analysis.workspace import written

REPO = Path(__file__).resolve().parents[3]
RUNNER = REPO / "engines" / "eslint-runner"


def _checker(runner: Path | None = RUNNER) -> TypeChecker:
    return TypeChecker(runner, node_executable=None, timeout_seconds=120, max_output_bytes=1 << 20)


def _run(tmp_path: Path, files: dict[str, str]) -> TypeCheckResult:
    with written(tmp_path / "work", {k: v.encode() for k, v in files.items()}) as root:
        return _checker().run(
            root, typecheck_paths(files), cancel=CancelToken(), heartbeat=lambda _m: None
        )


def _diag(path: str, line: int, code: str, message: str = "m") -> TypeDiagnostic:
    return TypeDiagnostic(path, line, 1, code, message)


def test_inputs_and_cache_keys() -> None:
    assert typecheck_paths(
        ["a.ts", "b.tsx", "c.js", "tsconfig.json", "x/tsconfig.json", "d.d.ts"]
    ) == [
        "a.ts",
        "b.tsx",
        "d.d.ts",
        "tsconfig.json",
    ]
    one = cache_key("tc", {"a.ts": "1" * 64})
    assert one == cache_key("tc", {"a.ts": "1" * 64})
    assert one != cache_key("tc", {"a.ts": "2" * 64}) != cache_key("tc2", {"a.ts": "1" * 64})


def test_compare_counts_new_and_fixed_errors_but_not_moved_lines() -> None:
    before = TypeCheckResult(
        "checked", "5.9.3", [_diag("a.ts", 3, "TS2322"), _diag("b.ts", 1, "TS1117")]
    )
    after = TypeCheckResult(
        "checked",
        "5.9.3",
        [_diag("a.ts", 9, "TS2322"), _diag("c.ts", 2, "TS2345"), _diag("c.ts", 4, "TS2345")],
        unresolved_imports=2,
    )
    result = compare(before, after)
    assert result["state"] == "checked" and result["tool"] == "TypeScript 5.9.3"
    assert (result["before"], result["after"], result["new_count"], result["fixed"]) == (2, 3, 2, 1)
    assert [d["path"] for d in result["new"]] == ["c.ts", "c.ts"]
    assert result["unresolved_imports"] == 2
    failed = compare(before, TypeCheckResult("timeout", "5.9.3", reason="slow"))
    assert failed == {"state": "timeout", "tool": "TypeScript 5.9.3", "reason": "slow"}
    no_base = compare(TypeCheckResult("failed", reason="broken"), after)
    assert no_base["new_count"] == 3 and no_base["fixed"] is None and no_base["reason"] == "broken"


def test_unconfigured_checker_says_why(tmp_path: Path) -> None:
    result = _checker(None).run(tmp_path, ["a.ts"], cancel=CancelToken(), heartbeat=lambda _m: None)
    assert result.state == "unavailable" and "not configured" in (result.reason or "")


@pytest.mark.integration
def test_type_errors_are_mapped_and_missing_packages_counted(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        {
            "tsconfig.json": json.dumps({"compilerOptions": {"strict": True, "jsx": "react-jsx"}}),
            "src/price.ts": "export function price(n: number): string {\n  return n;\n}\n",
            "src/use.ts": 'import { price } from "./price";\nimport React from "react";\n'
            "export const label: number = price(2);\n",
            "src/view.tsx": "export const View = () => <div>hi</div>;\n",
        },
    )
    assert result.state == "checked" and result.version == "5.9.3"
    found = {(d.path, d.line, d.code) for d in result.diagnostics}
    assert ("src/price.ts", 2, "TS2322") in found  # number returned as string
    assert ("src/use.ts", 3, "TS2322") in found  # string assigned to number
    # "react" is not installed and JSX has no types: counted, not reported as project errors.
    assert result.unresolved_imports >= 2
    assert all(d.code not in {"TS2307", "TS7026"} for d in result.diagnostics)


@pytest.mark.integration
def test_project_code_and_outside_files_are_never_used(tmp_path: Path) -> None:
    marker = tmp_path / "ran.txt"
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"compilerOptions": {"strict": False}}))
    evil = f'require("node:fs").writeFileSync({json.dumps(str(marker))}, "ran");\n'
    result = _run(
        tmp_path / "upload",
        {
            "tsconfig.json": json.dumps(
                {
                    "extends": "../../../outside.json",
                    "compilerOptions": {
                        "strict": True,
                        "plugins": [{"name": "./evil.js"}],
                        "outDir": "../../../escaped",
                    },
                }
            ),
            "evil.js": evil,
            "node_modules/evil-plugin/index.js": evil,
            "src/a.ts": 'import "../evil.js";\nexport const n: number = "x";\n',
        },
    )
    assert result.state == "checked"
    assert not marker.exists()  # nothing from the project was executed
    assert not (tmp_path / "escaped").exists()  # nothing was written
    assert any("extends" in note for note in result.notes)
    assert any("plugins" in note for note in result.notes)
    # strict (from the upload's own config) still applies; the outside file was not read.
    assert any(d.code == "TS2322" for d in result.diagnostics)
