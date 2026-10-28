"""Duplicate code with PMD's copy/paste detector (CPD, in the pinned PMD; ADD-A3).

One CPD run per language (Java, JavaScript, TypeScript, Apex, Kotlin, Python) over the upload's
files of that language. Each duplicated block becomes one finding per copy, anchored at that
copy's lines and naming the other copies; its identity is a hash of the duplicated text, so the
issue survives edits elsewhere in the file. The duplicated code itself is never stored: the raw
report keeps only locations and sizes.

Results depend on other files (a block is duplicated only if another copy exists), so they are
not cached per file. Exit contract (``--no-fail-on-violation --no-fail-on-error``): 0 = the report
was written (files that could not be tokenized are listed in it); anything else = failure.
"""

from __future__ import annotations

import hashlib
import shutil
from collections import defaultdict
from dataclasses import dataclass
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
    redact_root,
)
from crp_analysis.engines.process import run_bounded, scrubbed_env
from crp_analysis.frameworks import xmlsafe
from crp_core.domain.states import CoverageOutcome, EngineState

RULE_ID = "crp.duplication.block"
RULESET_ID = "crp-cpd-v1"
MINIMUM_TOKENS = 100  # PMD's and SonarQube's usual threshold for Java
_LANGUAGES = {
    "java": "java",
    "javascript": "ecmascript",
    "typescript": "typescript",
    "apex": "apex",
    "kotlin": "kotlin",
    "python": "python",
}
_MEDIUM_LINES = 40  # long copies are costly to keep in step


@dataclass(frozen=True, slots=True)
class _Copy:
    path: str
    line: int
    end_line: int


class CpdAdapter:
    name = "cpd"
    ruleset_id = RULESET_ID

    def __init__(
        self,
        pmd_home: Path | None,
        *,
        java_heap: str,
        timeout_seconds: float,
        max_output_bytes: int,
        minimum_tokens: int = MINIMUM_TOKENS,
        java_executable: Path | None = None,
    ) -> None:
        self._home = pmd_home
        self._heap = java_heap
        self._timeout = timeout_seconds
        self._max_output = max_output_bytes
        self._minimum = minimum_tokens
        self._java = java_executable or (Path(p) if (p := shutil.which("java")) else None)

    def is_eligible(self, path: str, language: str | None) -> bool:
        return language in _LANGUAGES

    def enabled_rules(self) -> tuple[str, ...]:
        return (RULE_ID,)

    def cache_identity(self) -> CacheIdentity | None:
        return None  # a file's duplicates depend on the other files

    def availability(self) -> Availability:
        if self._home is None or not (self._home / "bin" / "pmd").is_file():
            return Availability(
                False, None, "PMD is not configured (CRP_PMD_HOME); run make engines"
            )
        if self._java is None:
            return Availability(
                False, None, "A Java runtime (java) is required for PMD and was not found"
            )
        jars = sorted((self._home / "lib").glob("pmd-core-*.jar"))
        return Availability(True, jars[0].stem.removeprefix("pmd-core-") if jars else None)

    def run(
        self, root: Path, files: list[str], *, cancel: CancelToken, heartbeat: Heartbeat
    ) -> EngineOutcome:
        availability = self.availability()
        outcome = EngineOutcome(
            state=EngineState.FAILED,
            engine_version=availability.version,
            ruleset_id=RULESET_ID,
            ruleset_sha256=hashlib.sha256(f"{RULESET_ID}:{self._minimum}".encode()).hexdigest(),
        )
        if not availability.available or self._home is None or self._java is None:
            outcome.state = EngineState.UNAVAILABLE
            outcome.error_code = "engine_unavailable"
            outcome.error_message = availability.reason
            return outcome
        from crp_analysis.policy import LANGUAGES  # the inventory's extension -> language map

        groups: dict[str, list[str]] = defaultdict(list)
        for path in files:
            language = LANGUAGES.get(Path(path).suffix.lower())
            if language in _LANGUAGES:
                groups[_LANGUAGES[language]].append(path)
        attempted: list[str] = []
        problems: list[FileProblem] = []
        reports: list[str] = []
        duplications = 0
        duplicated_lines = 0
        for cpd_language, members in sorted(groups.items()):
            if cancel.cancelled:
                outcome.state = EngineState.CANCELED
                outcome.error_code = "canceled"
                problems += [
                    FileProblem(f, CoverageOutcome.NOT_ATTEMPTED.value, "scan canceled")
                    for f in members
                ]
                continue
            heartbeat(f"cpd {cpd_language}: {len(members)} files")
            result = self._run_language(root, cpd_language, members, cancel, heartbeat)
            if isinstance(result, str):
                problems += [
                    FileProblem(f, CoverageOutcome.NOT_ATTEMPTED.value, result) for f in members
                ]
                if outcome.error_code is None:
                    outcome.error_code = "engine_failed"
                    outcome.error_message = result
                continue
            found, unreadable, report = result
            reports.append(report)
            attempted += members
            problems += [
                FileProblem(path, CoverageOutcome.FAILED.value, reason)
                for path, reason in sorted(unreadable.items())
                if path in members
            ]
            for copies, lines, tokens, identity in found:
                duplications += 1
                duplicated_lines += lines * (len(copies) - 1)
                outcome.findings += self._findings(copies, lines, tokens, identity)
        outcome.attempted = sorted(attempted)
        outcome.problems = problems
        if outcome.state is not EngineState.CANCELED:
            failed = sum(1 for p in problems if p.outcome == CoverageOutcome.FAILED.value)
            outcome.state = completed_state(len(attempted), failed)
            if not attempted and files:
                outcome.state = EngineState.FAILED  # every language run failed
            elif outcome.state is EngineState.SUCCEEDED and len(problems) > failed:
                outcome.state = EngineState.PARTIAL  # a language could not be checked
        outcome.raw_report = "\n".join(reports).encode() if reports else None
        outcome.diagnostics = {
            "minimum_tokens": self._minimum,
            "duplications": duplications,
            "duplicated_lines": duplicated_lines,
            "languages": sorted(groups),
        }
        return outcome

    def _run_language(
        self,
        root: Path,
        language: str,
        members: list[str],
        cancel: CancelToken,
        heartbeat: Heartbeat,
    ) -> tuple[list[tuple[list[_Copy], int, int, str]], dict[str, str], str] | str:
        work = root.parent
        (work / "home").mkdir(exist_ok=True)
        file_list = work / f"cpd-{language}-files.txt"
        report = work / f"cpd-{language}.xml"
        file_list.write_text("".join(f"{root / f}\n" for f in members), encoding="utf-8")
        args = [
            str(self._home / "bin" / "pmd") if self._home else "pmd",
            "cpd",
            "--minimum-tokens",
            str(self._minimum),
            "--language",
            language,
            "--file-list",
            str(file_list),
            "--format",
            "xml",
            "--report-file",
            str(report),
            "--encoding",
            "UTF-8",
            "--no-fail-on-violation",
            "--no-fail-on-error",
        ]
        env = scrubbed_env(
            [self._java.parent] if self._java else [],
            work / "home",
            {"PMD_JAVA_OPTS": f"-Xmx{self._heap}"},
        )
        result = run_bounded(
            args,
            cwd=work,
            env=env,
            timeout_seconds=self._timeout,
            max_output_bytes=self._max_output,
            cancel=cancel,
            heartbeat=heartbeat,
            label=f"cpd-{language}",
        )
        if result.cancelled:
            return "scan canceled"
        if result.timed_out:
            return f"duplicate detection exceeded the {self._timeout:g}s time limit"
        if result.exit_code != 0 or not report.is_file():
            return redact_root(
                f"CPD exited with code {result.exit_code}: {result.stderr_tail[-300:]}", root
            )
        try:
            return parse_report(report.read_bytes(), root, set(members))
        except (ValueError, KeyError) as exc:
            return f"CPD report could not be read: {type(exc).__name__}"

    def _findings(
        self, copies: list[_Copy], lines: int, tokens: int, identity: str
    ) -> list[RawFinding]:
        severity = "medium" if lines >= _MEDIUM_LINES else "low"
        found: list[RawFinding] = []
        for copy in copies:
            others = [c for c in copies if c != copy]
            where = ", ".join(f"{c.path}:{c.line}" for c in others[:3])
            more = f" and {len(others) - 3} more" if len(others) > 3 else ""
            found.append(
                RawFinding(
                    path=copy.path,
                    rule_id=RULE_ID,
                    ruleset=RULESET_ID,
                    engine_severity=None,
                    message=(
                        f"These {lines} lines are also in {where}{more}. A change to one copy "
                        "must be repeated in the others."
                    ),
                    start_line=copy.line,
                    start_column=None,
                    end_line=copy.end_line,
                    end_column=None,
                    rule_url="https://pmd.github.io/pmd/pmd_userdocs_cpd.html",
                    severity=severity,
                    details={
                        "lines": lines,
                        "tokens": tokens,
                        "copies": len(copies),
                        "others": [[c.path, c.line, c.end_line] for c in others[:20]],
                    },
                    identity=f"duplicate:{identity}",
                    title=f"Duplicated code ({lines} lines, {len(copies)} copies)",
                )
            )
        return found


