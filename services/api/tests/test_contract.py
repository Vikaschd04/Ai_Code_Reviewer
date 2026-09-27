"""The committed OpenAPI document must match the implementation (client generation input)."""

from __future__ import annotations

from pathlib import Path

from crp_api.openapi_export import openapi_document, render_openapi

CONTRACT = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "openapi.json"


def test_committed_openapi_matches_the_api() -> None:
    assert CONTRACT.read_text(encoding="utf-8") == render_openapi(), (
        "packages/contracts/openapi.json is stale; run `make contracts`"
    )


def test_every_v1_operation_documents_its_error_shapes() -> None:
    paths = openapi_document()["paths"]
    assert "/v1/health/live" in paths
    for path, operations in paths.items():
        assert path.startswith("/v1/")
        for method, operation in operations.items():
            if path == "/v1/health/live" or method == "delete":
                continue
            codes = set(operation["responses"])
            assert codes & {"401", "403", "404", "409", "503"}, f"{method} {path} lacks errors"
