"""Deterministic fix recipes: approved, rule-specific transformations with known behaviour.

A recipe either produces exact line edits for one finding or says why it cannot. Recipes never
guess: when the code does not match the narrow shape they handle, no fix is offered (a person or
a bounded AI proposal can still help). Each proposal states what it changes and whether behaviour
can change, and every proposal still goes through validation.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from crp_analysis.fixes.patching import Edit

ESLINT_FIXABLE = {
    "prefer-const": (
        "Declare the variable with const",
        "It is never reassigned, so const states that intent and prevents accidental changes. "
        "This is ESLint's own fix.",
    ),
    "no-var": (
        "Replace var with let or const",
        "Block-scoped declarations avoid hoisting surprises. ESLint applies this fix only where "
        "the variable's scope stays the same.",
    ),
    "eqeqeq": (
        "Use strict equality (===)",
        "ESLint applies this fix only where both sides already have the same type, so the result "
        "of the comparison does not change.",
    ),
}
_STRING = r'"(?:[^"\\\n]|\\.)*"'
_OPERAND = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*(?:\(\))?)*"
_JAVA_COMPARE = re.compile(
    rf"(?P<left>{_OPERAND}|{_STRING})\s*(?P<op>==|!=)\s*(?P<right>{_OPERAND}|{_STRING})"
)
_API = re.compile(r"(<apiVersion>\s*)([^<\s]+)(\s*</apiVersion>)")


@dataclass(frozen=True, slots=True)
class FindingInfo:
    engine: str
    rule_id: str
    path: str
    start_line: int | None
    end_line: int | None
    details: dict[str, object] | None


@dataclass(frozen=True, slots=True)
class RecipeProposal:
    recipe_id: str
    title: str
    explanation: str
    behaviour_note: str | None
    edits: tuple[Edit, ...]


@dataclass(frozen=True, slots=True)
class RecipeOption:
    recipe_id: str
    title: str
    available: bool
    reason: str | None


ReadFile = Callable[[str], str | None]


class NoFix(Exception):
    """The recipe does not apply to this occurrence; the message says why."""


def _line(text: str, number: int | None) -> str:
    lines = text.splitlines()
    if number is None or number < 1 or number > len(lines):
        raise NoFix("the finding's line is not in the file")
    return lines[number - 1]


def _utf16_index(text: str, offset: int) -> int:
    encoded = text.encode("utf-16-le")
    if offset * 2 > len(encoded):
        raise NoFix("ESLint's fix does not match this file")
    return len(encoded[: offset * 2].decode("utf-16-le", errors="strict"))


def eslint_fix(finding: FindingInfo, text: str) -> RecipeProposal:
    title, explanation = ESLINT_FIXABLE[finding.rule_id]
    autofix = (finding.details or {}).get("autofix")
    if not isinstance(autofix, dict):
        raise NoFix("ESLint offered no safe automatic fix for this occurrence")
    start = _utf16_index(text, int(str(autofix["start"])))
    end = _utf16_index(text, int(str(autofix["end"])))
    if text[start:end] != autofix.get("original"):
        raise NoFix("ESLint's fix does not match this file")
    first = text.count("\n", 0, start) + 1
    last = text.count("\n", 0, max(start, end - 1)) + 1 if end > start else first
    lines = text.splitlines()
    line_start = sum(len(line) + 1 for line in lines[: first - 1]) if "\r\n" not in text else None
    if line_start is None:
        raise NoFix("files with Windows line endings are not fixed automatically yet")
    block = "\n".join(lines[first - 1 : last])
    local_start, local_end = start - line_start, end - line_start
    replaced = block[:local_start] + str(autofix["text"]) + block[local_end:]
    edit = Edit(
        finding.path,
        first,
        last,
        tuple(lines[first - 1 : last]),
        tuple(replaced.split("\n")),
    )
    return RecipeProposal(f"eslint:{finding.rule_id}", title, explanation, None, (edit,))


def java_string_equals(finding: FindingInfo, text: str) -> RecipeProposal:
    line = _line(text, finding.start_line)
    matches = [
        m
        for m in _JAVA_COMPARE.finditer(line)
        if (m.group("left").startswith('"')) != (m.group("right").startswith('"'))
    ]
    if len(matches) != 1:
        raise NoFix("only a single comparison with a string literal is fixed automatically")
    match = matches[0]
    literal, value = (
        (match.group("left"), match.group("right"))
        if match.group("left").startswith('"')
        else (match.group("right"), match.group("left"))
    )
    negate = "!" if match.group("op") == "!=" else ""
    fixed = line[: match.start()] + f"{negate}{literal}.equals({value})" + line[match.end() :]
    number = finding.start_line or 1
    return RecipeProposal(
        "java:string-literal-equals",
        "Compare the text with equals()",
        "Calling equals() on the literal compares the characters and is null-safe, which is what "
        "the == comparison intended.",
        "Results differ only where the old code relied on two variables being the same object.",
        (Edit(finding.path, number, number, (line,), (fixed,)),),
    )


def salesforce_api_version(finding: FindingInfo, text: str, read: ReadFile) -> RecipeProposal:
    if not finding.path.endswith("-meta.xml"):
        raise NoFix("only component metadata files are updated automatically")
    project = read("sfdx-project.json")
    target: str | None = None
    if project is not None:
        try:
            data = json.loads(project)
        except json.JSONDecodeError:
            data = None
        value = data.get("sourceApiVersion") if isinstance(data, dict) else None
        if isinstance(value, str) and re.fullmatch(r"\d{2,3}\.0", value) and float(value) > 30:
            target = value
    if target is None:
        raise NoFix("sfdx-project.json declares no supported sourceApiVersion to move to")
    line = _line(text, finding.start_line)
    match = _API.search(line)
    if match is None:
        raise NoFix("the apiVersion element is not on the reported line")
    number = finding.start_line or 1
    fixed = line[: match.start(2)] + target + line[match.end(2) :]
    return RecipeProposal(
        "salesforce:api-version",
        f"Move to API version {target}",
        f"Uses the project's source API version ({target}) instead of a retired version.",
        "Apex and metadata behaviour can change between API versions. Deploy check-only and run "
        "the Apex tests in an authorized org before releasing (not done by refactorX).",
        (Edit(finding.path, number, number, (line,), (fixed,)),),
    )


def options(finding: FindingInfo) -> list[str]:
    """Recipe ids that handle this finding's rule (availability is decided by ``propose``)."""
    if finding.engine == "eslint" and finding.rule_id in ESLINT_FIXABLE:
        return [f"eslint:{finding.rule_id}"]
    if finding.engine == "pmd" and finding.rule_id == "UseEqualsToCompareStrings":
        return ["java:string-literal-equals"]
    if finding.engine == "frameworks" and finding.rule_id == "crp.sf.metadata.retired-api-version":
        return ["salesforce:api-version"]
    if finding.engine == "nfr":
        from crp_analysis.fixes import config_recipes  # lazy: config_recipes imports this module

        recipe = config_recipes.RECIPES.get(finding.rule_id)
        return [recipe] if recipe else []
    return []


def propose(recipe_id: str, finding: FindingInfo, text: str, read: ReadFile) -> RecipeProposal:
    """The recipe's proposal for this finding, or NoFix with the reason."""
    if recipe_id not in options(finding):
        raise NoFix("this recipe does not handle the finding's rule")
    if recipe_id.startswith("eslint:"):
        return eslint_fix(finding, text)
    if recipe_id == "java:string-literal-equals":
        return java_string_equals(finding, text)
    if recipe_id.startswith("nfr:"):
        from crp_analysis.fixes import config_recipes  # lazy: config_recipes imports this module

        return config_recipes.propose(recipe_id, finding, text)
    return salesforce_api_version(finding, text, read)
