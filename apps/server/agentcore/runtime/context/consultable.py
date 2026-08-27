"""Small shared shape for「目录 + 按名取文」consult sources (上下文工程 · 按需与写侧配额).

skill / rule / memory 均适配本 Protocol；低频工具另走
:class:`~agentcore.runtime.context.consult_sources.ToolConsultSource`（不共享 Tool 基类）。
四源经 :class:`~agentcore.runtime.context.consult_sources.MergedConsultSource` 聚合，
供 ``consult`` 与提示词 ``<按需目录>`` 共用（目录与按名拉取不可漂移）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ConsultDirectoryEntry:
    """One catalog row: consult ``name`` plus optional one-line summary."""

    name: str
    summary: str = ""


@runtime_checkable
class Consultable(Protocol):
    """Directory + fetch-by-name — the shared consult surface (not a Provider)."""

    async def list_directory(self, user_id: str) -> Sequence[ConsultDirectoryEntry]:
        """Names (+ optional summaries) the model may consult this turn."""
        ...

    async def fetch_by_name(self, user_id: str, name: str) -> str | None:
        """Full body for ``name``, or ``None`` on miss (caller soft-misses)."""
        ...
