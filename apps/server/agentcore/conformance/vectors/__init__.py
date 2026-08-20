"""Curated conformance vectors — representative SSE event sequences (前端技术与架构 §十 SSE 与协议一致性).

Built with the REAL event builders (:mod:`agentcore.runtime.events`). Split by scenario
under this package (``single_agent`` / ``gates`` / ``multi_agent/`` / ``debate/`` / ``legal``
/ ``board`` / ``mcp`` / ``memory`` / ``resume_reload``); protocol vectors aggregate here as ``VECTORS``.
Memory consolidation vectors live separately in ``MEMORY_VECTORS`` (extraction prompt
regression, not protocol fold). Export protocol goldens via
``python -m agentcore.conformance.export``.
"""

from __future__ import annotations

from collections.abc import Callable

from agentcore.runtime.events import SSEEvent

from .board import VECTORS as _BOARD
from .debate import VECTORS as _DEBATE
from .gates import VECTORS as _GATES
from .interactions import VECTORS as _INTERACTIONS
from .legal import VECTORS as _LEGAL
from .mcp import VECTORS as _MCP
from .memory import MEMORY_VECTORS, MemoryConsolidationVector
from .multi_agent import VECTORS as _MULTI_AGENT
from .resume_reload import VECTORS as _RESUME_RELOAD
from .single_agent import VECTORS as _SINGLE_AGENT
from .turn_verdict import VECTORS as _TURN_VERDICT

VECTORS: dict[str, tuple[str, Callable[[], list[SSEEvent]]]] = {
    **_SINGLE_AGENT,
    **_GATES,
    **_INTERACTIONS,
    **_MULTI_AGENT,
    **_DEBATE,
    **_LEGAL,
    **_BOARD,
    **_MCP,
    **_RESUME_RELOAD,
    **_TURN_VERDICT,
}
