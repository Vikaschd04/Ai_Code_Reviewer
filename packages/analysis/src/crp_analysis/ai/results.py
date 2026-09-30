"""Structured results a model submits through the final ``submit_*`` tools, and their schemas.

Severity (impact if real), evidence (what was verified in the snapshot) and confidence (the
model's own certainty) are separate fields. Business intent inferred from code is labelled.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from crp_analysis.ai.models import ToolSpec
from crp_core.domain.states import AiConfidence, FindingCategory, Severity


class Anchor(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: Annotated[str, Field(min_length=1, max_length=512)]
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]
    quote: Annotated[str, Field(max_length=600)] = ""


class SubmittedAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answer: Annotated[str, Field(min_length=1, max_length=8000)]
    citations: Annotated[list[Anchor], Field(max_length=12)] = []
    uncertainty: Annotated[str, Field(max_length=2000)] = ""
    abstained: bool = False
    inferred_intent: Annotated[str, Field(max_length=2000)] = ""


class SubmittedFinding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: Annotated[str, Field(min_length=3, max_length=200)]
    category: FindingCategory
    severity: Severity
    severity_rationale: Annotated[str, Field(min_length=1, max_length=1500)]
    confidence: AiConfidence
    anchors: Annotated[list[Anchor], Field(min_length=1, max_length=6)]
    triggering_conditions: Annotated[str, Field(min_length=1, max_length=2000)]
    impact: Annotated[str, Field(min_length=1, max_length=2000)]
    recommendation: Annotated[str, Field(min_length=1, max_length=3000)]
    validation_needed: Annotated[str, Field(max_length=1500)] = ""
    uncertainty: Annotated[str, Field(max_length=1500)] = ""
    related_finding_id: Annotated[str, Field(max_length=64)] | None = None


class FindingAssessment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    verdict: Literal["confirmed", "likely_false_positive", "uncertain"]
    explanation: Annotated[str, Field(min_length=1, max_length=4000)]
    anchors: Annotated[list[Anchor], Field(max_length=6)] = []


class SubmittedReview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: Annotated[str, Field(min_length=1, max_length=4000)]
    findings: Annotated[list[SubmittedFinding], Field(max_length=15)] = []
    finding_assessment: FindingAssessment | None = None
    reviewed_paths: Annotated[list[str], Field(max_length=100)] = []
    not_reviewed: Annotated[str, Field(max_length=2000)] = ""


_ANCHOR_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File path exactly as listed"},
        "start_line": {"type": "integer", "minimum": 1},
        "end_line": {"type": "integer", "minimum": 1},
        "quote": {
            "type": "string",
            "description": "The exact code at these lines, copied without line numbers",
        },
    },
    "required": ["path", "start_line", "end_line", "quote"],
}

SUBMIT_ANSWER = ToolSpec(
    "submit_answer",
    "Submit the final answer to the question. Cite code for every factual claim; set "
    "abstained=true when the snapshot does not contain enough evidence.",
    {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "citations": {"type": "array", "items": _ANCHOR_SCHEMA, "maxItems": 12},
            "uncertainty": {"type": "string", "description": "What remains unknown and why"},
            "abstained": {"type": "boolean"},
            "inferred_intent": {
                "type": "string",
                "description": "Business intent inferred from the code (not stated in it), if any",
            },
        },
        "required": ["answer", "citations", "uncertainty", "abstained"],
    },
)

SUBMIT_REVIEW = ToolSpec(
    "submit_review",
    "Submit the final review. Report only problems you can anchor in code you read; keep "
    "severity (impact if real) separate from confidence (how sure you are).",
    {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "findings": {
                "type": "array",
                "maxItems": 15,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "category": {"type": "string", "enum": [c.value for c in FindingCategory]},
                        "severity": {"type": "string", "enum": [s.value for s in Severity]},
                        "severity_rationale": {"type": "string"},
                        "confidence": {"type": "string", "enum": [c.value for c in AiConfidence]},
                        "anchors": {"type": "array", "items": _ANCHOR_SCHEMA, "minItems": 1},
                        "triggering_conditions": {"type": "string"},
                        "impact": {"type": "string"},
                        "recommendation": {"type": "string"},
                        "validation_needed": {"type": "string"},
                        "uncertainty": {"type": "string"},
                        "related_finding_id": {"type": "string"},
                    },
                    "required": [
                        "title",
                        "category",
                        "severity",
                        "severity_rationale",
                        "confidence",
                        "anchors",
                        "triggering_conditions",
                        "impact",
                        "recommendation",
                    ],
                },
            },
            "finding_assessment": {
                "type": "object",
                "description": "Only when reviewing one existing finding",
                "properties": {
                    "verdict": {
                        "type": "string",
                        "enum": ["confirmed", "likely_false_positive", "uncertain"],
                    },
                    "explanation": {"type": "string"},
                    "anchors": {"type": "array", "items": _ANCHOR_SCHEMA},
                },
                "required": ["verdict", "explanation"],
            },
            "reviewed_paths": {"type": "array", "items": {"type": "string"}},
            "not_reviewed": {"type": "string", "description": "Scope you could not review"},
        },
        "required": ["summary", "findings", "reviewed_paths"],
    },
)