def parse_report(
    data: bytes, root: Path, members: set[str]
) -> tuple[list[tuple[list[_Copy], int, int, str]], dict[str, str], str]:
    """Duplications (copies, lines, tokens, identity), files CPD could not read, and the report
    without the duplicated code (paths relative to the upload)."""
    document = xmlsafe.parse(data)
    prefix = str(root.resolve()) + "/"
    plain = str(root) + "/"

    def relative(path: str) -> str:
        return path.removeprefix(prefix).removeprefix(plain)

    found: list[tuple[list[_Copy], int, int, str]] = []
    kept: list[str] = []
    for duplication in document.findall("duplication"):
        copies = sorted(
            {
                _Copy(relative(f.attrs["path"]), int(f.attrs["line"]), int(f.attrs["endline"]))
                for f in duplication.findall("file")
            },
            key=lambda c: (c.path, c.line),
        )
        copies = [c for c in copies if c.path in members]
        if len(copies) < 2:
            continue
        fragment = duplication.child_text("codefragment") or ""
        identity = hashlib.sha256(" ".join(fragment.split()).encode()).hexdigest()[:24]
        lines, tokens = int(duplication.attrs["lines"]), int(duplication.attrs["tokens"])
        found.append((copies, lines, tokens, identity))
        kept.append(
            f"duplication lines={lines} tokens={tokens} "
            + " ".join(f"{c.path}:{c.line}-{c.end_line}" for c in copies)
        )
    failed = {
        relative(e.attrs.get("filename", "")): redact_root(e.attrs.get("msg", "error"), root)[:300]
        for e in document.findall("error")
    }
    return found, failed, "\n".join(kept)
