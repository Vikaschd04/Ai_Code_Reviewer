"""Deterministic recipes for the configuration checkpoints (engine ``nfr``, ADR 0024).

Each recipe edits only the reported line (or inserts lines right after it) in the narrow shapes it
understands; anything else is ``NoFix`` with the reason. Every proposal states the behaviour it
changes and still goes through the validation ladder, where the configuration checks run again on
the changed copy.
"""

from __future__ import annotations

import re

import yaml

from crp_analysis.fixes.patching import Edit
from crp_analysis.fixes.recipes import FindingInfo, NoFix, RecipeProposal
from crp_analysis.nfr import config

RECIPES = {
    config.SCHEMA_AUTO: "nfr:schema-validate",
    config.HEALTH_DETAILS: "nfr:health-details-authorized",
    config.ACTUATOR_EXPOSED: "nfr:actuator-safe-exposure",
    config.RECREATE: "nfr:rolling-update",
    config.SINGLE_REPLICA: "nfr:two-replicas",
    config.NO_READINESS: "nfr:readiness-probe",
}
_SETTING = (
    r"(?P<head>^.*?{key}\s*[:=]\s*)(?P<q>[\"']?)(?P<value>{value})(?P=q)(?P<tail>\s*(?:#.*)?)$"
)
_DDL = re.compile(
    _SETTING.format(key=r"(?:ddl[-_]?auto|hbm2ddl\.auto)", value=r"create-drop|create|update"),
    re.IGNORECASE,
)
_DETAILS = re.compile(_SETTING.format(key=r"show[-_]?details", value=r"always"), re.IGNORECASE)
_INCLUDE = re.compile(_SETTING.format(key=r"include", value=r"[^\"'#\[\]{}]+?"))
_STRATEGY = re.compile(_SETTING.format(key=r"type", value=r"Recreate"))
_ONE = re.compile(_SETTING.format(key=r"(?:replicas|minReplicas|count)", value=r"1"))


def _line(text: str, number: int | None) -> str:

    lines = text.splitlines()
    if number is None or number < 1 or number > len(lines):
        raise NoFix("the finding's line is not in the file")
    return lines[number - 1]


def _replace(
    finding: FindingInfo, text: str, pattern: re.Pattern[str], value: str, what: str
) -> tuple[Edit, str]:

    line = _line(text, finding.start_line)
    match = pattern.match(line)
    if match is None:
        raise NoFix(
            f"the {what} is not written on the reported line in a form changed automatically"
        )
    fixed = f"{match['head']}{match['q']}{value}{match['q']}{match['tail']}"
    number = finding.start_line or 1
    return Edit(finding.path, number, number, (line,), (fixed,)), match["value"]


def _documents(text: str) -> list[yaml.Node]:

    try:
        return config.parse_nodes(text)
    except config.ConfigError as exc:
        raise NoFix(f"the file could not be read ({exc})") from None


def _insert_after(finding: FindingInfo, text: str, number: int, lines: list[str]) -> Edit:
    original = _line(text, number)
    return Edit(finding.path, number, number, (original,), (original, *lines))


def schema_validate(finding: FindingInfo, text: str) -> RecipeProposal:

    edit, old = _replace(finding, text, _DDL, "validate", "ddl-auto setting")
    return RecipeProposal(
        "nfr:schema-validate",
        "Validate the schema instead of changing it",
        f"Hibernate stops running '{old}' at startup and only checks that the schema matches "
        "the entities.",
        "The application no longer creates or changes tables: it fails to start when the schema "
        "does not match. Apply schema changes with versioned migrations (Flyway or Liquibase).",
        (edit,),
    )


def health_details(finding: FindingInfo, text: str) -> RecipeProposal:

    edit, _ = _replace(finding, text, _DETAILS, "when-authorized", "show-details setting")
    return RecipeProposal(
        "nfr:health-details-authorized",
        "Show health details only to authorised users",
        "Anonymous callers see only the overall health status; authorised users still see the "
        "components.",
        "Monitoring that reads component details anonymously needs credentials or a role "
        "(management.endpoint.health.roles).",
        (edit,),
    )


