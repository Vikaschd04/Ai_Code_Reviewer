"""Fix proposals (P05): edits and diffs, the change policy, deterministic recipes and the
validation ladder. Ladder tests with real engines are marked integration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from crp_analysis.engines.base import (
    Availability,
    CacheIdentity,
    CancelToken,
    EngineAdapter,
    EngineOutcome,
    Heartbeat,
)
from crp_analysis.engines.opengrep import OpengrepAdapter
from crp_analysis.engines.pmd import PmdAdapter
from crp_analysis.fixes import policy, recipes
from crp_analysis.fixes.patching import (
    Edit,
    PatchError,
    apply_edits,
    relocate,
    sha256_text,
    unified_diff,
)
from crp_analysis.fixes.validation import LadderInput, TargetFinding, run_ladder
from crp_devtools.testing.fixture_projects import prepare_fixture

REPO = Path(__file__).resolve().parents[3]
ENGINES = REPO / ".local" / "engines"
INVOICE = "src/main/java/com/example/billing/InvoiceService.java"


def _no_read(_: str) -> str | None:
    return None


# -- patching -----------------------------------------------------------------------------------


def test_edits_apply_exactly_or_conflict() -> None:
    text = "a\nb\nc\n"
    assert apply_edits(text, [Edit("f", 2, 2, ("b",), ("B", "B2"))]) == "a\nB\nB2\nc\n"
    assert apply_edits("a\r\nb\r\n", [Edit("f", 1, 1, ("a",), ("A",))]) == "A\r\nb\r\n"
    assert apply_edits("a\nb", [Edit("f", 2, 2, ("b",), ("B",))]) == "a\nB"  # no final newline
    with pytest.raises(PatchError) as conflict:
        apply_edits(text, [Edit("f", 2, 2, ("x",), ("y",))])
    assert conflict.value.code == "conflict"
    with pytest.raises(PatchError) as overlap:
        apply_edits(text, [Edit("f", 1, 2, ("a", "b"), ("z",)), Edit("f", 2, 2, ("b",), ("y",))])
    assert overlap.value.code == "overlapping_edits"
    with pytest.raises(PatchError):
        apply_edits(text, [Edit("f", 3, 5, ("c", "d", "e"), ())])  # beyond the file


def test_relocate_follows_moved_lines_only_when_unambiguous() -> None:
    edit = Edit("f", 2, 2, ("target()",), ("fixed()",))
    assert relocate("x\ny\ntarget()\n", edit).start_line == 3
    with pytest.raises(PatchError):
        relocate("x\ntarget(1)\n", edit)  # the lines changed
    with pytest.raises(PatchError):
        relocate("target()\ntarget()\n", edit)  # ambiguous


def test_diffs_apply_with_git_and_patch(tmp_path: Path) -> None:
    before = "one\ntwo\nthree"  # no final newline
    after = apply_edits(before, [Edit("dir/f.txt", 3, 3, ("three",), ("THREE",))])
    diff = unified_diff("dir/f.txt", before, after)
    assert "\\ No newline at end of file" in diff
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "f.txt").write_text(before)
    (tmp_path / "fix.diff").write_text("# refactorX header\n" + diff)
    subprocess.run(
        ["git", "apply", "-p1", "fix.diff"],  # noqa: S607
        cwd=tmp_path,
        check=True,
    )
    assert (tmp_path / "dir" / "f.txt").read_text() == after
    assert unified_diff("f", "same\n", "same\n") == ""


# -- policy -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "before", "after", "code"),
    [
        ("src/Other.java", "a\n", "b\n", "out_of_scope"),
        ("../etc/passwd", "a\n", "b\n", "unsafe_path"),
        ("/abs/A.java", "a\n", "b\n", "unsafe_path"),
        ("src/A.java", "x = 1;\n", "x = 1; // NOPMD\n", "suppression_added"),
        ("src/a.js", "eval(x);\n", "// eslint-disable-next-line\neval(x);\n", "suppression_added"),
        ("src/a.js", "eval(x);\n", '/* eslint no-eval: "off" */\neval(x);\n', "suppression_added"),
        ("src/a.js", "eval(x);\n", "eval(x); // nosemgrep\n", "suppression_added"),
        ("src/A.java", "x = 1;\n", "x = 1; // trivy:ignore:AVD-X-0001\n", "suppression_added"),
        (
            "src/A.java",
            "@SuppressWarnings\n",
            '@SuppressWarnings\n@SuppressWarnings("PMD")\n',
            "suppression_added",
        ),
        (
            "src/ATest.java",
            "@Test\nvoid t() { assertEquals(1, f()); }\n",
            "@Test\nvoid t() { f(); }\n",
            "test_weakened",
        ),
        ("src/T.cls", "@isTest\nstatic void t() {}\n", "static void t() {}\n", "test_weakened"),
        ("src/a.js", "it('x', () => {});\n", "it.skip('x', () => {});\n", "test_weakened"),
        ("src/a.js", "it('x', () => {});\n", "xit('x', () => {});\n", "test_weakened"),
        ("src/a.js", "describe('x', f);\n", "describe.only('x', f);\n", "test_weakened"),
        (
            "src/ATest.java",
            "@Test\nvoid t() {}\n",
            "@Disabled\n@Test\nvoid t() {}\n",
            "test_weakened",
        ),
        ("package.json", "{}\n", '{"a": 1}\n', "config_change"),
        ("src/A.java", "", "x\n" * 80, "too_large"),
    ],
)
def test_policy_refuses_changes_that_hide_problems(
    path: str, before: str, after: str, code: str
) -> None:
    allowed = frozenset({"src/A.java", "src/a.js", "src/ATest.java", "src/T.cls", "package.json"})
    assert code in {v.code for v in policy.check(path, before, after, allowed)}


def test_policy_allows_a_real_fix() -> None:
    before = 'return status == "PAID";\n'
    after = 'return "PAID".equals(status);\n'
    assert policy.check("src/A.java", before, after, frozenset({"src/A.java"})) == []


# -- recipes ------------------------------------------------------------------------------------


def _info(
    engine: str, rule: str, path: str, line: int, details: object = None
) -> recipes.FindingInfo:
    return recipes.FindingInfo(engine, rule, path, line, line, details)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('    if (status == "PAID") {', '    if ("PAID".equals(status)) {'),
        (
            '    return "PAID" != order.getStatus();',
            '    return !"PAID".equals(order.getStatus());',
        ),
        ('    ok = this.code == "A\\"B";', '    ok = "A\\"B".equals(this.code);'),
    ],
)
def test_java_string_equals_recipe(line: str, expected: str) -> None:
    text = f"class A {{\n{line}\n}}\n"
    proposal = recipes.propose(
        "java:string-literal-equals",
        _info("pmd", "UseEqualsToCompareStrings", "A.java", 2),
        text,
        _no_read,
    )
    assert apply_edits(text, list(proposal.edits)).splitlines()[1] == expected
    assert proposal.behaviour_note


@pytest.mark.parametrize(
    "line",
    ["    return a == b;", '    return a == "x" || b == "y";', '    return "a" == "b";'],
)
def test_java_recipe_only_handles_one_literal_comparison(line: str) -> None:
    with pytest.raises(recipes.NoFix):
        recipes.propose(
            "java:string-literal-equals",
            _info("pmd", "UseEqualsToCompareStrings", "A.java", 1),
            line + "\n",
            _no_read,
        )


def test_eslint_fix_uses_utf16_offsets_and_verifies_them() -> None:
    text = 'const icon = "🙂"; let total = 1;\nconsole.log(icon, total);\n'
    start = len(text[: text.index("let")].encode("utf-16-le")) // 2  # JS string offset
    details = {"autofix": {"start": start, "end": start + 3, "text": "const", "original": "let"}}
    proposal = recipes.propose(
        "eslint:prefer-const", _info("eslint", "prefer-const", "a.js", 1, details), text, _no_read
    )
    assert apply_edits(text, list(proposal.edits)).startswith('const icon = "🙂"; const total')
    wrong = {"autofix": {"start": start, "end": start + 3, "text": "const", "original": "var"}}
    with pytest.raises(recipes.NoFix):
        recipes.propose(
            "eslint:prefer-const", _info("eslint", "prefer-const", "a.js", 1, wrong), text, _no_read
        )
    with pytest.raises(recipes.NoFix):  # ESLint offered no fix for this occurrence
        recipes.propose("eslint:eqeqeq", _info("eslint", "eqeqeq", "a.js", 1), text, _no_read)


def test_salesforce_api_version_recipe_needs_a_supported_project_version() -> None:
    meta = '<?xml version="1.0"?>\n<ApexClass>\n    <apiVersion>29.0</apiVersion>\n</ApexClass>\n'
    info = _info("frameworks", "crp.sf.metadata.retired-api-version", "cls/A.cls-meta.xml", 3)

    def project(version: str) -> recipes.ReadFile:
        return lambda name: f'{{"sourceApiVersion": "{version}"}}' if name else None

    proposal = recipes.propose("salesforce:api-version", info, meta, project("62.0"))
    assert "<apiVersion>62.0</apiVersion>" in apply_edits(meta, list(proposal.edits))
    assert proposal.behaviour_note and "org" in proposal.behaviour_note
    for read in (project("29.0"), _no_read):
        with pytest.raises(recipes.NoFix):
            recipes.propose("salesforce:api-version", info, meta, read)


def test_recipe_options_by_rule() -> None:
    assert recipes.options(_info("eslint", "eqeqeq", "a.js", 1)) == ["eslint:eqeqeq"]
    assert recipes.options(_info("eslint", "no-eval", "a.js", 1)) == []
    assert (
        recipes.options(_info("opengrep", "crp.java.sql-injection.string-concat", "A.java", 1))
        == []
    )


# -- ladder with real engines -----------------------------------------------------------------------


def _engines() -> dict[str, EngineAdapter]:
    home = ENGINES / "pmd-bin-7.27.0"
    if not (home / "bin" / "pmd").is_file():
        pytest.fail("PMD is not installed; run `make engines` (engine tests are never skipped)")
    return {
        "pmd": PmdAdapter(home, java_heap="512m", timeout_seconds=120, max_output_bytes=10_000_000),
        "opengrep": OpengrepAdapter(
            ENGINES / "opengrep-1.30.0",
            timeout_seconds=120,
            max_output_bytes=10_000_000,
            max_target_bytes=1_000_000,
        ),
    }


def _ladder(text: str, replacement: str, **overrides: object) -> LadderInput:
    line = 10
    original = text.splitlines()[line - 1]
    edits = (Edit(INVOICE, line, line, (original,), (replacement,)),)
    after = apply_edits(text, list(edits))
    values: dict[str, object] = {
        "path": INVOICE,
        "language": "java",
        "base_text": text,
        "edits": edits,
        "base_sha256": sha256_text(text),
        "result_sha256": sha256_text(after),
        "patch_sha256": sha256_text(unified_diff(INVOICE, text, after)),
        "allowed_paths": frozenset({INVOICE}),
        "finding": TargetFinding("pmd", "UseEqualsToCompareStrings", line),
    }
    values.update(overrides)
    return LadderInput(**values)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def invoice(tmp_path_factory: pytest.TempPathFactory) -> str:
    root = prepare_fixture("seeded-mixed", tmp_path_factory.mktemp("fx") / "src")
    return (root / INVOICE).read_text()


def _states(result: object) -> dict[str, str]:
    return {step.id: step.state for step in result.steps}  # type: ignore[attr-defined]


@pytest.mark.integration
def test_ladder_passes_a_correct_small_repair(invoice: str, tmp_path: Path) -> None:
    line = invoice.splitlines()[9]
    result = run_ladder(
        _ladder(invoice, line.replace('status == "PAID"', '"PAID".equals(status)')),
        _engines(),
        tmp_path,
        cancel=CancelToken(),
    )
    assert result.passed, result.summary
    assert _states(result) == {
        "integrity": "passed",
        "syntax": "passed",
        "checks": "passed",
        "tests": "not_run",
        "build": "not_run",
    }
    assert "Not compiled" in result.summary or "not compiled" in result.summary


@pytest.mark.integration
@pytest.mark.parametrize(
    ("replacement", "failed_step", "text"),
    [
        ('        if ("PAID".equals(status) {', "syntax", "syntax errors"),
        ('        if (status == "PAID") {', "checks", "still reported"),
        (
            '        if ("PAID".equals(status)) { System.out.println(status);',
            "checks",
            "new findings",
        ),
    ],
)
def test_ladder_rejects_broken_ineffective_and_regressing_fixes(
    invoice: str, tmp_path: Path, replacement: str, failed_step: str, text: str
) -> None:
    ladder = _ladder(invoice, replacement)
    if failed_step == "checks" and "still" in text:
        ladder = _ladder(invoice, replacement + " ")  # a change that keeps the problem
    result = run_ladder(ladder, _engines(), tmp_path, cancel=CancelToken())
    assert not result.passed
    step = next(s for s in result.steps if s.id == failed_step)
    assert step.state == "failed" and text in step.detail, step.detail


@pytest.mark.integration
def test_ladder_detects_stale_base_and_tampered_patches(invoice: str, tmp_path: Path) -> None:
    good = invoice.splitlines()[9].replace('status == "PAID"', '"PAID".equals(status)')
    stale = run_ladder(
        _ladder(invoice, good, base_sha256="0" * 64), _engines(), tmp_path, cancel=CancelToken()
    )
    assert _states(stale)["integrity"] == "failed" and "stale base" in stale.summary
    assert _states(stale)["checks"] == "not_run"
    tampered = run_ladder(
        _ladder(invoice, good, patch_sha256="1" * 64), _engines(), tmp_path, cancel=CancelToken()
    )
    assert "patch hash" in tampered.steps[0].detail


class _Recording:
    """A real adapter that also records every file in the directory it is asked to check."""

    def __init__(self, inner: EngineAdapter) -> None:
        self._inner = inner
        self.name = inner.name
        self.ruleset_id = inner.ruleset_id
        self.seen: list[list[str]] = []

    def is_eligible(self, path: str, language: str | None) -> bool:
        return self._inner.is_eligible(path, language)

    def availability(self) -> Availability:
        return self._inner.availability()

    def enabled_rules(self) -> tuple[str, ...] | None:
        return self._inner.enabled_rules()

    def cache_identity(self) -> CacheIdentity | None:
        return self._inner.cache_identity()

    def run(
        self, root: Path, files: list[str], *, cancel: CancelToken, heartbeat: Heartbeat
    ) -> EngineOutcome:
        self.seen.append(sorted(p.relative_to(root).as_posix() for p in root.rglob("*")))
        return self._inner.run(root, files, cancel=cancel, heartbeat=heartbeat)


@pytest.mark.integration
def test_ladder_copies_only_the_changed_file(invoice: str, tmp_path: Path) -> None:
    """The upload's build scripts, tests and analyzer configs never reach the ladder's work area,
    so nothing can run them there; the copies are removed afterwards. (The worker tests also
    plant hostile build and test files in real uploads.)"""
    pmd = _Recording(_engines()["pmd"])
    good = invoice.splitlines()[9].replace('status == "PAID"', '"PAID".equals(status)')
    work = tmp_path / "work"
    result = run_ladder(_ladder(invoice, good), {"pmd": pmd}, work, cancel=CancelToken())
    assert result.passed, result.summary
    tree = sorted({*_parents(INVOICE), INVOICE})
    assert pmd.seen == [tree, tree]  # the original copy, then the patched copy
    assert not list(work.rglob("*"))


def _parents(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


@pytest.mark.integration
def test_ladder_cancellation(invoice: str, tmp_path: Path) -> None:
    cancel = CancelToken()
    cancel.cancel()
    good = invoice.splitlines()[9].replace('status == "PAID"', '"PAID".equals(status)')
    result = run_ladder(_ladder(invoice, good), _engines(), tmp_path, cancel=cancel)
    assert result.canceled and not result.passed
