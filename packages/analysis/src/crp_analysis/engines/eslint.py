"""ESLint adapter (JavaScript/TypeScript) using the isolated ``engines/eslint-runner`` package.

The runner loads only the platform's trusted flat config, lints exactly the listed files, ignores
inline configuration comments and never reads project ESLint configs. Exit contract: 0 = report
written; anything else = runner failure. Fatal messages mark a file FAILED (parse error).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from crp_analysis.engines.base import (
    Availability,
    CacheIdentity,
    CancelToken,
    EngineOutcome,
    FileProblem,
    Heartbeat,
    RawFinding,
    completed_state,
    fingerprint_config,
    redact_root,
)
from crp_analysis.engines.process import run_bounded, scrubbed_env
from crp_core.domain.states import CoverageOutcome, EngineState

RULESET_ID = "crp-eslint-v1"
_EXTENSIONS = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx")
_ENABLED_RULE = re.compile(r'"((?:@typescript-eslint/)?[a-z-]+)": (?:"error"|\["error")')


def _autofix(fix: object) -> dict[str, object] | None:
    """ESLint's own fix for the message (UTF-16 offsets plus the text they replace), if any."""
    if not isinstance(fix, dict):
        return None
    try:
        start, end = int(fix["start"]), int(fix["end"])
        text, original = str(fix["text"]), str(fix["original"])
    except KeyError, TypeError, ValueError:
        return None
    if start < 0 or end < start or len(text) > 10_000 or len(original) > 10_000:
        return None
    return {"autofix": {"start": start, "end": end, "text": text, "original": original}}


