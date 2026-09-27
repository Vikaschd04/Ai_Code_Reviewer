"""Graph extraction and resolution on the synthetic graph-mixed fixture (no execution)."""

from __future__ import annotations

from pathlib import Path

import pytest

from crp_analysis.graph.extract import FileFacts, extract_facts
from crp_analysis.graph.manifests import (
    parse_package_json,
    parse_pom,
    parse_tsconfig,
)
from crp_analysis.graph.resolve import EdgeSpec, GraphData, build_graph
from crp_analysis.policy import classify
from crp_analysis.structure import grammar_for
from crp_devtools.testing.fixture_projects import prepare_fixture


def _graph(root: Path) -> tuple[GraphData, dict[str, FileFacts]]:
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    languages = {f: classify(f).language for f in files}
    sources = {f: lang for f, lang in languages.items() if grammar_for(f, lang)}
    facts = {f: extract_facts((root / f).read_bytes(), f, lang) for f, lang in sources.items()}
    modules, tsconfigs = [], []
    for f in files:
        data = (root / f).read_bytes()
        if f.endswith("pom.xml"):
            modules.append(parse_pom(f, data))
        elif f.endswith("package.json"):
            modules.append(parse_package_json(f, data))
        elif f.endswith("tsconfig.json"):
            config, _ = parse_tsconfig(f, data)
            assert config is not None
            tsconfigs.append(config)
    data = build_graph(
        sources=sources,
        facts=facts,
        known_files=dict.fromkeys(files, "ANALYZABLE"),
        modules=modules,
        tsconfigs=tsconfigs,
    )
    return data, facts


@pytest.fixture(scope="module")
def graph(tmp_path_factory: pytest.TempPathFactory) -> tuple[GraphData, dict[str, FileFacts]]:
    return _graph(prepare_fixture("graph-mixed", tmp_path_factory.mktemp("g") / "src"))


def _edge(data: GraphData, path: str, target_ref: str, relation: str | None = None) -> EdgeSpec:
    found = [
        e
        for e in data.edges
        if e.path == path and e.target_ref == target_ref and relation in {None, e.relation}
    ]
    assert len(found) == 1, (path, target_ref, found)
    return found[0]


JAVA_APP = "app/src/main/java/com/acme/app/"


@pytest.mark.parametrize(
    ("path", "ref", "relation", "classification", "target"),
    [
        (JAVA_APP + "Customer.java", "com.acme.core.Entity", "imports", "resolved",
         "type:core/src/main/java/com/acme/core/Entity.java#Entity"),
        (JAVA_APP + "Customer.java", "Entity", "extends", "resolved",
         "type:core/src/main/java/com/acme/core/Entity.java#Entity"),
        (JAVA_APP + "CustomerRepository.java", "com.acme.core", "imports", "resolved",
         "package:com.acme.core"),
        (JAVA_APP + "CustomerRepository.java", "Repository", "implements", "resolved",
         "type:core/src/main/java/com/acme/core/Repository.java#Repository"),
        (JAVA_APP + "CustomerRepository.java", "java.util.List", "imports", "declared",
         "external:jdk:java.util"),
        (JAVA_APP + "CustomerRepository.java", "org.slf4j.Logger", "imports", "inferred",
         "external:maven:org.slf4j:slf4j-api"),
        (JAVA_APP + "CustomerRepository.java", "com.unknown.Missing", "imports", "unresolved",
         "external:java:com.unknown"),
        (JAVA_APP + "Main.java", "com.acme.core.util.Strings.isBlank", "imports", "resolved",
         "type:core/src/main/java/com/acme/core/util/Strings.java#Strings"),
        (JAVA_APP + "Main.java", "Thread", "extends", "declared", "external:jdk:java.lang"),
        ("core/src/main/java/com/acme/core/Entity.java", "java.io.Serializable", "implements",
         "declared", "external:jdk:java.io"),
        # groupId com.google.guava does not prefix package com.google.common: never guessed.
        ("core/src/main/java/com/acme/core/util/Strings.java",
         "com.google.common.base.Preconditions", "imports", "unresolved",
         "external:java:com.google.common.base"),
        ("web/src/index.ts", "./app.js", "imports", "resolved", "file:web/src/app.ts"),
        ("web/src/index.ts", "@/styles/theme", "imports", "resolved", "file:web/src/styles/theme.ts"),
        ("web/src/index.ts", "./components", "reexports", "resolved",
         "file:web/src/components/index.ts"),
        ("web/src/app.ts", "react", "imports", "declared", "external:npm:react"),
        ("web/src/app.ts", "@acme/shared", "imports", "resolved", "module:npm:shared"),
        ("web/src/app.ts", "node:fs", "imports", "declared", "external:node:fs"),
        ("web/src/app.ts", "@/views/base", "imports", "resolved", "file:web/src/views/base.ts"),
        ("web/src/app.ts", "lodash", "imports", "unresolved", "external:npm:lodash"),
        ("web/src/app.ts", "./config.json", "requires", "resolved", "file:web/src/config.json"),
        ("web/src/app.ts", "BaseView", "extends", "resolved", "type:web/src/views/base.ts#BaseView"),
        ("web/src/views/home.tsx", "Renderable", "implements", "resolved",
         "type:web/src/views/base.ts#Renderable"),
        ("web/src/views/home.tsx", "./missing", "imports", "unresolved", None),
        ("web/src/components/index.ts", "./button", "reexports", "resolved",
         "file:web/src/components/button.ts"),
    ],
)  # fmt: skip
def test_reference_classification(
    graph: tuple[GraphData, dict[str, FileFacts]],
    path: str,
    ref: str,
    relation: str,
    classification: str,
    target: str | None,
) -> None:
    edge = _edge(graph[0], path, ref, relation)
    assert (edge.classification, edge.target) == (classification, target), edge.reason
    assert edge.reason and edge.start_line and edge.text, "every edge carries evidence"
    assert edge.text and (ref.split(".")[-1] in edge.text or ref in edge.text)


