"""Framework packs (P04) on the synthetic SAP Commerce and Salesforce fixtures: secure metadata
parsing, conservative version detection, evidence-backed mappings, configuration checks and
catalog coverage. Nothing is executed; inputs are never modified."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from crp_analysis.catalog import all_rules
from crp_analysis.engines.base import CancelToken
from crp_analysis.engines.frameworks import CYCLE, RETIRED, FrameworkRulesAdapter
from crp_analysis.engines.frameworks import RULES as CONFIG_RULES
from crp_analysis.engines.opengrep import rule_ids as opengrep_rule_ids
from crp_analysis.engines.pmd import APEX, rule_ids
from crp_analysis.frameworks import registry, salesforce, sap_commerce, xmlsafe
from crp_analysis.frameworks.base import PackReport, VersionStatus
from crp_analysis.graph.extract import extract_facts
from crp_analysis.graph.resolve import EdgeSpec, GraphData, build_graph
from crp_analysis.policy import classify
from crp_analysis.structure import grammar_for
from crp_devtools.testing.fixture_projects import prepare_fixture

SAP = "core-customize/hybris/bin/custom/"
SF = "force-app/main/default/"


def _pack_graph(root: Path) -> tuple[GraphData, list[PackReport]]:
    """The graph job's steps (crp_worker.graph_job.run_graph) on a directory."""
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    languages = {f: classify(f).language for f in files}
    sources = {f: lang for f, lang in languages.items() if grammar_for(f, lang)}
    facts = {f: extract_facts((root / f).read_bytes(), f, lang) for f, lang in sources.items()}
    detection = registry.detect(set(files))
    texts = {f: (root / f).read_text() for f in files if registry.wants(f, detection)}
    prepared = registry.prepare(texts, detection)
    data = build_graph(
        sources=sources,
        facts=facts,
        known_files=dict.fromkeys(files, "ANALYZABLE"),
        modules=prepared.modules,
        tsconfigs=[],
    )
    return data, registry.map_packs(data, texts, prepared)


def _digest(root: Path) -> str:
    return hashlib.sha256(
        b"".join(p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file())
    ).hexdigest()


def _edge(data: GraphData, relation: str, target_ref: str, path_end: str = "") -> EdgeSpec:
    found = [
        e
        for e in data.edges
        if e.relation == relation
        and e.target_ref == target_ref
        and (e.path or "").endswith(path_end)
    ]
    assert len(found) == 1, (relation, target_ref, path_end, found)
    return found[0]


@pytest.fixture(scope="module")
def sap_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return prepare_fixture("sap-commerce-mixed", tmp_path_factory.mktemp("sap") / "src")


@pytest.fixture(scope="module")
def sf_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return prepare_fixture("salesforce-mixed", tmp_path_factory.mktemp("sf") / "src")


# -- secure XML -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "document",
    [
        b'<!DOCTYPE x [<!ENTITY e "boom">]><x>&e;</x>',
        b'<!DOCTYPE x SYSTEM "http://attacker.example/x.dtd"><x/>',
        b'<!DOCTYPE x [<!ENTITY % p SYSTEM "file:///etc/passwd"> %p;]><x/>',
        b'<!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><x>&b;</x>',
    ],
)
def test_xml_with_dtd_or_entities_is_refused(document: bytes) -> None:
    with pytest.raises(xmlsafe.UnsafeXmlError):
        xmlsafe.parse(document)


def test_xml_keeps_lines_and_strips_namespaces() -> None:
    root = xmlsafe.parse(
        b'<?xml version="1.0"?>\n<beans xmlns="urn:b" xmlns:p="urn:p">\n'
        b'  <bean id="a" p:svc-ref="b"/>\n</beans>'
    )
    bean = root.find("bean")
    assert bean is not None and bean.line == 3 and bean.attrs == {"id": "a", "svc-ref": "b"}
    with pytest.raises(ValueError, match="malformed"):
        xmlsafe.parse(b"<a><b></a>")


# -- SAP Commerce -----------------------------------------------------------------------------------


def test_sap_versions_are_detected_conservatively() -> None:
    assert sap_commerce.detect_version(
        {"core-customize/manifest.json": '{\n  "commerceSuiteVersion": "2211.28"\n}'}
    ) == sap_commerce.Version("2211.28", "core-customize/manifest.json:2", VersionStatus.SUPPORTED)
    old = sap_commerce.detect_version({"bin/platform/build.number": "builddate=x\nversion=1905.30"})
    assert (old.value, old.status) == ("1905.30", VersionStatus.UNSUPPORTED)
    assert sap_commerce.detect_version({"a/manifest.json": '{"name": "web app"}'}).status == (
        VersionStatus.UNKNOWN
    )


