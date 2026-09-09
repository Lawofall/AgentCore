"""Detached asyncio tasks for fire-and-forget work.

The only intentionally shared primitive across workflows and
handoff job shells. Credential / pause / result tables stay product-specific
(see docs/02-架构/后端架构.md · 后台派活三壳).
"""

import asyncio
from collections.abc import Coroutine
from typing import Any

_background_tasks: set[asyncio.Task] = set()


def spawn_background(coro: Coroutine[Any, Any, None]) -> asyncio.Task:
    """Fire-and-forget a coroutine, holding a reference until it completes."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task
