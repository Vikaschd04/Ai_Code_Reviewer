from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from crp_analysis import policy
from crp_analysis.architecture.smells import RULES as SMELL_RULES
from crp_analysis.catalog import all_rules, lookup
from crp_analysis.engines.eslint import EslintAdapter
from crp_analysis.engines.frameworks import RULES as FRAMEWORK_RULES
from crp_analysis.engines.opengrep import rule_ids as opengrep_rule_ids
from crp_analysis.engines.pmd import APEX, ruleset_path
from crp_analysis.engines.pmd import rule_ids as pmd_rule_ids
from crp_analysis.inventory import build_inventory
from crp_analysis.manifest import ManifestEntry
from crp_analysis.nfr.config import RULES as NFR_RULES
from crp_analysis.paths import CollisionTracker, safe_display
from crp_analysis.structure import extract
from crp_core.domain.states import FileDisposition, Severity

REPO = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("path", "reason", "category"),
    [
        ("src/Main.java", None, "source"),
        ("src/test/java/MainTest.java", None, "test"),
        ("web/app.spec.ts", None, "test"),
        ("pom.xml", None, "build"),
        ("config/app.yml", None, "config"),
        ("docs/guide.md", None, "docs"),
        ("CLAUDE.md", None, "agent_instructions"),
        ("sub/.env.local", "secret_candidate", "excluded"),
        ("sub/.env.example", None, "other"),
        ("id_rsa", "secret_candidate", "excluded"),
        ("target/classes/A.class", "build_output", "excluded"),
        ("a/node_modules/b/c.js", "dependency_vendor", "excluded"),
        ("dist/bundle.min.js", "build_output", "excluded"),
        ("web/lib.min.js", "generated_minified", "excluded"),
    ],
)
def test_policy_classification(path: str, reason: str | None, category: str) -> None:
    result = policy.classify(path)
    assert result.excluded_reason == reason
    assert result.category == category


def test_collision_tracker_detects_file_directory_conflicts() -> None:
    tracker = CollisionTracker()
    assert tracker.add("a/b.java") is None
    assert tracker.add("A/B.JAVA") == "a/b.java"
    assert tracker.add("a") is not None


def test_safe_display_escapes_control_characters() -> None:
    assert safe_display("bad\nname\x1b[31m") == "bad\\x0aname\\x1b[31m"


def _entry(path: str, language: str | None, lines: int = 10) -> ManifestEntry:
    return ManifestEntry(
        path,
        FileDisposition.ANALYZABLE,
        None,
        100,
        "a" * 64,
        language,
        policy.classify(path).category,
        lines,
    )


def test_inventory_reads_declared_versions_without_executing() -> None:
    entries = [
        _entry("src/A.java", "java", 40),
        _entry("web/a.ts", "typescript", 12),
        _entry("AGENTS.md", "markdown"),
    ]
    texts = {
        "pom.xml": """<project xmlns="http://maven.apache.org/POM/4.0.0"><parent>
            <artifactId>spring-boot-starter-parent</artifactId><version>3.3.4</version></parent>
            <properties><maven.compiler.release>17</maven.compiler.release></properties></project>""",
        "package.json": json.dumps(
            {
                "engines": {"node": ">=20"},
                "dependencies": {"react": "^18.3.1"},
                "devDependencies": {"typescript": "^5.6.2", "eslint": "^9"},
            }
        ),
        "tsconfig.json": '{ // comment\n "compilerOptions": { "target": "ES2022" } }',
        "sub/build.gradle": "java { toolchain { languageVersion = JavaLanguageVersion.of(21) } }",
        "sfdx-project.json": '{"sourceApiVersion": "61.0"}',
    }
    inventory = build_inventory(entries, texts)
    found = {(i["name"], i["version"], i["confidence"]) for i in inventory["indicators"]}  # type: ignore[union-attr]
    assert ("Java", "17", "declared") in found
    assert ("Spring Boot", "3.3.4", "declared") in found
    assert ("React", "^18.3.1", "declared") in found
    assert ("TypeScript", "^5.6.2", "declared") in found
    assert ("Node.js", ">=20", "declared") in found
    assert ("TypeScript config", "ES2022", "declared") in found
    assert ("Java", "21", "declared") in found
    assert ("Salesforce DX project", "61.0", "declared") in found
    assert any(i["name"] == "Project ESLint config" for i in inventory["indicators"])  # type: ignore[union-attr]
    assert inventory["agent_instruction_files"] == ["AGENTS.md"]
    assert inventory["languages"][0] == {"language": "java", "files": 1, "lines": 40}  # type: ignore[index]


