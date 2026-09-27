"""Structured API errors ``{code, message, request_id, details}``; no tracebacks or host paths."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ErrorResponse(BaseModel):
    code: str = Field(description="Stable machine-readable error code")
    message: str = Field(description="Human-readable explanation without secrets or host paths")
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers


def request_id_of(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def _response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        code=code, message=message, request_id=request_id_of(request), details=details or {}
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(), headers=headers)


_HTTP_CODES = {
    400: "bad_request",
    401: "authentication_required",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    429: "rate_limited",
    503: "dependency_unavailable",
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _response(request, exc.status_code, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        return _response(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Echo locations and messages only; submitted values may contain secrets.
        errors = [
            {"loc": list(error.get("loc", ())), "msg": error.get("msg"), "type": error.get("type")}
            for error in exc.errors()
        ]
        return _response(
            request, 400, "validation_failed", "Request validation failed", {"errors": errors}
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"request_id": request_id_of(request)})
        return _response(request, 500, "internal_error", "An internal error occurred")
