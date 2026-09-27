"""``crp-api`` entry point: validate settings (including loopback binding), then serve."""

from __future__ import annotations

import logging
import sys

import uvicorn
from pydantic import ValidationError

from crp_api.app import create_app
from crp_core.config import describe_settings_error, load_settings
from crp_core.local_secrets import SecretFileError, read_secret_file
from crp_core.log import configure_logging

logger = logging.getLogger("crp_api")


def main() -> int:
    try:
        settings = load_settings()
        if settings.local_token_file is not None:
            read_secret_file(settings.local_token_file)
    except ValidationError as exc:
        print(f"crp-api: refusing to start: {describe_settings_error(exc)}", file=sys.stderr)
        return 2
    except SecretFileError as exc:
        print(f"crp-api: refusing to start: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level, settings.log_format)
    logger.info(
        "starting API",
        extra={"host": settings.api_host, "port": settings.api_port, "auth": settings.auth_mode},
    )
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        proxy_headers=False,
        server_header=False,
        log_config=None,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
