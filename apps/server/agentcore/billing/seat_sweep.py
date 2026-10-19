"""Background loop: expire paid seats, apply pending downgrades, send reminders."""

from __future__ import annotations

import asyncio

from agentcore.billing.seat_service import run_seat_sweep
from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.db.errors import is_schema_error

logger = get_logger(__name__)


async def seat_sweep_loop() -> None:
    interval = settings.seat_sweep_interval_seconds
    if interval <= 0:
        return
    while True:
        try:
            stats = await run_seat_sweep()
            if any(stats.values()):
                logger.info("seat.swept", **stats)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — a failed sweep must not kill the loop
            if is_schema_error(e):
                logger.error("seat.sweep_failed", error=str(e))
            else:
                logger.warning("seat.sweep_failed", error=str(e))
        await asyncio.sleep(interval)
