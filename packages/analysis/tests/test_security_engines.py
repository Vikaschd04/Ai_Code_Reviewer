"""Real Opengrep 1.30.0 and Trivy 0.69.3 (offline DB) runs on the synthetic security fixture."""

from __future__ import annotations

from pathlib import Path

import pytest

from crp_analysis.engines.base import CancelToken
from crp_analysis.engines.opengrep import OpengrepAdapter, rule_ids
from crp_analysis.engines.trivy import TrivyAdapter
from crp_analysis.frameworks import sap_commerce
from crp_analysis.normalize import normalize
from crp_core.domain.states import EngineState
from crp_devtools.testing.fixture_projects import prepare_fixture

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
ENGINES = REPO / ".local" / "engines"
SAP_PACK_RULES = set(sap_commerce.RULES) - {"crp.sap.extension.dependency-cycle"}


def _noop(_: str) -> None:
    return None


def _require(path: Path, what: str) -> Path:
    if not path.exists():
        pytest.fail(f"{what} is not installed; run `make engines` (engine tests are never skipped)")
    return path


def opengrep(home: Path | None = None, timeout: float = 300) -> OpengrepAdapter:
    return OpengrepAdapter(
        home if home is not None else _require(ENGINES / "opengrep-1.30.0", "Opengrep"),
        timeout_seconds=timeout,
        max_output_bytes=10_000_000,
        max_target_bytes=1_000_000,
    )


def trivy(home: Path | None = None, cache: Path | None = None) -> TrivyAdapter:
    return TrivyAdapter(
        home if home is not None else _require(ENGINES / "trivy-0.69.3", "Trivy"),
        cache if cache is not None else _require(ENGINES / "trivy-cache", "Trivy DB"),
        timeout_seconds=300,
        max_output_bytes=10_000_000,
    )


def _workspace(tmp_path: Path, fixture: str) -> Path:
    root = prepare_fixture(fixture, tmp_path / "work" / "src")
    (tmp_path / "work" / "home").mkdir()
    return root


def _files(root: Path, suffixes: tuple[str, ...] | None = None) -> list[str]:
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and (suffixes is None or p.suffix in suffixes)
    )


def test_every_owned_rule_fires_on_positive_and_not_on_negative_examples(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "security-mixed")
    files = _files(root, (".java", ".js", ".ts"))
    outcome = opengrep().run(root, files, cancel=CancelToken(), heartbeat=_noop)
    assert outcome.engine_version == "1.30.0"
    assert outcome.state is EngineState.SUCCEEDED, outcome.error_message
    fired = {f.rule_id for f in outcome.findings}
    expected = set(rule_ids()) - SAP_PACK_RULES  # SAP pack rules: see the SAP fixture test
    assert fired == expected, f"rules without a positive example: {expected - fired}"
    assert not [f for f in outcome.findings if f.path.endswith("SafeService.java")], (
        "negative Java examples fired"
    )
    lines = {(f.path.rsplit("/", 1)[-1], f.rule_id): f.start_line for f in outcome.findings}
    assert (
        lines[("server.js", "crp.js.command-injection.child-process")] == 5
    )  # literal call on line 9 is not flagged
    assert ("view.ts", "crp.js.xss.inner-html") in lines and lines[
        ("view.ts", "crp.js.xss.inner-html")
    ] == 2


def test_sap_pack_rules_fire_only_on_positive_examples(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "sap-commerce-mixed")
    outcome = opengrep().run(root, _files(root, (".java",)), cancel=CancelToken(), heartbeat=_noop)
    assert outcome.state is EngineState.SUCCEEDED, outcome.error_message
    found = sorted((f.rule_id, f.path.rsplit("/", 1)[-1], f.start_line) for f in outcome.findings)
    assert found == [
        ("crp.java.config.hardcoded-environment-url", "EndpointConfig.java", 11),
        ("crp.java.logging.sensitive-data", "PaymentLogger.java", 11),
        ("crp.sap.cronjob.missing-abort-check", "LoyaltyRecalculationJob.java", 18),
        ("crp.sap.flexiblesearch.string-concat", "DefaultShopProductDao.java", 16),
        ("crp.sap.flexiblesearch.unbounded-result", "DefaultShopProductDao.java", 17),
        ("crp.sap.flexiblesearch.unbounded-result", "DefaultShopProductDao.java", 34),
        ("crp.sap.interceptor.persisting-side-effect", "LoyaltyPrepareInterceptor.java", 20),
        ("crp.sap.jalo.deprecated-api", "LegacyPriceHelper.java", 3),
        ("crp.sap.jalo.deprecated-api", "LegacyPriceHelper.java", 4),
        ("crp.sap.jalo.deprecated-api", "LegacyPriceHelper.java", 10),
        ("crp.sap.model.save-in-loop", "DefaultLoyaltyService.java", 17),
    ]  # negatives: bound/paged query, saveAll, validate-only interceptor, abortable job,
    # configured endpoint, masked log line and generated Jalo sources (gensrc) stay silent
    assert {r for r, _, _ in found} == SAP_PACK_RULES