def actuator_exposure(finding: FindingInfo, text: str) -> RecipeProposal:

    line = _line(text, finding.start_line)
    match = _INCLUDE.match(line)
    if match is None:
        raise NoFix("the exposure list is not a single value on the reported line")
    names = [n.strip() for n in match["value"].split(",") if n.strip()]
    kept = (
        ["health", "info"]
        if "*" in names
        else [n for n in names if n.lower() not in config.SENSITIVE_ENDPOINTS]
    )
    if not kept or kept == names:
        raise NoFix("nothing safe to keep in the exposure list")
    value = ",".join(kept)
    fixed = f"{match['head']}{match['q']}{value}{match['q']}{match['tail']}"
    number = finding.start_line or 1
    return RecipeProposal(
        "nfr:actuator-safe-exposure",
        f"Expose only {value} over HTTP",
        "Heap dumps, environment, configuration and thread dumps are no longer reachable over "
        "HTTP.",
        "Every endpoint left out is no longer reachable over HTTP"
        + (", including metrics endpoints such as prometheus" if "*" in names else "")
        + "; add the ones you use back explicitly and protect them.",
        (Edit(finding.path, number, number, (line,), (fixed,)),),
    )


def rolling_update(finding: FindingInfo, text: str) -> RecipeProposal:

    edit, _ = _replace(finding, text, _STRATEGY, "RollingUpdate", "strategy type")
    return RecipeProposal(
        "nfr:rolling-update",
        "Use rolling updates",
        "New instances start before old ones stop, so releases do not take the service down.",
        "For a short time the old and the new version run side by side; a volume that only one "
        "instance can mount (ReadWriteOnce) can block the rollout.",
        (edit,),
    )


def two_replicas(finding: FindingInfo, text: str) -> RecipeProposal:

    line = _line(text, finding.start_line)
    note = (
        "Two instances run (and cost) instead of one; the application must work with two "
        "instances at once (no in-memory sessions or unlocked scheduled jobs)."
    )
    title = "Run two instances"
    explanation = "A second instance keeps the service up during crashes, node failures and "
    explanation += "rollouts."
    if _ONE.match(line):
        edit, _ = _replace(finding, text, _ONE, "2", "replica count")
        return RecipeProposal("nfr:two-replicas", title, explanation, note, (edit,))
    # replicas not set: add it as the first entry of the workload's spec.
    for node in _documents(text):
        if config.key_line(node, "kind") != finding.start_line:
            continue
        spec = config.child(node, "spec")
        if not isinstance(spec, yaml.MappingNode) or spec.flow_style or not spec.value:
            break
        first_key = spec.value[0][0]
        indent = " " * first_key.start_mark.column
        number = first_key.start_mark.line + 1
        original = _line(text, number)
        return RecipeProposal(
            "nfr:two-replicas",
            title,
            explanation,
            note,
            (Edit(finding.path, number, number, (original,), (f"{indent}replicas: 2", original)),),
        )
    raise NoFix("the workload's spec could not be located to add replicas")


def readiness_probe(finding: FindingInfo, text: str) -> RecipeProposal:

    for node in _documents(text):
        for container in config.containers_of(node):
            name_line = config.key_line(container, "name")
            if name_line is None or name_line != finding.start_line:
                continue
            if container.flow_style:
                raise NoFix("containers written on one line are not changed automatically")
            port = config.first_port(container)
            if port is None:
                raise NoFix("the container's port could not be read")
            key = next(k for k, _ in container.value if getattr(k, "value", None) == "name")
            indent = " " * key.start_mark.column
            probe = [
                f"{indent}readinessProbe:",
                f"{indent}  tcpSocket:",
                f"{indent}    port: {port}",
            ]
            return RecipeProposal(
                "nfr:readiness-probe",
                f"Add a readiness probe on port {port}",
                "Kubernetes sends traffic to the container only after its port accepts "
                "connections.",
                "A TCP check only proves the port is open. Replace it with an HTTP readiness "
                "endpoint when the application has one (Spring Boot: /actuator/health/readiness).",
                (_insert_after(finding, text, name_line, probe),),
            )
    raise NoFix("the container could not be located in the file")


def propose(recipe_id: str, finding: FindingInfo, text: str) -> RecipeProposal:
    handlers = {
        "nfr:schema-validate": schema_validate,
        "nfr:health-details-authorized": health_details,
        "nfr:actuator-safe-exposure": actuator_exposure,
        "nfr:rolling-update": rolling_update,
        "nfr:two-replicas": two_replicas,
        "nfr:readiness-probe": readiness_probe,
    }
    return handlers[recipe_id](finding, text)
