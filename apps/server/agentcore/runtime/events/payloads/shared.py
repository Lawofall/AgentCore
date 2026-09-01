"""Shared wire leaf types referenced across SSE payload domains."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from agentcore.runtime.debate.types import DebateForm
from agentcore.runtime.events.payloads._base import WirePayload, absent


class UsageBreakdown(WirePayload):
    """Token counts in the ledger short-key form. `cache_hit + cache_miss === input`."""

    input: int
    output: int
    reasoning: int
    cache_hit: int
    cache_miss: int


class CostBreakdown(WirePayload):
    """A run's / turn's cost in integer nano-money (1 unit = 1e9).

    ``currency`` labels ``input``/``cached``/``output``/``total`` — curated CNY or
    community-estimated USD, never converted (this product has no FX). Clients
    must read it to pick a symbol; inferring ¥ from ``pricing_source`` is how BYOK
    dollars once rendered as yuan at ~1/7 of the real amount.
    """

    input: int
    cached: int
    output: int
    total: int
    currency: str
    # Additive: missing on legacy vectors → default curated (compat).
    pricing_source: str = "curated"
    # BYOK estimate total when billed total is 0; absent on platform-only rows.
    estimated_total: int | None = absent()
    # Currency of ``estimated_total`` — the estimate rides the USD community table
    # while ``total`` stays CNY, so on a turn aggregate the two amounts can differ
    # in unit. Absent (legacy / run rows) → read ``currency``.
    estimated_currency: str | None = absent()


class MotionCardSide(WirePayload):
    """One participant on a handoff ``motion_card`` (thin stance, not an argument list)."""

    key: str
    name: str
    stance: str


class MotionCard(WirePayload):
    """Worker-authored「建议开辩」命题卡 — optional on ``RunDebrief``; omitted when absent."""

    motion: str
    sides: list[MotionCardSide]
    fact_pointers: list[str]
    rationale: str
    form: DebateForm


class RunDebrief(WirePayload):
    """完工交接简报 — every field optional; absent when the worker did not call `handoff`."""

    summary: str | None = None
    key_points: list[str] | None = None
    assumptions: str | None = None
    next_steps: str | None = None
    # Leftover on historical debrief JSON; new handoff rounds do not harvest this.
    motion_card: MotionCard | None = absent()


class Vec3(WirePayload):
    """3D position (R3F / Three.js Y-up): x=east, z=south, y=height."""

    x: float
    y: float
    z: float


class Citation(WirePayload):
    url: str
    title: str
    snippet: str | None = absent()
    site: str | None = absent()
    # 证据台账 / 引用即出处：缺字段（老 wire）→ 前端忽略。
    id: str | None = absent()
    date: str | None = absent()
    tier: str | None = absent()  # official | media | unknown | weak
    query: str | None = absent()
    deep_read: bool | None = absent()
    registrant: str | None = absent()
    citable: bool | None = absent()


class CitationsPayload(WirePayload):
    citations: list[Citation]


class TurnEvidenceLedgerEntry(WirePayload):
    """回合调研台账条目（辩论 ``EvidenceLedgerEntry`` 超集）。

    ``registrant`` ↔ 辩论 ``side_key``。``selected`` / ``doc_kind`` 随 ledger JSON 透传。
    """

    id: str  # #r1, #r2, …
    url: str = ""
    title: str = ""
    snippet: str = ""
    site: str = ""
    date: str = ""
    tier: str = "unknown"  # official | media | unknown | weak
    query: str = ""
    deep_read: bool = False
    selected: bool = False
    doc_kind: str = ""  # 可选；启发式如 announcement
    registrant: str = ""
    citable: bool = True


class EvidenceLedgerPayload(WirePayload):
    """Turn 级台账通道（引用即出处 P1 · Q4）。

    - ``delta``：自上次 drain 以来的增量（live mid-turn）
    - ``entries``：全量快照（settle 权威覆盖；与 delta 可同发，客户端以 entries 为准）
    - ``cited_ids``：成稿实际引用的 id 集（P2：``citations_event`` 投影权威；通常仅 settle 携带）
    """

    delta: list[TurnEvidenceLedgerEntry] = Field(default_factory=list)
    entries: list[TurnEvidenceLedgerEntry] | None = absent()
    cited_ids: list[str] | None = absent()


# Opaque alias — emitted as `export type ToolDisplay = Record<string, unknown>`.
ToolDisplayWire = dict[str, Any]
