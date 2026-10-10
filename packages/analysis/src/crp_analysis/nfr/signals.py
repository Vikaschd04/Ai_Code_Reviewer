"""Evidence signals for the NFR questionnaire (P12 slice 1): what the upload declares or contains.

Two kinds:
- ``supports``: a mechanism the requirement relies on is declared or present (a library in a
  manifest, a pipeline, an API description). It shows intent, not that it works at run time.
- ``context``: material that is in the upload but not assessed yet (deployment manifests,
  Terraform, application configuration, load-test scripts); later slices check it.

Libraries come from the manifests' declared dependencies (pom.xml, package.json) with their file
and line; files from the upload's paths. Nothing is executed or downloaded.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from crp_analysis import policy as scope_policy

SIGNALS_VERSION = "crp-nfr-signals-v1"
MAX_LOCATIONS = 5


@dataclass(frozen=True, slots=True)
class Signal:
    id: str
    label: str
    kind: str  # supports | context
    questions: tuple[str, ...]
    libraries: tuple[tuple[str, str], ...] = ()  # (ecosystem, name pattern)
    files: tuple[str, ...] = ()  # path patterns (``**`` = any folders)


@dataclass(frozen=True, slots=True)
class LibraryUse:
    ecosystem: str  # maven | npm
    name: str  # groupId:artifactId or npm package
    path: str  # the manifest
    line: int | None


@dataclass(slots=True)
class Evidence:
    signal: str
    label: str
    kind: str
    questions: tuple[str, ...]
    count: int = 0
    locations: list[tuple[str, int | None, str | None]] = field(default_factory=list)
    """(path, line, detail such as the library name), up to ``MAX_LOCATIONS``."""

    def add(self, path: str, line: int | None, detail: str | None) -> None:
        self.count += 1
        if len(self.locations) < MAX_LOCATIONS:
            self.locations.append((path, line, detail))


def _yaml(stem: str) -> tuple[str, ...]:
    return tuple(f"{stem}.{ext}" for ext in ("yml", "yaml"))


SIGNALS: tuple[Signal, ...] = (
    Signal(
        "health-endpoints",
        "Health and readiness endpoints (Spring Boot Actuator)",
        "supports",
        ("operations.monitoring", "recoverability.recovery-time", "availability.continuous"),
        libraries=(("maven", "org.springframework.boot:spring-boot-starter-actuator"),),
    ),
    Signal(
        "metrics",
        "Metrics library",
        "supports",
        ("operations.monitoring",),
        libraries=(
            ("maven", "io.micrometer:*"),
            ("maven", "io.prometheus:*"),
            ("npm", "prom-client"),
        ),
    ),
    Signal(
        "tracing",
        "Distributed tracing (OpenTelemetry)",
        "supports",
        ("operations.monitoring", "reliability.glitches"),
        libraries=(("maven", "io.opentelemetry*:*"), ("npm", "@opentelemetry/*")),
    ),
    Signal(
        "circuit-breakers",
        "Circuit breakers and fault handling",
        "supports",
        ("reliability.consistency", "availability.fault-tolerance", "recoverability.outage"),
        libraries=(
            ("maven", "io.github.resilience4j:*"),
            ("maven", "org.springframework.cloud:spring-cloud-starter-circuitbreaker-*"),
            ("maven", "com.netflix.hystrix:*"),
            ("npm", "opossum"),
            ("npm", "cockatiel"),
        ),
    ),
    Signal(
        "retries",
        "Retries for failed calls",
        "supports",
        ("reliability.consistency", "recoverability.outage"),
        libraries=(
            ("maven", "org.springframework.retry:spring-retry"),
            ("npm", "p-retry"),
            ("npm", "async-retry"),
            ("npm", "axios-retry"),
        ),
    ),
    Signal(
        "error-tracking",
        "Error tracking",
        "supports",
        ("reliability.glitches",),
        libraries=(("maven", "io.sentry:*"), ("npm", "@sentry/*"), ("npm", "@bugsnag/*")),
    ),
    Signal(
        "structured-logging",
        "Structured logging",
        "supports",
        ("reliability.glitches", "operations.monitoring"),
        libraries=(
            ("maven", "net.logstash.logback:logstash-logback-encoder"),
            ("npm", "pino"),
            ("npm", "winston"),
        ),
    ),
    Signal(
        "authentication",
        "Authentication and authorization framework",
        "supports",
        ("security.access",),
        libraries=(
            ("maven", "org.springframework.boot:spring-boot-starter-security"),
            ("maven", "org.springframework.boot:spring-boot-starter-oauth2-*"),
            ("maven", "org.springframework.security:*"),
            ("maven", "org.keycloak:*"),
            ("npm", "passport"),
            ("npm", "next-auth"),
            ("npm", "@auth/*"),
            ("npm", "express-openid-connect"),
            ("npm", "@azure/msal-*"),
        ),
    ),
    Signal(
        "security-headers",
        "HTTP security headers (helmet)",
        "supports",
        ("security.attacks",),
        libraries=(("npm", "helmet"),),
    ),
    Signal(
        "db-migrations",
        "Versioned database migrations",
        "supports",
        ("recoverability.data",),
        libraries=(
            ("maven", "org.flywaydb:*"),
            ("maven", "org.liquibase:*"),
            ("npm", "node-pg-migrate"),
            ("npm", "db-migrate"),
            ("npm", "umzug"),
        ),
    ),
    Signal(
        "caching",
        "Caching",
        "supports",
        ("performance.latency", "scalability.demand"),
        libraries=(
            ("maven", "org.springframework.boot:spring-boot-starter-cache"),
            ("maven", "org.springframework.boot:spring-boot-starter-data-redis"),
            ("maven", "com.github.ben-manes.caffeine:*"),
            ("maven", "org.ehcache:*"),
            ("maven", "redis.clients:jedis"),
            ("maven", "io.lettuce:*"),
            ("npm", "ioredis"),
            ("npm", "redis"),
            ("npm", "lru-cache"),
            ("npm", "node-cache"),
        ),
    ),
    Signal(
        "messaging",
        "Asynchronous messaging and queues",
        "supports",
        (
            "scalability.spikes",
            "scalability.demand",
            "availability.fault-tolerance",
            "portability.data-exchange",
        ),
        libraries=(
            ("maven", "org.springframework.kafka:*"),
            ("maven", "org.apache.kafka:*"),
            ("maven", "org.springframework.boot:spring-boot-starter-amqp"),
            ("maven", "com.rabbitmq:*"),
            ("maven", "software.amazon.awssdk:sqs"),
            ("npm", "kafkajs"),
            ("npm", "amqplib"),
            ("npm", "bullmq"),
            ("npm", "@aws-sdk/client-sqs"),
        ),
    ),
    Signal(
        "api-docs-library",
        "API documentation generated from the code (OpenAPI)",
        "supports",
        ("portability.data-exchange",),
        libraries=(
            ("maven", "org.springdoc:*"),
            ("maven", "io.swagger*:*"),
            ("npm", "@nestjs/swagger"),
            ("npm", "swagger-ui-express"),
            ("npm", "swagger-jsdoc"),
        ),
    ),
    Signal(
        "job-locking",
        "Scheduled jobs locked across instances (ShedLock)",
        "supports",
        ("availability.continuous",),
        libraries=(("maven", "net.javacrumbs.shedlock:*"),),
    ),
    Signal(
        "rate-limiting",
        "Rate limiting",
        "supports",
        ("scalability.spikes", "security.attacks"),
        libraries=(
            ("maven", "com.bucket4j:*"),
            ("npm", "express-rate-limit"),
            ("npm", "rate-limiter-flexible"),
        ),
    ),
    Signal(
        "internationalisation",
        "Internationalisation",
        "supports",
        ("usability.simplicity",),
        libraries=(
            ("npm", "i18next"),
            ("npm", "react-i18next"),
            ("npm", "react-intl"),
            ("npm", "vue-i18n"),
            ("npm", "@angular/localize"),
        ),
    ),
    Signal(
        "accessibility-checks",
        "Accessibility checks in the build or tests",
        "supports",
        ("usability.experience",),
        libraries=(
            ("npm", "eslint-plugin-jsx-a11y"),
            ("npm", "eslint-plugin-vuejs-accessibility"),
            ("npm", "axe-core"),
            ("npm", "@axe-core/*"),
            ("npm", "jest-axe"),
            ("npm", "cypress-axe"),
            ("npm", "pa11y"),
        ),
    ),
    Signal(
        "page-performance",
        "Page performance measurement",
        "supports",
        ("performance.load-time",),
        libraries=(("npm", "web-vitals"), ("npm", "@lhci/cli"), ("npm", "lighthouse")),
    ),
    Signal(
        "ci-pipeline",
        "Automated build and deployment pipeline",
        "supports",
        ("recoverability.recovery-time", "reliability.consistency"),
        files=(
            *(f"**/.github/workflows/*.{e}" for e in ("yml", "yaml")),
            "**/Jenkinsfile",
            "**/.gitlab-ci.yml",
            "**/azure-pipelines*.yml",
            "**/bitbucket-pipelines.yml",
            "**/.circleci/config.yml",
        ),
    ),
    Signal(
        "container",
        "Container image definition",
        "supports",
        ("portability.platforms",),
        files=("**/Dockerfile", "**/Dockerfile.*", "**/*.Dockerfile", "**/Containerfile"),
    ),
    Signal(
        "runtime-pins",
        "Pinned runtime versions",
        "supports",
        ("portability.platforms",),
        files=(
            "**/.nvmrc",
            "**/.node-version",
            "**/.java-version",
            "**/.tool-versions",
            "**/.sdkmanrc",
        ),
    ),
    Signal(
        "api-specs",
        "API description files (OpenAPI, AsyncAPI)",
        "supports",
        ("portability.data-exchange",),
        files=tuple(
            f"**/{stem}*.{ext}"
            for stem in ("openapi", "swagger", "asyncapi")
            for ext in ("yaml", "yml", "json")
        ),
    ),
    Signal(
        "runbooks",
        "Runbooks and operations guides",
        "supports",
        ("operations.remediation",),
        files=(
            "**/RUNBOOK*.md",
            "**/runbook*.md",
            "**/runbooks/**",
            "**/OPERATIONS.md",
            "**/docs/operations*.md",
        ),
    ),
    Signal(
        "alerting",
        "Alert rules and dashboards",
        "supports",
        ("operations.monitoring",),
        files=(
            *_yaml("**/alertmanager*"),
            *_yaml("**/*alert*rules*"),
            *_yaml("**/prometheus*"),
            "**/grafana/**/*.json",
            "**/dashboards/**/*.json",
        ),
    ),
    Signal(
        "deployment-manifests",
        "Kubernetes or Helm manifests (replicas, probes and autoscaling not checked yet)",
        "context",
        ("availability.continuous", "scalability.demand", "scalability.spikes"),
        files=(
            "**/Chart.yaml",
            *_yaml("**/kustomization"),
            *(
                f"**/{d}/**/*.{e}"
                for d in ("k8s", "kubernetes", "helm", "charts")
                for e in ("yml", "yaml")
            ),
        ),
    ),
    Signal(
        "terraform",
        "Terraform infrastructure (backups and redundancy not checked yet)",
        "context",
        ("recoverability.data", "availability.continuous"),
        files=("**/*.tf",),
    ),
    Signal(
        "application-config",
        "Application configuration (timeouts and pool sizes not checked yet)",
        "context",
        ("performance.access-pattern", "performance.latency"),
        files=(
            "**/application*.properties",
            *_yaml("**/application*"),
        ),
    ),
    Signal(
        "load-tests",
        "Load-test scripts (results can be imported later)",
        "context",
        ("scalability.spikes", "performance.access-pattern"),
        files=("**/*.jmx", "**/gatling/**", "**/k6/**", "**/load-test*/**", "**/loadtest*/**"),
    ),
)
TESTS_SIGNAL = Signal(
    "automated-tests", "Automated tests", "supports", ("reliability.consistency",)
)


def detect(paths: Iterable[str], libraries: Iterable[LibraryUse]) -> list[Evidence]:
    """Evidence found in an upload: its file paths and the manifests' declared libraries."""
    found: dict[str, Evidence] = {}

    def hit(signal: Signal, path: str, line: int | None, detail: str | None) -> None:
        evidence = found.get(signal.id)
        if evidence is None:
            evidence = found[signal.id] = Evidence(
                signal.id, signal.label, signal.kind, signal.questions
            )
        evidence.add(path, line, detail)

    for path in sorted(paths):
        pure = PurePosixPath(path)
        for signal in SIGNALS:
            if signal.files and any(pure.full_match(p) for p in signal.files):
                hit(signal, path, None, None)
        if scope_policy.classify(path).category == "test":
            hit(TESTS_SIGNAL, path, None, None)
    for use in sorted(libraries, key=lambda u: (u.path, u.line or 0, u.name)):
        for signal in SIGNALS:
            if any(
                eco == use.ecosystem and fnmatch.fnmatchcase(use.name, pattern)
                for eco, pattern in signal.libraries
            ):
                hit(signal, use.path, use.line, use.name)
    order = {s.id: i for i, s in enumerate((*SIGNALS, TESTS_SIGNAL))}
    return sorted(found.values(), key=lambda e: order[e.signal])
