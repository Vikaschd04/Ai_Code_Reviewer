"""Framework configuration checks (SAP Commerce and Salesforce packs) run in-process.

Rules that need the whole configuration rather than one source file: extension dependency cycles
across ``extensioninfo.xml`` files and Salesforce metadata or project files declaring a retired
API version. Files are read as data with the secure XML reader; malformed or DTD-bearing files are
reported as FAILED coverage, never as clean. Not cacheable per file (cycles span files).
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from crp_analysis.engines.base import (
    Availability,
    CacheIdentity,
    CancelToken,
    EngineOutcome,
    FileProblem,
    Heartbeat,
    RawFinding,
    completed_state,
)
from crp_analysis.frameworks import salesforce, sap_commerce, xmlsafe
from crp_analysis.frameworks.base import basename
from crp_core.domain.states import CoverageOutcome, EngineState

ENGINE_VERSION = "1.0.0"
RULESET_ID = "crp-framework-rules-v1"
CYCLE = "crp.sap.extension.dependency-cycle"
RETIRED = "crp.sf.metadata.retired-api-version"
RULES = (CYCLE, RETIRED)


def ruleset_sha256() -> str:
    identity = [ENGINE_VERSION, *RULES, sap_commerce.ADAPTER, salesforce.ADAPTER]
    return hashlib.sha256("\x1f".join(identity).encode()).hexdigest()


class FrameworkRulesAdapter:
    name = "frameworks"
    ruleset_id = RULESET_ID

    def __init__(self, max_file_bytes: int = 2 * 1024 * 1024) -> None:
        self._max = max_file_bytes

    def is_eligible(self, path: str, language: str | None) -> bool:
        name = basename(path)
        return name in {"extensioninfo.xml", "sfdx-project.json"} or name.endswith("-meta.xml")

    def availability(self) -> Availability:
        return Availability(True, ENGINE_VERSION)

    def enabled_rules(self) -> tuple[str, ...]:
        return RULES

    def cache_identity(self) -> CacheIdentity | None:
        return None

    def run(
        self, root: Path, files: list[str], *, cancel: CancelToken, heartbeat: Heartbeat
    ) -> EngineOutcome:
        started = time.monotonic()
        outcome = EngineOutcome(
            state=EngineState.FAILED,
            engine_version=ENGINE_VERSION,
            ruleset_id=RULESET_ID,
            ruleset_sha256=ruleset_sha256(),
        )
        texts: dict[str, str] = {}
        failed: dict[str, str] = {}
        for path in files:
            if cancel.cancelled:
                outcome.state = EngineState.CANCELED
                outcome.error_code = "canceled"
                return outcome
            data = (root / path).read_bytes()[: self._max]
            texts[path] = data.decode("utf-8", errors="replace")
        heartbeat("framework configuration checks running")
        extensions = sap_commerce.parse_extensions(
            {p: t for p, t in texts.items() if basename(p) == "extensioninfo.xml"}
        )
        for note in extensions.notes:
            path, _, reason = note.partition(": ")
            failed[path] = reason[:500]
        outcome.findings.extend(self._cycles(extensions))
        for path, text in sorted(texts.items()):
            if path.endswith("-meta.xml"):
                self._meta(path, text, outcome, failed)
            elif basename(path) == "sfdx-project.json":
                self._project(path, text, outcome, failed)
        outcome.attempted = sorted(files)
        outcome.problems = [
            FileProblem(p, CoverageOutcome.FAILED.value, r) for p, r in sorted(failed.items())
        ]
        outcome.state = completed_state(len(outcome.attempted), len(outcome.problems))
        outcome.exit_code = 0
        outcome.duration_ms = int((time.monotonic() - started) * 1000)
        outcome.diagnostics = {
            "extensions": len(extensions.modules),
            "metadata_files": sum(1 for p in texts if p.endswith("-meta.xml")),
        }
        return outcome

    @staticmethod
    def _cycles(extensions: sap_commerce.Extensions) -> list[RawFinding]:
        modules = {m.name: m for m in extensions.modules}
        findings: list[RawFinding] = []
        for cycle in sap_commerce.dependency_cycles(extensions.modules):
            chain = " → ".join([*cycle, cycle[0]])
            for index, name in enumerate(cycle):
                module = modules[name]
                required = cycle[(index + 1) % len(cycle)]
                line = next(
                    (d.line for d in module.dependencies if d.name == required and d.line),
                    extensions.lines.get(name),
                )
                findings.append(
                    RawFinding(
                        path=module.manifest,
                        rule_id=CYCLE,
                        ruleset=RULESET_ID,
                        engine_severity=None,
                        message=f"Extension {name} requires {required}, completing the cycle "
                        f"{chain}; the platform cannot order these extensions.",
                        start_line=line,
                        start_column=None,
                        end_line=line,
                        end_column=None,
                        rule_url=None,
                        identity=f"cycle:{','.join(sorted(cycle))}:{name}",
                    )
                )
        return findings

    @staticmethod
    def _retired(path: str, line: int, value: str, where: str) -> RawFinding:
        return RawFinding(
            path=path,
            rule_id=RETIRED,
            ruleset=RULESET_ID,
            engine_severity=None,
            message=f"{where} declares API version {value}, in the range Salesforce retired "
            f"(21.0-{salesforce.RETIRED_MAX:.1f}, Summer '25); calls at this version fail and "
            "the component keeps legacy behaviour.",
            start_line=line,
            start_column=None,
            end_line=line,
            end_column=None,
            rule_url=None,
        )

    def _meta(self, path: str, text: str, outcome: EngineOutcome, failed: dict[str, str]) -> None:
        try:
            root = xmlsafe.parse(text)
        except ValueError as exc:
            failed[path] = f"not parsed ({exc})"[:500]
            return
        version = root.find("apiVersion")
        if version is None:
            return
        value = version.text.strip()
        number = salesforce.api_version(value)
        if number is not None and number <= salesforce.RETIRED_MAX:
            outcome.findings.append(self._retired(path, version.line, value, "This metadata"))

    def _project(
        self, path: str, text: str, outcome: EngineOutcome, failed: dict[str, str]
    ) -> None:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            failed[path] = f"not parsed ({exc.msg})"
            return
        value = data.get("sourceApiVersion") if isinstance(data, dict) else None
        number = salesforce.api_version(value if isinstance(value, str) else None)
        if number is not None and number <= salesforce.RETIRED_MAX:
            line = next(
                (i for i, t in enumerate(text.splitlines(), 1) if "sourceApiVersion" in t), 1
            )
            outcome.findings.append(
                self._retired(path, line, str(value), "The project (sourceApiVersion)")
            )
