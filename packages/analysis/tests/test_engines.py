"""Real PMD 7.27.0 and ESLint 10.11.0 runs on synthetic fixtures (no mocked engine output).

Failure-mode tests replace the engine executable with small fake launchers to simulate crashes,
hangs and malformed output; those are labelled test doubles, not engine-integration evidence.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from crp_analysis.engines.base import CancelToken, EngineOutcome
from crp_analysis.engines.eslint import EslintAdapter
from crp_analysis.engines.pmd import APEX, PmdAdapter, rule_ids
from crp_analysis.fixes import policy, recipes
from crp_analysis.fixes.patching import apply_edits
from crp_analysis.normalize import normalize
from crp_core.domain.states import EngineState
from crp_devtools.engines import pmd_home
from crp_devtools.testing.fixture_projects import prepare_fixture

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
ESLINT_DIR = REPO / "engines" / "eslint-runner"


def _noop(_: str) -> None:
    return None


@pytest.fixture(scope="session")
def pmd_dir() -> Path:
    home = pmd_home(REPO / ".local" / "engines")
    if not (home / "bin" / "pmd").is_file():
        pytest.fail("PMD is not installed; run `make engines` (engine tests are never skipped)")
    return home


def pmd(home: Path | None, timeout: float = 120) -> PmdAdapter:
    return PmdAdapter(home, java_heap="512m", timeout_seconds=timeout, max_output_bytes=10_000_000)


def eslint(directory: Path | None, timeout: float = 120) -> EslintAdapter:
    return EslintAdapter(
        directory, node_executable=None, timeout_seconds=timeout, max_output_bytes=10_000_000
    )


def _files(root: Path, suffixes: tuple[str, ...]) -> list[str]:
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.suffix in suffixes and "node_modules" not in p.parts
    )


def _workspace(tmp_path: Path, fixture: str) -> Path:
    root = prepare_fixture(fixture, tmp_path / "work" / "src")
    (tmp_path / "work" / "home").mkdir()
    return root


def _rules(outcome: EngineOutcome) -> set[tuple[str, str]]:
    return {(f.path.rsplit("/", 1)[-1], f.rule_id) for f in outcome.findings}


def test_pmd_seeded_fixture(pmd_dir: Path, tmp_path: Path) -> None:
    root = _workspace(tmp_path, "seeded-mixed")
    files = _files(root, (".java",))
    outcome = pmd(pmd_dir).run(root, files, cancel=CancelToken(), heartbeat=_noop)
    assert outcome.engine_version == "7.27.0"
    assert outcome.state is EngineState.PARTIAL, outcome.error_message
    assert {"InvoiceService.java", "CryptoUtil.java"} <= {
        f.path.rsplit("/", 1)[-1] for f in outcome.findings
    }
    expected = {
        ("InvoiceService.java", "UseEqualsToCompareStrings"),
        ("InvoiceService.java", "EmptyCatchBlock"),
        ("InvoiceService.java", "CloseResource"),
        ("InvoiceService.java", "UnusedLocalVariable"),
        ("InvoiceService.java", "UnusedPrivateMethod"),
        ("InvoiceService.java", "SystemPrintln"),
        ("InvoiceService.java", "ReturnEmptyCollectionRatherThanNull"),
        ("CryptoUtil.java", "HardCodedCryptoKey"),
        ("CryptoUtil.java", "InsecureCryptoIv"),
        ("CryptoUtil.java", "SystemPrintln"),
    }
    assert expected <= _rules(outcome)
    suppressed = [f for f in outcome.findings if f.suppressed_in_source]
    assert suppressed and all(f.path.endswith("CryptoUtil.java") for f in suppressed)
    (problem,) = outcome.problems
    assert problem.path.endswith("Broken.java") and problem.outcome == "FAILED"
    assert str(tmp_path) not in problem.reason
    assert str(tmp_path).encode() not in (outcome.raw_report or b"")
    assert len(outcome.attempted) == len(files)


def test_pmd_clean_fixture_is_clean(pmd_dir: Path, tmp_path: Path) -> None:
    root = _workspace(tmp_path, "clean-mixed")
    outcome = pmd(pmd_dir).run(
        root, _files(root, (".java",)), cancel=CancelToken(), heartbeat=_noop
    )
    assert outcome.state is EngineState.SUCCEEDED
    assert outcome.findings == []
    assert outcome.problems == []


def test_pmd_apex_rules_on_the_salesforce_fixture(pmd_dir: Path, tmp_path: Path) -> None:
    root = _workspace(tmp_path, "salesforce-mixed")
    adapter = PmdAdapter(
        pmd_dir, java_heap="512m", timeout_seconds=120, max_output_bytes=10_000_000, ruleset=APEX
    )
    assert adapter.name == "pmd-apex" and adapter.is_eligible("a/B.cls", "apex")
    assert not adapter.is_eligible("a/B.java", "java")
    outcome = adapter.run(
        root, _files(root, (".cls", ".trigger")), cancel=CancelToken(), heartbeat=_noop
    )
    assert outcome.engine_version == "7.27.0" and outcome.ruleset_id == "crp-pmd-apex-v1"
    assert outcome.state is EngineState.SUCCEEDED, outcome.error_message
    assert {f.rule_id for f in outcome.findings} == set(rule_ids(APEX))  # every rule has a positive
    clean = {"SafeAccountService.cls", "SafeAccountServiceTest.cls", "LoyaltyInvocable.cls"}
    assert not [f for f in outcome.findings if f.path.rsplit("/", 1)[-1] in clean]
    assert ("ReportService.cls", 21) not in {
        (f.path.rsplit("/", 1)[-1], f.start_line) for f in outcome.findings
    }  # the describe call hoisted out of the loop is not reported
    normalized = normalize("pmd-apex", root, outcome.findings)
    injection = next(n for n in normalized if n.raw.rule_id == "ApexSOQLInjection")
    assert injection.in_catalog and injection.severity == "critical"


def test_eslint_seeded_fixture(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "seeded-mixed")
    files = _files(root, (".js", ".ts", ".tsx"))
    outcome = eslint(ESLINT_DIR).run(root, files, cancel=CancelToken(), heartbeat=_noop)
    assert outcome.engine_version == "10.11.0"
    assert outcome.state is EngineState.PARTIAL
    expected = {
        ("app.js", "no-var"),
        ("app.js", "eqeqeq"),
        ("app.js", "no-eval"),
        ("app.js", "no-unused-vars"),
        ("app.js", "no-debugger"),
        ("cart.ts", "prefer-const"),
        ("cart.ts", "@typescript-eslint/no-unused-vars"),
        ("cart.ts", "no-dupe-keys"),
        ("cart.ts", "use-isnan"),
        ("widget.tsx", "no-self-compare"),
    }
    assert expected <= _rules(outcome), "the /* eslint-disable */ directive must be ignored"
    (problem,) = outcome.problems
    assert problem.path.endswith("broken.ts") and problem.outcome == "FAILED"


def test_eslint_reads_lightning_web_component_decorators(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "salesforce-mixed")
    files = [f for f in _files(root, (".js",)) if "/lwc/" in f]
    outcome = eslint(ESLINT_DIR).run(root, files, cancel=CancelToken(), heartbeat=_noop)
    assert files and outcome.state is EngineState.SUCCEEDED, outcome.problems
    assert outcome.problems == [] and outcome.findings == []  # @wire-only imports are used


def test_eslint_safe_fixes_become_fix_proposals(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "seeded-mixed")
    outcome = eslint(ESLINT_DIR).run(
        root, _files(root, (".js", ".ts")), cancel=CancelToken(), heartbeat=_noop
    )
    fixes = {f.rule_id: f for f in outcome.findings if (f.details or {}).get("autofix") is not None}
    assert set(fixes) == {"prefer-const", "no-var"}  # eqeqeq here is not provably safe
    for rule, finding in fixes.items():
        text = (root / finding.path).read_text()
        info = recipes.FindingInfo(
            "eslint", rule, finding.path, finding.start_line, finding.end_line, finding.details
        )
        proposal = recipes.propose(f"eslint:{rule}", info, text, lambda _: None)
        after = apply_edits(text, list(proposal.edits))
        assert (
            after != text
            and policy.check(finding.path, text, after, frozenset({finding.path})) == []
        )


def test_eslint_clean_fixture_is_clean(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "clean-mixed")
    outcome = eslint(ESLINT_DIR).run(
        root, _files(root, (".ts",)), cancel=CancelToken(), heartbeat=_noop
    )
    assert outcome.state is EngineState.SUCCEEDED
    assert outcome.findings == []


def test_fingerprints_are_stable_and_distinct(pmd_dir: Path, tmp_path: Path) -> None:
    root = _workspace(tmp_path, "seeded-mixed")
    files = _files(root, (".js", ".ts", ".tsx"))
    first = normalize(
        "eslint",
        root,
        eslint(ESLINT_DIR).run(root, files, cancel=CancelToken(), heartbeat=_noop).findings,
    )
    prints = [f.fingerprint for f in first]
    assert len(prints) == len(set(prints))
    target = root / "web" / "src" / "app.js"
    target.chmod(0o600)
    target.write_text("// unrelated line added above\n" + target.read_text())
    second = normalize(
        "eslint",
        root,
        eslint(ESLINT_DIR).run(root, files, cancel=CancelToken(), heartbeat=_noop).findings,
    )
    assert {f.fingerprint for f in second} == set(prints)
    assert {f.start_line for f in second if f.raw.path.endswith("app.js")} != {
        f.start_line for f in first if f.raw.path.endswith("app.js")
    }


def test_missing_engines_are_unavailable(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "clean-mixed")
    for adapter in (pmd(None), eslint(None), pmd(tmp_path / "nowhere")):
        outcome = adapter.run(root, ["x"], cancel=CancelToken(), heartbeat=_noop)
        assert outcome.state is EngineState.UNAVAILABLE
        assert outcome.error_code == "engine_unavailable"
        assert outcome.findings == []


def _fake_pmd(tmp_path: Path, script: str) -> Path:
    home = tmp_path / "fake-pmd"
    (home / "bin").mkdir(parents=True)
    (home / "lib").mkdir()
    (home / "lib" / "pmd-core-0.0.0.jar").write_bytes(b"")
    launcher = home / "bin" / "pmd"
    launcher.write_text("#!/bin/sh\n" + script)
    launcher.chmod(0o755)
    return home


def _report_arg() -> str:
    return 'for a in "$@"; do if [ "$prev" = "-r" ]; then out="$a"; fi; prev="$a"; done\n'


def test_engine_crash_is_failed_not_clean(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "clean-mixed")
    home = _fake_pmd(tmp_path, "echo 'java.lang.OutOfMemoryError' >&2\nexit 1\n")
    outcome = pmd(home).run(root, _files(root, (".java",)), cancel=CancelToken(), heartbeat=_noop)
    assert outcome.state is EngineState.FAILED
    assert outcome.error_code == "engine_crashed"
    assert outcome.attempted == [] and all(p.outcome == "NOT_ATTEMPTED" for p in outcome.problems)


def test_malformed_engine_output_is_failed(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "clean-mixed")
    home = _fake_pmd(tmp_path, _report_arg() + 'printf "{not json" > "$out"\nexit 0\n')
    outcome = pmd(home).run(root, _files(root, (".java",)), cancel=CancelToken(), heartbeat=_noop)
    assert outcome.state is EngineState.FAILED
    assert outcome.error_code == "malformed_output"


def test_engine_timeout_kills_process(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "clean-mixed")
    home = _fake_pmd(tmp_path, "sleep 30\n")
    started = time.monotonic()
    outcome = pmd(home, timeout=1).run(
        root, _files(root, (".java",)), cancel=CancelToken(), heartbeat=_noop
    )
    assert time.monotonic() - started < 15
    assert outcome.state is EngineState.FAILED
    assert outcome.error_code == "engine_timeout"


def test_engine_cancellation(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "clean-mixed")
    home = _fake_pmd(tmp_path, "sleep 30\n")
    token = CancelToken()
    threading.Timer(0.5, token.cancel).start()
    started = time.monotonic()
    outcome = pmd(home).run(root, _files(root, (".java",)), cancel=token, heartbeat=_noop)
    assert time.monotonic() - started < 15
    assert outcome.state is EngineState.CANCELED


def test_eslint_runner_crash_is_failed(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "clean-mixed")
    runner = tmp_path / "fake-eslint"
    (runner / "node_modules" / "eslint").mkdir(parents=True)
    (runner / "node_modules" / "eslint" / "package.json").write_text('{"version": "0.0.0"}')
    (runner / "trusted.config.mjs").write_text("export default [];")
    (runner / "run.mjs").write_text("process.stderr.write('boom'); process.exit(2);")
    outcome = eslint(runner).run(
        root, _files(root, (".ts",)), cancel=CancelToken(), heartbeat=_noop
    )
    assert outcome.state is EngineState.FAILED
    assert outcome.error_code == "engine_crashed"
