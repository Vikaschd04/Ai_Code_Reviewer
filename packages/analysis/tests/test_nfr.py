"""NFR evidence signals (P12): libraries declared in manifests with their file and line, files in
the upload, and what refactorX does not check (context)."""

from __future__ import annotations

from crp_analysis.nfr.signals import LibraryUse, detect


def test_signals_cite_manifest_lines_and_files() -> None:
    paths = [
        "Dockerfile",
        ".github/workflows/ci.yml",
        "api/openapi.yaml",
        "docs/runbooks/restart.md",
        "deploy/k8s/deployment.yaml",
        "infra/main.tf",
        "deploy/chart/Chart.yaml",
        "src/main/resources/application.yml",
        "src/test/java/com/acme/OrderTest.java",
        "web/src/cart.test.ts",
        "README.md",
    ]
    libraries = [
        LibraryUse("maven", "org.springframework.boot:spring-boot-starter-actuator", "pom.xml", 21),
        LibraryUse("maven", "io.github.resilience4j:resilience4j-spring-boot3", "pom.xml", 30),
        LibraryUse("maven", "org.flywaydb:flyway-core", "pom.xml", 34),
        LibraryUse("npm", "@opentelemetry/sdk-node", "web/package.json", 12),
        LibraryUse("npm", "redis-mock", "web/package.json", 13),  # not a cache in production
        LibraryUse("maven", "io.micrometerx:other", "pom.xml", 40),  # look-alike group
    ]
    found = {e.signal: e for e in detect(paths, libraries)}
    assert set(found) == {
        "health-endpoints",
        "tracing",
        "circuit-breakers",
        "db-migrations",
        "ci-pipeline",
        "container",
        "api-specs",
        "runbooks",
        "terraform",
        "helm-charts",
        "automated-tests",
    }
    assert found["health-endpoints"].locations == [
        ("pom.xml", 21, "org.springframework.boot:spring-boot-starter-actuator")
    ]
    assert found["automated-tests"].count == 2
    assert found["terraform"].kind == "context" and found["helm-charts"].kind == "context"
    assert (
        found["terraform"].label == "Terraform infrastructure (security settings are not checked)"
    )
    assert found["container"].kind == "supports"
