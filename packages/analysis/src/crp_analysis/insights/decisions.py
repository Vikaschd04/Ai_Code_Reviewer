"""The team's decisions on NFR checkpoints (ADR 0024): "handled outside this code" with a reason,
for checkpoints whose mechanism is not found in the upload (for example monitoring run by the
platform). Stored as append-only versions (table ``nfr_profile_versions``, document schema
``crp-nfr-decisions-v1``); earlier documents of the retired questionnaire hold no decisions.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from crp_analysis.insights.engine import MISSING_CAPABLE

SCHEMA = "crp-nfr-decisions-v1"
MIN_REASON, MAX_REASON = 3, 500


class DecisionError(ValueError):
    """The decision is not valid (message is safe to show)."""


def handled_from(document: Mapping[str, object] | None) -> dict[str, str]:
    """Checkpoint id -> reason, from a stored document (other schemas hold no decisions)."""
    if not document or document.get("schema") != SCHEMA:
        return {}
    handled = document.get("handled")
    if not isinstance(handled, dict):
        return {}
    return {
        str(key): str(value["reason"])
        for key, value in handled.items()
        if isinstance(value, dict) and isinstance(value.get("reason"), str)
    }


def with_handled(current: Mapping[str, str], checkpoint: str, reason: str | None) -> dict[str, str]:
    """The decisions after marking ``checkpoint`` handled (``reason``) or clearing it (None)."""
    if checkpoint not in MISSING_CAPABLE:
        raise DecisionError("only checkpoints about a missing mechanism can be marked as handled")
    updated = dict(current)
    if reason is None:
        updated.pop(checkpoint, None)
        return updated
    text = " ".join(reason.split())
    if not MIN_REASON <= len(text) <= MAX_REASON:
        raise DecisionError(f"say how it is handled in {MIN_REASON} to {MAX_REASON} characters")
    updated[checkpoint] = text
    return updated


def document(handled: Mapping[str, str]) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "handled": {key: {"reason": handled[key]} for key in sorted(handled)},
    }


def sha256(doc: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()
