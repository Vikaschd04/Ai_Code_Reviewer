"""Deployment-wide AI usage for the current calendar month (UTC), from ``ai_calls``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crp_core.db.models import AiCall


@dataclass(frozen=True, slots=True)
class MonthUsage:
    month_start: datetime
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None  # None when any call's cost is unknown (no configured prices)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def month_start(now: datetime | None = None) -> datetime:
    current = now or datetime.now(UTC)
    return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def month_usage(session: AsyncSession, now: datetime | None = None) -> MonthUsage:
    start = month_start(now)
    row = (
        await session.execute(
            select(
                func.count(AiCall.id),
                func.coalesce(func.sum(AiCall.input_tokens), 0),
                func.coalesce(func.sum(AiCall.output_tokens), 0),
                func.sum(AiCall.cost_usd),
                func.count(AiCall.id).filter(AiCall.cost_usd.is_(None)),
            ).where(AiCall.created_at >= start)
        )
    ).one()
    calls, input_tokens, output_tokens, cost, unknown = row
    return MonthUsage(
        month_start=start,
        calls=int(calls),
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        cost_usd=0.0 if not calls else (None if unknown or cost is None else float(cost)),
    )