def test_nosem_comments_cannot_suppress_platform_rules(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "security-mixed")
    target = root / "web" / "src" / "server.js"
    target.write_text(
        target.read_text().replace("return eval(expression);", "return eval(expression); // nosem")
    )
    outcome = opengrep().run(root, ["web/src/server.js"], cancel=CancelToken(), heartbeat=_noop)
    assert "crp.js.code-injection.eval" in {f.rule_id for f in outcome.findings}


def test_opengrep_reports_unparseable_files(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "seeded-mixed")
    files = _files(root, (".java", ".js", ".ts", ".tsx"))
    files = [f for f in files if "node_modules" not in f]
    outcome = opengrep().run(root, files, cancel=CancelToken(), heartbeat=_noop)
    assert outcome.state in {EngineState.PARTIAL, EngineState.SUCCEEDED}
    fired = {(f.path.rsplit("/", 1)[-1], f.rule_id) for f in outcome.findings}
    assert ("CryptoUtil.java", "crp.java.crypto.hardcoded-key") in fired
    assert ("app.js", "crp.js.code-injection.eval") in fired


def test_trivy_finds_known_vulnerable_dependencies_and_secrets(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "security-mixed")
    outcome = trivy().run(root, _files(root), cancel=CancelToken(), heartbeat=_noop)
    assert outcome.engine_version == "0.69.3"
    assert outcome.state is EngineState.SUCCEEDED, outcome.error_message
    by_rule = {f.rule_id: f for f in outcome.findings}
    log4shell = by_rule["CVE-2021-44228"]
    assert log4shell.path == "pom.xml" and log4shell.anchor == "dependency"
    assert log4shell.severity == "critical" and log4shell.category == "dependencies"
    assert (
        log4shell.details and log4shell.details["package"] == "org.apache.logging.log4j:log4j-core"
    )
    assert "CVE-2020-8203" in by_rule and by_rule["CVE-2020-8203"].path == "package-lock.json"
    secrets = {(f.path, f.rule_id) for f in outcome.findings if f.rule_id.startswith("secret:")}
    assert ("web/src/config.js", "secret:github-pat") in secrets
    assert ("src/main/resources/deploy-key.txt", "secret:private-key") in secrets
    assert b"ghp_" not in (outcome.raw_report or b""), "matched secret text must not be stored"
    assert outcome.diagnostics["db_updated_at"]
    normalized = normalize("trivy", root, outcome.findings)
    dep = next(n for n in normalized if n.raw.rule_id == "CVE-2021-44228")
    assert dep.anchor == "dependency" and dep.severity == "critical" and dep.in_catalog


def test_trivy_without_database_is_unavailable(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "clean-mixed")
    outcome = trivy(cache=tmp_path / "no-db").run(
        root, _files(root), cancel=CancelToken(), heartbeat=_noop
    )
    assert outcome.state is EngineState.UNAVAILABLE
    assert "database" in (outcome.error_message or "")


def test_missing_binaries_are_unavailable(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "clean-mixed")
    for adapter in (opengrep(home=tmp_path / "none"), trivy(home=tmp_path / "none")):
        outcome = adapter.run(root, ["pom.xml"], cancel=CancelToken(), heartbeat=_noop)
        assert outcome.state is EngineState.UNAVAILABLE
        assert outcome.findings == []


def test_clean_fixture_has_no_security_findings(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "clean-mixed")
    og = opengrep().run(root, _files(root, (".java", ".ts")), cancel=CancelToken(), heartbeat=_noop)
    tv = trivy().run(root, _files(root), cancel=CancelToken(), heartbeat=_noop)
    assert og.state is EngineState.SUCCEEDED and og.findings == []
    assert tv.state is EngineState.SUCCEEDED and tv.findings == []


def test_cross_engine_duplicates_correlate_without_collapsing_distinct_occurrences(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path, "seeded-mixed")
    from crp_analysis.engines.eslint import EslintAdapter

    eslint = EslintAdapter(
        REPO / "engines" / "eslint-runner",
        node_executable=None,
        timeout_seconds=120,
        max_output_bytes=10_000_000,
    )
    target = root / "web" / "src" / "app.js"
    target.write_text(target.read_text() + "export const twice = (a, b) => [eval(a), eval(b)];\n")
    es = normalize(
        "eslint",
        root,
        eslint.run(root, ["web/src/app.js"], cancel=CancelToken(), heartbeat=_noop).findings,
    )
    og = normalize(
        "opengrep",
        root,
        opengrep().run(root, ["web/src/app.js"], cancel=CancelToken(), heartbeat=_noop).findings,
    )
    es_eval = sorted((f.start_line, f.correlation_key) for f in es if f.raw.rule_id == "no-eval")
    og_eval = sorted(
        (f.start_line, f.correlation_key)
        for f in og
        if f.raw.rule_id == "crp.js.code-injection.eval"
    )
    assert len(es_eval) == len(og_eval) == 3
    assert es_eval == og_eval, "the same eval() call must correlate across engines"
    keys = [k for _, k in es_eval]
    assert len(set(keys)) == 3, "distinct eval() calls (even on one line) must stay distinct"
    assert len({f.fingerprint for f in es + og}) == len(es) + len(og), (
        "every engine observation is kept"
    )