def test_inventory_refuses_xml_doctype() -> None:
    evil = '<?xml version="1.0"?><!DOCTYPE p [<!ENTITY x SYSTEM "file:///etc/passwd">]><project>&x;</project>'
    inventory = build_inventory([], {"pom.xml": evil})
    (indicator,) = inventory["indicators"]  # type: ignore[misc]
    assert indicator["confidence"] == "unknown"


def test_java_structure_symbols_and_spans() -> None:
    source = b"package a;\nimport java.util.List;\npublic class Svc {\n  public Svc() {}\n  void run() {}\n  interface Inner { void x(); }\n}\n"
    result = extract(source, "a/Svc.java", "java", max_symbols=100)
    assert result.status == "OK"
    kinds = {(s.kind, s.name, s.container) for s in result.symbols}
    assert ("class", "Svc", None) in kinds
    assert ("constructor", "Svc", "Svc") in kinds
    assert ("method", "run", "Svc") in kinds
    assert ("interface", "Inner", "Svc") in kinds
    assert ("import", "import java.util.List", None) in kinds
    run = next(s for s in result.symbols if s.name == "run")
    assert (run.start_line, run.end_line) == (5, 5)


@pytest.mark.parametrize(
    ("path", "language", "source", "expected"),
    [
        (
            "a.js",
            "javascript",
            b"export function f() {}\nconst g = () => 1;\nclass K { m() {} }\n",
            {"f", "g", "K", "m"},
        ),
        (
            "a.ts",
            "typescript",
            b"interface I { a: number }\ntype T = string;\nexport enum E { A }\n",
            {"I", "T", "E"},
        ),
        ("a.tsx", "typescript", b"export const C = () => <div/>;\n", {"C"}),
    ],
)
def test_js_ts_structure(path: str, language: str, source: bytes, expected: set[str]) -> None:
    result = extract(source, path, language, max_symbols=100)
    assert result.status == "OK"
    assert expected <= {s.name for s in result.symbols}


def test_syntax_errors_give_partial_parse() -> None:
    result = extract(b"class Broken { void m( { }", "B.java", "java", max_symbols=100)
    assert result.status == "PARTIAL"
    assert result.error_count >= 1


def test_symbol_budget_is_disclosed() -> None:
    source = "".join(f"function f{i}() {{}}\n" for i in range(20)).encode()
    result = extract(source, "many.js", "javascript", max_symbols=5)
    assert len(result.symbols) == 5
    assert result.truncated


def test_unsupported_languages_are_reported() -> None:
    assert extract(b"x", "a.py", "python", max_symbols=10).status == "UNSUPPORTED"


def test_every_enabled_rule_has_a_catalog_entry() -> None:
    catalog = all_rules()
    pmd_rules = re.findall(
        r'ref="category/java/[a-z]+\.xml/([A-Za-z]+)"', ruleset_path().read_text()
    )
    assert pmd_rules and all(f"pmd:{rule}" in catalog for rule in pmd_rules)
    assert len(pmd_rules) == sum(1 for k in catalog if k.startswith("pmd:"))
    config = (REPO / "engines" / "eslint-runner" / "trusted.config.mjs").read_text()
    eslint_rules = set(
        re.findall(r'"((?:@typescript-eslint/)?[a-z-]+)": (?:"error"|\["error")', config)
    )
    assert eslint_rules and all(f"eslint:{rule}" in catalog for rule in eslint_rules)
    assert (
        set(
            EslintAdapter(
                REPO / "engines" / "eslint-runner",
                node_executable=None,
                timeout_seconds=1,
                max_output_bytes=1,
            ).enabled_rules()
            or ()
        )
        == eslint_rules
    )
    assert set(pmd_rule_ids()) == set(pmd_rules)
    opengrep = set(opengrep_rule_ids())
    assert len(opengrep) == 24 and {f"opengrep:{r}" for r in opengrep} == {
        k for k in catalog if k.startswith("opengrep:")
    }
    assert {f"pmd-apex:{r}" for r in pmd_rule_ids(APEX)} == {
        k for k in catalog if k.startswith("pmd-apex:")
    }
    assert {f"frameworks:{r}" for r in FRAMEWORK_RULES} == {
        k for k in catalog if k.startswith("frameworks:")
    }
    assert {f"smells:{r}" for r in SMELL_RULES} == {k for k in catalog if k.startswith("smells:")}
    assert {f"nfr:{r}" for r in NFR_RULES} == {k for k in catalog if k.startswith("nfr:")}
    for info in catalog.values():
        assert info.explanation and info.recommendation and info.severity_rationale and info.url


def test_uncatalogued_rules_are_explicit() -> None:
    info = lookup("pmd", "SomeNewRule", "1", "https://example.invalid/rule")
    assert info.in_catalog is False
    assert info.severity is Severity.HIGH
