"""Evidence signals for the NFR checkpoints (P12): what the upload declares or contains.

Two kinds:
- ``supports``: a mechanism a checkpoint relies on is declared or present (a library in a
  manifest, a pipeline, a probe in a manifest). It shows intent, not that it works at run time.
- ``context``: material that is in the upload but that refactorX does not assess (Helm templates,
  Terraform security settings, load-test scripts); listed so nothing looks checked that is not.

Libraries come from the manifests' declared dependencies (pom.xml, package.json) with their file
and line; files from the upload's paths; configuration signals (replicas, probes, autoscaling,
timeouts, ...) from the ``nfr`` engine's run of the same review (``crp_analysis.nfr.config``).
Nothing is executed or downloaded.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from crp_analysis import policy as scope_policy

SIGNALS_VERSION = "crp-nfr-signals-v3"
MAX_LOCATIONS = 5


@dataclass(frozen=True, slots=True)
class Signal:
    id: str
    label: str
    kind: str  # supports | context
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
        libraries=(("maven", "org.springframework.boot:spring-boot-starter-actuator"),),
    ),
    Signal(
        "metrics",
        "Metrics library",
        "supports",
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
        libraries=(("maven", "io.opentelemetry*:*"), ("npm", "@opentelemetry/*")),
    ),
    Signal(
        "circuit-breakers",
        "Circuit breakers and fault handling",
        "supports",
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
        libraries=(("maven", "io.sentry:*"), ("npm", "@sentry/*"), ("npm", "@bugsnag/*")),
    ),
    Signal(
        "structured-logging",
        "Structured logging",
        "supports",
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
        libraries=(("npm", "helmet"),),
    ),
    Signal(
        "db-migrations",
        "Versioned database migrations",
        "supports",
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
        libraries=(("maven", "net.javacrumbs.shedlock:*"),),
    ),
    Signal(
        "rate-limiting",
        "Rate limiting",
        "supports",
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
        libraries=(("npm", "web-vitals"), ("npm", "@lhci/cli"), ("npm", "lighthouse")),
    ),
    Signal(
        "ci-pipeline",
        "Automated build and deployment pipeline",
        "supports",
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
        files=("**/Dockerfile", "**/Dockerfile.*", "**/*.Dockerfile", "**/Containerfile"),
    ),
    Signal(
        "runtime-pins",
        "Pinned runtime versions",
        "supports",
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
        files=(
            *_yaml("**/alertmanager*"),
            *_yaml("**/*alert*rules*"),
            *_yaml("**/prometheus*"),
            "**/grafana/**/*.json",
            "**/dashboards/**/*.json",
        ),
    ),
    Signal(
        "load-tests",
        "Load-test scripts (their results are not read)",
        "context",
        files=("**/*.jmx", "**/gatling/**", "**/k6/**", "**/load-test*/**", "**/loadtest*/**"),
    ),
)
# Found by the configuration checks (engine ``nfr``), with file and line.
CONFIG_SIGNALS: tuple[Signal, ...] = (
    Signal(
        "multiple-instances",
        "More than one instance (Kubernetes replicas or autoscaler minimum)",
        "supports",
    ),
    Signal(
        "autoscaling",
        "Autoscaling (Kubernetes HorizontalPodAutoscaler or KEDA)",
        "supports",
    ),
    Signal(
        "disruption-budget",
        "Pod disruption budgets (maintenance keeps instances running)",
        "supports",
    ),
    Signal(
        "k8s-probes",
        "Kubernetes health probes (readiness, liveness, startup)",
        "supports",
    ),
    Signal(
        "graceful-shutdown",
        "Graceful shutdown (requests in progress finish)",
        "supports",
    ),
    Signal(
        "timeouts",
        "Timeouts for connections and calls",
        "supports",
    ),
    Signal(
        "connection-pool",
        "Database connection pool sized in configuration",
        "supports",
    ),
    Signal(
        "backups",
        "Database backups configured (Terraform)",
        "supports",
    ),
    Signal(
        "multi-zone",
        "Database across availability zones (Terraform)",
        "supports",
    ),
)
# What refactorX does not check in these files.
CHECKED_CONTEXT: tuple[Signal, ...] = (
    Signal(
        "helm-charts",
        "Helm charts (replicas and probes inside templates are not checked)",
        "context",
        files=("**/Chart.yaml",),
    ),
    Signal(
        "terraform",
        "Terraform infrastructure (security settings are not checked)",
        "context",
        files=("**/*.tf",),
    ),
)
TESTS_SIGNAL = Signal(
    "automated-tests",
    "Automated tests",
    "supports",
)


ConfigSignals = list[dict[str, object]]
"""The ``nfr`` engine's ``diagnostics["signals"]``: ``{signal, count, locations}`` entries."""
_BY_ID = {s.id: s for s in (*SIGNALS, *CONFIG_SIGNALS)}


def detect(
    paths: Iterable[str],
    libraries: Iterable[LibraryUse],
    config: ConfigSignals | None = None,
) -> list[Evidence]:
    """Evidence found in an upload: its file paths, the manifests' declared libraries and, when
    the configuration checks ran (``config`` is not None), what they found."""
    found: dict[str, Evidence] = {}
    signals = (*SIGNALS, *CHECKED_CONTEXT)

    def hit(signal: Signal, path: str, line: int | None, detail: str | None) -> None:
        evidence = found.get(signal.id)
        if evidence is None:
            evidence = found[signal.id] = Evidence(signal.id, signal.label, signal.kind)
        evidence.add(path, line, detail)

    for path in sorted(paths):
        pure = PurePosixPath(path)
        for signal in signals:
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
    for entry in config or []:
        configured = _BY_ID.get(str(entry.get("signal")))
        locations = entry.get("locations")
        if configured is None or not isinstance(locations, list):
            continue
        for location in locations:
            if isinstance(location, list) and len(location) == 3:
                where, line, detail = location
                number = line if isinstance(line, int) else None
                hit(configured, str(where), number, str(detail) if detail else None)
        evidence = found.get(configured.id)
        count = entry.get("count")
        if evidence is not None and isinstance(count, int) and count > len(locations):
            evidence.count += count - len(locations)  # locations are capped; the count is not
    everything = (*SIGNALS, *CONFIG_SIGNALS, *CHECKED_CONTEXT, TESTS_SIGNAL)
    order = {s.id: i for i, s in enumerate(everything)}
    return sorted(found.values(), key=lambda e: order[e.signal])