class EslintAdapter:
    name = "eslint"
    ruleset_id = RULESET_ID

    def __init__(
        self,
        runner_dir: Path | None,
        *,
        node_executable: Path | None,
        timeout_seconds: float,
        max_output_bytes: int,
        heap_mb: int = 1024,
    ) -> None:
        self._dir = runner_dir
        found = node_executable or (Path(p) if (p := shutil.which("node")) else None)
        self._node = found
        self._timeout = timeout_seconds
        self._max_output = max_output_bytes
        self._heap = heap_mb

    def is_eligible(self, path: str, language: str | None) -> bool:
        return language in {"javascript", "typescript"} and path.endswith(_EXTENSIONS)

    def _config(self) -> Path | None:
        return None if self._dir is None else self._dir / "trusted.config.mjs"

    def enabled_rules(self) -> tuple[str, ...] | None:
        config = self._config()
        if config is None or not config.is_file():
            return None
        return tuple(sorted(set(_ENABLED_RULE.findall(config.read_text(encoding="utf-8")))))

    def cache_identity(self) -> CacheIdentity | None:
        """Config hash plus runner script and installed plugin versions (rules are per file)."""
        ruleset = self.ruleset_sha256()
        if ruleset is None or self._dir is None or not (self._dir / "run.mjs").is_file():
            return None
        versions = []
        for package in ("eslint", "typescript-eslint", "globals", "typescript"):
            manifest = self._dir / "node_modules" / package / "package.json"
            data = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
            versions.append(f"{package}@{data.get('version', 'missing')}")
        runner = hashlib.sha256((self._dir / "run.mjs").read_bytes()).hexdigest()
        return CacheIdentity(ruleset, fingerprint_config("eslint-runner-v1", runner, *versions))

    def ruleset_sha256(self) -> str | None:
        config = self._config()
        return (
            hashlib.sha256(config.read_bytes()).hexdigest() if config and config.is_file() else None
        )

    def availability(self) -> Availability:
        if self._dir is None:
            return Availability(
                False, None, "ESLint runner is not configured (CRP_ESLINT_RUNNER_DIR)"
            )
        package = self._dir / "node_modules" / "eslint" / "package.json"
        if not (self._dir / "run.mjs").is_file() or not package.is_file():
            return Availability(
                False, None, "ESLint runner dependencies are missing; run pnpm install"
            )
        if self._node is None:
            return Availability(
                False, None, "Node.js (node) is required for ESLint and was not found"
            )
        version = json.loads(package.read_text(encoding="utf-8")).get("version")
        return Availability(True, str(version) if version else None)

    def run(
        self, root: Path, files: list[str], *, cancel: CancelToken, heartbeat: Heartbeat
    ) -> EngineOutcome:
        availability = self.availability()
        outcome = EngineOutcome(
            state=EngineState.FAILED,
            engine_version=availability.version,
            ruleset_id=RULESET_ID,
            ruleset_sha256=self.ruleset_sha256(),
        )
        if not availability.available or self._dir is None or self._node is None:
            outcome.state = EngineState.UNAVAILABLE
            outcome.error_code = "engine_unavailable"
            outcome.error_message = availability.reason
            return outcome
        work = root.parent
        file_list = work / "eslint-files.json"
        report = work / "eslint-report.json"
        file_list.write_text(json.dumps(files), encoding="utf-8")
        args = [
            str(self._node),
            f"--max-old-space-size={self._heap}",
            str(self._dir / "run.mjs"),
            str(root),
            str(file_list),
            str(report),
        ]
        result = run_bounded(
            args,
            cwd=work,
            env=scrubbed_env([self._node.parent], work / "home"),
            timeout_seconds=self._timeout,
            max_output_bytes=self._max_output,
            cancel=cancel,
            heartbeat=heartbeat,
            label="eslint",
        )
        outcome.exit_code = result.exit_code
        outcome.duration_ms = result.duration_ms
        if result.cancelled:
            outcome.state = EngineState.CANCELED
            outcome.error_code = "canceled"
            return self._not_attempted(outcome, files, "scan canceled")
        if result.timed_out:
            outcome.error_code = "engine_timeout"
            outcome.error_message = f"ESLint exceeded the {self._timeout:g}s time limit"
            return self._not_attempted(outcome, files, "engine timed out")
        if result.exit_code != 0 or not report.is_file():
            outcome.error_code = "engine_crashed"
            outcome.error_message = redact_root(
                f"ESLint runner exited with code {result.exit_code}: {result.stderr_tail[-800:]}",
                root,
            )
            return self._not_attempted(outcome, files, "engine did not complete")
        raw = report.read_bytes()
        outcome.raw_report = raw
        try:
            self._parse(json.loads(raw), outcome, set(files), root)
        except (ValueError, KeyError, TypeError) as exc:
            outcome.state = EngineState.FAILED
            outcome.findings = []
            outcome.error_code = "malformed_output"
            outcome.error_message = f"ESLint report could not be parsed: {type(exc).__name__}"
            return self._not_attempted(outcome, files, "engine output malformed")
        return outcome

    @staticmethod
    def _not_attempted(outcome: EngineOutcome, files: list[str], reason: str) -> EngineOutcome:
        outcome.attempted = []
        outcome.problems = [
            FileProblem(f, CoverageOutcome.NOT_ATTEMPTED.value, reason) for f in files
        ]
        return outcome

    @staticmethod
    def _parse(data: Any, outcome: EngineOutcome, files: set[str], root: Path) -> None:
        if data.get("eslintVersion"):
            outcome.engine_version = str(data["eslintVersion"])
        seen: set[str] = set()
        problems: list[FileProblem] = []
        for result in data["results"]:
            path = str(result["path"])
            if path not in files:
                continue
            seen.add(path)
            fatal = [m for m in result["messages"] if m.get("fatal")]
            if fatal:
                reason = redact_root(str(fatal[0].get("message", "parse error")), root)[:500]
                problems.append(FileProblem(path, CoverageOutcome.FAILED.value, reason))
                continue
            for message in result["messages"]:
                if message.get("ruleId") is None:
                    problems.append(
                        FileProblem(
                            path,
                            CoverageOutcome.NOT_ATTEMPTED.value,
                            str(message.get("message"))[:500],
                        )
                    )
                    break
                line = int(message["line"])
                outcome.findings.append(
                    RawFinding(
                        path=path,
                        rule_id=str(message["ruleId"]),
                        ruleset=RULESET_ID,
                        engine_severity=str(message.get("severity")),
                        message=str(message["message"]),
                        start_line=line,
                        start_column=int(message["column"]) if message.get("column") else None,
                        end_line=int(message.get("endLine") or line),
                        end_column=int(message["endColumn"]) if message.get("endColumn") else None,
                        rule_url=None,
                        details=_autofix(message.get("fix")),
                    )
                )
        for missing in sorted(files - seen):
            problems.append(
                FileProblem(missing, CoverageOutcome.NOT_ATTEMPTED.value, "no result from ESLint")
            )
        not_attempted = {
            p.path for p in problems if p.outcome == CoverageOutcome.NOT_ATTEMPTED.value
        }
        failed = sum(1 for p in problems if p.outcome == CoverageOutcome.FAILED.value)
        outcome.attempted = sorted(seen - not_attempted)
        outcome.problems = problems
        outcome.state = completed_state(len(outcome.attempted), failed)
        if not_attempted and outcome.state is EngineState.SUCCEEDED:
            outcome.state = EngineState.PARTIAL  # some eligible files were never analyzed
