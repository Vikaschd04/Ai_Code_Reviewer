"""Deterministic OpenAPI export. The generated document is the published API contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from crp_api.app import create_app
from crp_core.config import Settings

# Placeholder values only satisfy validation; export never runs the app lifespan or connects.
_EXPORT_SETTINGS = Settings(
    database_url=SecretStr("postgresql+psycopg://export@127.0.0.1:1/unused"),
    local_token_file=Path("/nonexistent/crp-export-token"),
    artifact_root=Path("/nonexistent/crp-export-artifacts"),
)


def openapi_document() -> dict[str, Any]:
    return create_app(_EXPORT_SETTINGS).openapi()


def render_openapi() -> str:
    return json.dumps(openapi_document(), indent=2, sort_keys=True) + "\n"
