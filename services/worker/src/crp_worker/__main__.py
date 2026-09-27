"""``crp-worker`` entry point: connect to Temporal and poll the configured task queue."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from pydantic import ValidationError

from crp_core.artifacts import create_artifact_store
from crp_core.config import Settings, describe_settings_error, load_settings
from crp_core.db.session import create_engine_from_settings
from crp_core.log import configure_logging
from crp_core.workflows.gateway import WorkflowUnavailableError
from crp_core.workflows.temporal import connect_temporal
from crp_worker.runtime import build_worker, worker_identity

logger = logging.getLogger("crp_worker")


async def run(settings: Settings) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    client = await connect_temporal(settings)
    engine = create_engine_from_settings(settings)
    identity = worker_identity()
    try:
        worker = build_worker(
            client,
            settings,
            store=create_artifact_store(settings),
            engine=engine,
            identity=identity,
        )
        async with worker:
            logger.info(
                "worker polling",
                extra={"task_queue": settings.temporal_task_queue, "identity": identity},
            )
            await stop.wait()
            logger.info("worker shutting down")
    finally:
        await engine.dispose()


def main() -> int:
    try:
        settings = load_settings()
    except ValidationError as exc:
        print(f"crp-worker: refusing to start: {describe_settings_error(exc)}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level, settings.log_format)
    try:
        asyncio.run(run(settings))
    except WorkflowUnavailableError as exc:
        logger.exception("cannot connect to workflow service", extra={"error": str(exc)})
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
