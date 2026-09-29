"""Activity progress reporting that also works when activities run in-process (lite mode)."""

from __future__ import annotations

from temporalio import activity


def heartbeat(message: str) -> None:
    """Heartbeat inside a Temporal activity; a no-op when run by the in-process runner."""
    if activity.in_activity():
        activity.heartbeat(message)
