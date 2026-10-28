"""Dependency upgrades as fixes (ADD-A2): the version that fixes every reported vulnerability of a
Maven library, written where the version is set in ``pom.xml`` (and nowhere it is not)."""

from __future__ import annotations

import pytest

from crp_analysis.fixes.dependency_recipes import (
    applies,
    fix_for,
    maven_upgrade,
    target_version,
    version_key,
)
from crp_analysis.fixes.patching import apply_edits
from crp_analysis.fixes.recipes import FindingInfo, NoFix

LOG4J = "org.apache.logging.log4j:log4j-core"
# Trivy's fixed versions for log4j-core 2.14.1 (Log4Shell and its follow-ups).
FIXES = ["2.15.0, 2.12.2", "2.16.0, 2.12.2", "2.17.0, 2.12.3, 2.3.1", "2.17.1, 2.12.4, 2.3.2"]


def _pom(dependencies: str, extra: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<project>\n  <modelVersion>4.0.0</modelVersion>\n'
        f"{extra}  <dependencies>\n{dependencies}  </dependencies>\n</project>\n"
    )


def _dep(group: str, artifact: str, version: str | None, indent: str = "    ") -> str:
    inner = f"{indent}  <groupId>{group}</groupId>\n{indent}  <artifactId>{artifact}</artifactId>\n"
    if version is not None:
        inner += f"{indent}  <version>{version}</version>\n"
    return f"{indent}<dependency>\n{inner}{indent}</dependency>\n"


def _finding(fixed: str = FIXES[0], others: list[str] | None = None) -> FindingInfo:
    details: dict[str, object] = {
        "package": LOG4J,
        "installed_version": "2.14.1",
        "fixed_version": fixed,
        "vulnerability_id": "CVE-2021-44228",
    }
    if others is not None:
        details["fixed_versions"] = others
    return FindingInfo("trivy", "CVE-2021-44228", "pom.xml", None, None, details)


def test_versions_order_like_maven() -> None:
    assert version_key("2.17") == version_key("2.17.0") == version_key("2.17.0.RELEASE")
    assert version_key("2.17.1-rc1") < version_key("2.17.1") < version_key("2.17.1.1")
    assert version_key("2.12.4") < version_key("2.17.1") < version_key("10.0")
    assert (
        version_key("1.0-beta2")
        < version_key("1.0-rc1")
        < version_key("1.0")
        < version_key("1.0-sp1")
    )


def test_the_target_fixes_every_vulnerability_on_the_same_major_version() -> None:
    assert fix_for("2.14.1", "2.15.0, 2.12.2") == "2.15.0"
    assert fix_for("2.12.1", "2.15.0, 2.12.2") == "2.12.2"  # the nearest newer line
    assert fix_for("1.2.3", "2.0.0-rc1, 2.0.0") == "2.0.0"  # never a pre-release
    assert fix_for("2.17.1", "2.15.0, 2.12.2") is None  # already past every fix
    assert target_version("2.14.1", FIXES) == "2.17.1"
    assert target_version("2.12.1", FIXES) == "2.12.4"
    assert target_version("2.14.1", ["", "garbage"]) is None


def test_a_direct_version_is_upgraded_for_all_of_the_librarys_vulnerabilities() -> None:
    text = _pom(_dep("org.apache.logging.log4j", "log4j-core", "2.14.1"))
    proposal = maven_upgrade(_finding(others=FIXES), text)
    after = apply_edits(text, list(proposal.edits))
    assert "<version>2.17.1</version>" in after and "2.14.1" not in after
    assert proposal.title == "Upgrade log4j-core 2.14.1 to 2.17.1"
    assert "lowest version that fixes" in proposal.explanation
    assert proposal.behaviour_note and "Compile and run the tests" in proposal.behaviour_note
    single = maven_upgrade(_finding(), text)  # without the other findings: this one's fix
    assert "<version>2.15.0</version>" in apply_edits(text, list(single.edits))


def test_a_shared_version_property_and_the_management_section_are_upgraded() -> None:
    properties = "  <properties>\n    <log4j.version>2.14.1</log4j.version>\n  </properties>\n"
    text = _pom(
        _dep("org.apache.logging.log4j", "log4j-api", "${log4j.version}")
        + _dep("org.apache.logging.log4j", "log4j-core", "${log4j.version}"),
        properties,
    )
    proposal = maven_upgrade(_finding(others=FIXES), text)
    after = apply_edits(text, list(proposal.edits))
    assert "<log4j.version>2.17.1</log4j.version>" in after
    assert after.count("${log4j.version}") == 2
    assert "log4j.version is shared" in proposal.explanation
    managed = (
        "  <dependencyManagement>\n    <dependencies>\n"
        + _dep("org.apache.logging.log4j", "log4j-core", "2.14.1", "      ")
        + "    </dependencies>\n  </dependencyManagement>\n"
    )
    text = _pom(_dep("org.apache.logging.log4j", "log4j-core", None), managed)
    after = apply_edits(text, list(maven_upgrade(_finding(), text).edits))
    assert "<version>2.15.0</version>" in after


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (_pom(_dep("org.apache.logging.log4j", "log4j-core", None)), "parent POM"),
        (_pom(_dep("org.slf4j", "slf4j-api", "2.0.9")), "not declared in this file"),
        (_pom(_dep("org.apache.logging.log4j", "log4j-core", "${missing}")), "not set"),
        (_pom(_dep("org.apache.logging.log4j", "log4j-core", "2.17.1")), "already at 2.17.1"),
        (
            _pom(
                "",
                "  <build>\n    <plugins>\n      <plugin>\n        <dependencies>\n"
                + _dep("org.apache.logging.log4j", "log4j-core", "2.14.1", "          ")
                + "        </dependencies>\n      </plugin>\n    </plugins>\n  </build>\n",
            ),
            "not declared in this file",  # a build plugin's own dependency is left alone
        ),
    ],
)
def test_versions_that_are_not_set_here_are_not_guessed(text: str, reason: str) -> None:
    with pytest.raises(NoFix, match=reason):
        maven_upgrade(_finding(), text)


def test_only_maven_vulnerabilities_with_a_newer_fix_apply() -> None:
    text = _pom(_dep("org.apache.logging.log4j", "log4j-core", "2.14.1"))
    with pytest.raises(NoFix, match="no fixed version"):
        maven_upgrade(_finding(fixed="2.12.2, 2.3.1"), text)
    assert applies(_finding())
    lock = FindingInfo("trivy", "CVE-1", "web/package-lock.json", None, None, {"package": "lodash"})
    assert not applies(lock)  # lockfile integrity comes from the registry: fixed by hand
    assert not applies(FindingInfo("trivy", "CVE-1", "pom.xml", None, None, {"package": LOG4J}))
