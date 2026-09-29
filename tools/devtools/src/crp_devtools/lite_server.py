"""Single-process server for the lite profile: web UI + API + in-process workflow runner.

Used by ``crp-dev hosted`` when ``CRP_PROFILE=lite`` (free-tier hosts with ~512 MB of memory and
no persistent disk). Same API and UI; scans run inside this process one engine at a time
(ADR 0010). Settings come from ``CRP_*`` environment variables like ``crp-api``.
"""

from __future__ import annotations

import os
import sys
import threading

import uvicorn
from pydantic import ValidationError

from crp_analysis.engines.trivy import DB_LOCK
from crp_api.app import create_app
from crp_core.artifacts import create_artifact_store
from crp_core.config import describe_settings_error, load_settings
from crp_core.db.session import create_engine_from_settings
from crp_core.local_secrets import SecretFileError, read_secret_file
from crp_core.log import configure_logging
from crp_devtools import trivy_db
from crp_worker.inline import InlineWorkflowGateway


def main() -> int:
    try:
        settings = load_settings()
        if settings.local_token_file is not None:
            read_secret_file(settings.local_token_file)
    except ValidationError as exc:
        print(f"crp-lite: refusing to start: {describe_settings_error(exc)}", file=sys.stderr)
        return 2
    except SecretFileError as exc:
        print(f"crp-lite: refusing to start: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level, settings.log_format)
    if (
        os.environ.get("CRP_TRIVY_DB_AUTO_REFRESH", "1") != "0"
        and settings.trivy_home is not None
        and settings.trivy_cache_dir is not None
    ):
        # Scans run in this process: swap copies under the analyzer's DB lock.
        trivy_db.start_refresh_thread(
            settings.trivy_home, settings.trivy_cache_dir.parent, threading.Event(), DB_LOCK
        )
    store = create_artifact_store(settings)
    gateway = InlineWorkflowGateway(settings, store, create_engine_from_settings(settings))
    uvicorn.run(
        create_app(settings, workflow_gateway=gateway, artifact_store=store),
        host=settings.api_host,
        port=settings.api_port,
        proxy_headers=False,
        server_header=False,
        log_level=settings.log_level.lower(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
