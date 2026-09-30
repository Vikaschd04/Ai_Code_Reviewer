"""Typed workflow payloads shared by the API (starter) and the worker (executor).

Payloads carry identifiers, hashes and small probe results only; never source text.
"""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

DIAGNOSTIC_WORKFLOW_NAME = "DiagnosticWorkflow"
DIAGNOSTIC_WORKFLOW_ID_PREFIX = "crp-diagnostic-"
DIAGNOSTIC_WORKFLOW_ID_PATTERN = re.compile(
    "^" + re.escape(DIAGNOSTIC_WORKFLOW_ID_PREFIX) + r"[0-9a-f]{32}$"
)


class _Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DiagnosticWorkflowInput(_Contract):
    run_id: UUID
    requested_by: UUID


class ArtifactProbeResult(_Contract):
    key: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    read_back_verified: bool
    deleted: bool


class DatabaseProbeResult(_Contract):
    reachable: bool
    schema_revision: str | None
    expected_revision: str
    schema_current: bool


class DiagnosticWorkflowResult(_Contract):
    run_id: UUID
    worker_identity: str
    artifact: ArtifactProbeResult
    database: DatabaseProbeResult
    started_at: datetime
    completed_at: datetime


def diagnostic_workflow_id(run_id: UUID) -> str:
    return f"{DIAGNOSTIC_WORKFLOW_ID_PREFIX}{run_id.hex}"


# -- intake ------------------------------------------------------------------------------------

INTAKE_WORKFLOW_NAME = "IntakeWorkflow"


def intake_workflow_id(intake_id: UUID) -> str:
    return f"crp-intake-{intake_id.hex}"


class IntakeWorkflowInput(_Contract):
    intake_id: UUID


class IntakeWorkflowResult(_Contract):
    intake_id: UUID
    state: str
    snapshot_id: UUID | None = None
    error_code: str | None = None


# -- scan --------------------------------------------------------------------------------------

SCAN_WORKFLOW_NAME = "ScanWorkflow"
ENGINE_NAMES = ("structure", "graph", "pmd", "eslint", "opengrep", "trivy")
# Platform extractors (not finding engines): their failures make a scan PARTIAL, never FAILED.
EXTRACTOR_NAMES = frozenset({"structure", "graph"})


def scan_workflow_id(scan_id: UUID) -> str:
    return f"crp-scan-{scan_id.hex}"


class ScanWorkflowInput(_Contract):
    scan_id: UUID


class ScanPlan(_Contract):
    scan_id: UUID
    engines: list[str]
    terminal: bool = False


class EngineTask(_Contract):
    scan_id: UUID
    engine: str = Field(pattern="^(structure|graph|pmd|eslint|opengrep|trivy)$")


class EngineTaskResult(_Contract):
    engine: str
    state: str
    findings: int = 0


class FinalizeInput(_Contract):
    scan_id: UUID
    canceled: bool = False


class ScanWorkflowResult(_Contract):
    scan_id: UUID
    state: str


# -- AI runs (P03) -------------------------------------------------------------------------------

AI_RUN_WORKFLOW_NAME = "AiRunWorkflow"


def ai_run_workflow_id(run_id: UUID) -> str:
    return f"crp-ai-{run_id.hex}"


class AiRunInput(_Contract):
    run_id: UUID


class AiRunPlan(_Contract):
    run_id: UUID
    terminal: bool = False


class AiRunFinalize(_Contract):
    run_id: UUID
    canceled: bool = False
    interrupted: bool = False


class AiRunResult(_Contract):
    run_id: UUID
    state: str