def test_sap_mapping_is_evidence_backed(sap_root: Path) -> None:
    before = _digest(sap_root)
    data, [report] = _pack_graph(sap_root)
    assert _digest(sap_root) == before  # the upload is read, never changed
    assert (report.id, report.version, report.version_status) == (
        "sap-commerce",
        "2211.28",
        VersionStatus.SUPPORTED,
    )
    expected = [
        ("depends_on", "shopcore", "resolved", SAP + "shopfacades/extensioninfo.xml"),
        ("depends_on", "commerceservices", "declared", SAP + "shopcore/extensioninfo.xml"),
        ("loads_extension", "shopocc", "resolved", "localextensions.xml"),
        ("loads_extension", "commerceservices", "declared", "localextensions.xml"),
        ("implemented_by", "com.shop.core.service.impl.DefaultLoyaltyService", "resolved", ""),
        ("injects", "shopProductDao", "resolved", "shopcore-spring.xml"),
        ("injects", "loyaltyFacade", "resolved", "LoyaltyController.java"),
        ("injects", "flexibleSearchService", "declared", "shopcore-spring.xml"),
        ("intercepts", "LoyaltyAccount", "resolved", "shopcore-spring.xml"),
        ("intercepts", "Order", "declared", "shopcore-spring.xml"),
        ("extends_type", "LoyaltyAccount", "resolved", "shopcore-items.xml"),
        ("extends_type", "CronJob", "declared", "shopcore-items.xml"),
        ("relates_to", "LoyaltyAccount", "resolved", "shopcore-items.xml"),
        ("imports_data", "LoyaltyRecalculationCronJob", "resolved", ".impex"),
        ("imports_data", "Product", "declared", ".impex"),
        ("runs_bean", "loyaltyRecalculationJob", "resolved", ".impex"),
        ("extends_bean", "abstractPopulatingConverter", "declared", "shopfacades-spring.xml"),
    ]
    lines = {
        p.relative_to(sap_root).as_posix(): p.read_text().splitlines()
        for p in sap_root.rglob("*")
        if p.is_file()
    }
    for relation, ref, classification, path_end in expected:
        edge = _edge(data, relation, ref, path_end)
        assert edge.classification == classification, (relation, ref, edge.reason)
        assert edge.path in lines and edge.start_line is not None
        if edge.text is not None:  # evidence text is the cited line itself
            assert edge.text == " ".join(lines[edge.path][edge.start_line - 1].split())[:300]
    extensions = next(c for c in report.capabilities if c.id == "extensions")
    assert extensions.state == "partial"  # shopbroken's extensioninfo.xml is malformed
    assert any("shopbroken" in note for note in report.notes)
    assert {c.id: c.state for c in report.capabilities}["build_validation"] == "unavailable"
    pack_nodes = [n for n in data.nodes.values() if n.kind == "component"]
    assert {n.attributes["component_type"] for n in pack_nodes} >= {"spring_bean", "itemtype"}


def test_sap_extension_cycle_and_malformed_config(sap_root: Path) -> None:
    files = sorted(
        p.relative_to(sap_root).as_posix()
        for p in sap_root.rglob("*")
        if FrameworkRulesAdapter().is_eligible(p.name, None)
    )
    (sap_root.parent / "home").mkdir(exist_ok=True)
    outcome = FrameworkRulesAdapter().run(
        sap_root, files, cancel=CancelToken(), heartbeat=lambda _: None
    )
    cycles = sorted((f.path.split("/")[-2], f.start_line) for f in outcome.findings)
    assert [f.rule_id for f in outcome.findings] == [CYCLE, CYCLE]
    assert cycles == [("shopexport", 4), ("shopimport", 4)]  # each requires-extension line
    assert [p.path.split("/")[-2] for p in outcome.problems] == ["shopbroken"]
    assert outcome.state.value == "PARTIAL"


# -- Salesforce -------------------------------------------------------------------------------------