def test_dynamic_imports_are_unresolved_never_guessed(graph: tuple[GraphData, dict]) -> None:
    edge = _edge(graph[0], "web/src/app.ts", "<non-literal>", "dynamic_import")
    assert edge.classification == "unresolved" and edge.target is None
    assert "dynamic" in edge.reason


def test_modules_and_manifest_dependencies(graph: tuple[GraphData, dict]) -> None:
    data = graph[0]
    modules = {k: n for k, n in data.nodes.items() if n.kind == "module"}
    assert {n.label for n in modules.values()} == {
        "platform",
        "core",
        "app",
        "@acme/web",
        "@acme/shared",
    }
    deps = {(e.source, e.target_ref): e for e in data.edges if e.relation == "depends_on"}
    assert deps[("module:maven:app", "com.acme:core")].classification == "resolved"
    assert deps[("module:maven:app", "com.acme:core")].target == "module:maven:core"
    assert deps[("module:maven:core", "com.google.guava:guava")].classification == "declared"
    assert deps[("module:npm:web", "@acme/shared")].target == "module:npm:shared"
    assert deps[("module:maven:core", "com.google.guava:guava")].start_line == 13  # unique pom line
    assert (
        data.nodes["file:app/src/main/java/com/acme/app/Main.java"].module_key == "module:maven:app"
    )


def test_partial_parse_keeps_well_formed_relations(graph: tuple[GraphData, dict]) -> None:
    data, facts = graph
    assert facts["shared/src/broken.ts"].status == "PARTIAL"
    assert data.nodes["file:shared/src/broken.ts"].attributes["parse_status"] == "PARTIAL"
    edge = _edge(data, "shared/src/broken.ts", "./index", "imports")
    assert edge.classification == "resolved"


def test_every_anchor_is_a_snapshot_file(graph: tuple[GraphData, dict]) -> None:
    data = graph[0]
    for node in data.nodes.values():
        if node.path is not None:
            assert not node.path.startswith(("/", "..")), node
    for edge in data.edges:
        assert edge.path is not None and not edge.path.startswith(("/", ".."))
        assert edge.classification != "resolved" or edge.target in data.nodes


def test_extraction_is_deterministic_and_serializable(graph: tuple[GraphData, dict]) -> None:
    facts = graph[1]
    for item in facts.values():
        assert FileFacts.from_json(item.to_json()) == item


def test_untrusted_manifests_are_parsed_defensively() -> None:
    bomb = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><project>&lol;</project>'
    module = parse_pom("pom.xml", bomb)
    assert module.dependencies == [] and "DTD" in module.notes[0]
    assert "not well-formed" in parse_pom("x/pom.xml", b"<project>").notes[0]
    assert "not valid JSON" in parse_package_json("package.json", b"{nope").notes[0]
    config, note = parse_tsconfig(
        "tsconfig.json", b'{"extends": "./base.json", "compilerOptions": {"baseUrl": "src",}}'
    )
    assert config is not None and config.base_url == "src" and note and "extends" in note


def test_maven_and_npm_modules_in_one_directory_stay_distinct(tmp_path: Path) -> None:
    data, _ = _graph(prepare_fixture("seeded-mixed", tmp_path / "src"))
    modules = {k for k, n in data.nodes.items() if n.kind == "module"}
    assert {"module:maven:.", "module:npm:."} <= modules
    assert data.nodes["file:web/src/app.js"].module_key == "module:npm:."
    assert data.nodes["file:src/main/java/com/example/billing/CryptoUtil.java"].module_key == (
        "module:maven:."
    )
