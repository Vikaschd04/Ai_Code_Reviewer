"""Serve the built single-page web UI from the API process (one origin for UI and API).

Used by the container deployment (``CRP_WEB_STATIC_DIR``); local development keeps the Vite dev
server. API routes under ``/v1`` are registered first and always take precedence. HTML gets a
strict Content-Security-Policy; content-hashed assets are cacheable forever.
"""

from __future__ import annotations

from pathlib import Path

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'self'; form-action 'self'; object-src 'none'"
)


class WebAppFiles(StaticFiles):
    def __init__(self, directory: Path) -> None:
        if not (directory / "index.html").is_file():
            raise RuntimeError(f"CRP_WEB_STATIC_DIR has no index.html: {directory}")
        super().__init__(directory=directory, html=True)

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if path.startswith("assets/") and response.status_code == 200:
            response.headers["cache-control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["cache-control"] = "no-cache"
        response.headers["content-security-policy"] = CONTENT_SECURITY_POLICY
        return response