def test_salesforce_mapping_is_evidence_backed(sf_root: Path) -> None:
    before = _digest(sf_root)
    data, [report] = _pack_graph(sf_root)
    assert _digest(sf_root) == before
    assert (report.id, report.version, report.version_status) == (
        "salesforce",
        "62.0",
        VersionStatus.SUPPORTED,
    )
    expected = [
        ("depends_on", "LoyaltyBase", "declared", "sfdx-project.json"),
        ("triggers_on", "Account", "declared", "AccountTrigger.trigger"),
        ("calls_apex", "AccountService.getAccounts", "resolved", "accountList.js"),
        ("calls_apex", "TierService.upsertTier", "declared", "accountList.js"),
        ("references_schema", "Loyalty_Member__c.Points__c", "resolved", "accountList.js"),
        ("queries", "Loyalty_Member__c", "resolved", "LoyaltyQueueable.cls"),
        ("queries", "Opportunity", "declared", "LegacyIntegration.cls"),
        ("flow_triggers_on", "Loyalty_Member__c", "resolved", ".flow-meta.xml"),
        ("flow_calls_apex", "LoyaltyInvocable", "resolved", ".flow-meta.xml"),
        ("flow_uses_object", "Account", "declared", ".flow-meta.xml"),
        ("grants_object_access", "Loyalty_Member__c", "resolved", ".permissionset-meta.xml"),
        ("grants_class_access", "AccountService", "resolved", ".permissionset-meta.xml"),
        ("lookup_to", "Account", "declared", "Account__c.field-meta.xml"),
        ("record_of", "Shop_Setting__mdt", "resolved", "Shop_Setting.Default.md-meta.xml"),
    ]
    for relation, ref, classification, path_end in expected:
        edge = _edge(data, relation, ref, path_end)
        assert edge.classification == classification, (relation, ref, edge.reason)
    grants = _edge(data, "grants_object_access", "Loyalty_Member__c")
    assert grants.reason.startswith("Read, Edit")
    classes = {
        n.label: n.attributes
        for n in data.nodes.values()
        if n.attributes.get("component_type") == "apex_class"
    }
    assert classes["LegacyIntegration"]["sharing"] == "not declared"
    assert classes["AccountService"]["sharing"] == "with sharing"
    assert "callout" in classes["LegacyIntegration"]["entry_points"]
    assert classes["AccountServiceTest"]["is_test"] is True
    objects = next(c for c in report.capabilities if c.id == "objects")
    assert objects.state == "partial"  # Broken__c declares a DTD and is refused
    assert any("DTD" in note for note in report.notes)


def test_apex_comments_strings_and_missing_metadata() -> None:
    data = GraphData()
    texts = {
        "force-app/classes/Probe.cls": (
            "public class Probe {\n"
            "  // [SELECT Id FROM Commented__c]\n"
            "  String s = '[SELECT Id FROM Quoted__c]';\n"
            "  List<Invoice__c> rows = [SELECT Id FROM Invoice__c];\n"
            "}\n"
        )
    }
    report = salesforce.map_upload(data, texts, salesforce.Project())
    refs = [(e.target_ref, e.classification) for e in data.edges if e.relation == "queries"]
    assert refs == [("Invoice__c", "unresolved")]  # custom object without metadata
    assert report.version_status == VersionStatus.UNKNOWN


def test_retired_api_versions_are_reported(sf_root: Path, tmp_path: Path) -> None:
    files = sorted(
        p.relative_to(sf_root).as_posix()
        for p in sf_root.rglob("*")
        if FrameworkRulesAdapter().is_eligible(p.name, None)
    )
    outcome = FrameworkRulesAdapter().run(
        sf_root, files, cancel=CancelToken(), heartbeat=lambda _: None
    )
    retired = [(f.path.rsplit("/", 1)[-1], f.start_line) for f in outcome.findings]
    assert retired == [("LegacyIntegration.cls-meta.xml", 3)]
    assert [p.path.rsplit("/", 1)[-1] for p in outcome.problems] == ["Broken__c.object-meta.xml"]
    project = tmp_path / "old"
    project.mkdir()
    (project / "sfdx-project.json").write_text(json.dumps({"sourceApiVersion": "29.0"}))
    old = FrameworkRulesAdapter().run(
        project, ["sfdx-project.json"], cancel=CancelToken(), heartbeat=lambda _: None
    )
    assert [f.rule_id for f in old.findings] == [RETIRED]
    report = salesforce.map_upload(
        GraphData(),
        {},
        salesforce.parse_project({"sfdx-project.json": '{"sourceApiVersion": "29.0"}'}),
    )
    assert report.version_status == VersionStatus.UNSUPPORTED


def test_detection_and_non_framework_uploads() -> None:
    assert registry.detect({"src/App.java", "pom.xml"}) == registry.Detection(False, False)
    assert registry.detect({"x/extensioninfo.xml"}).sap
    assert registry.detect({"sfdx-project.json"}).salesforce
    assert not registry.wants("src/App.java", registry.Detection(False, False))
    assert registry.wants("src/App.java", registry.Detection(True, False))  # SAP annotations


# -- catalog ------------------------------------------------------------------------------------------


def test_every_pack_rule_is_catalogued_and_listed() -> None:
    catalog = all_rules()
    for rule in rule_ids(APEX):
        assert f"pmd-apex:{rule}" in catalog, rule
    for rule in CONFIG_RULES:
        assert f"frameworks:{rule}" in catalog, rule
    for rule in opengrep_rule_ids():
        assert f"opengrep:{rule}" in catalog, rule
    assert set(salesforce.RULES) == {*rule_ids(APEX), RETIRED}
    assert set(sap_commerce.RULES) <= {*opengrep_rule_ids(), CYCLE}
