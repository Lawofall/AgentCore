"""Thrash rebrand guard: reject cold redelegate after a thrashing worker.

When a recent worker finished thrashing (DEGRADED + ``source=ceiling_backstop``)
and a new cold task matches the old topic / artifacts fingerprint, refuse silent
rebrand — take ``continue_from_run_id`` (同队续派入口), or change the role /
write a task that is not like the old topic.

Sibling to :mod:`isomorphic` (same drive admission layer). Does **not** auto-replan,
does not track completion-gap streaks (retired with S3 kind), and does not expand
isomorphic to arbitrary same-role fan-out.

The memory is bounded like the 留人 roster it depends on (:mod:`agentcore.runtime.sessions`):
per-record idle TTL + per-conversation LRU. Both only ever shrink what the gate
remembers — the collision rule and the rejection copy are untouched.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from agentcore.runtime.coordination.isomorphic import tasks_similar
from agentcore.runtime.engine.ceiling import CEILING_BACKSTOP_SOURCE
from agentcore.runtime.runs.constants import (
    DEFAULT_ROSTER_MAX_CONVERSATIONS,
    DEFAULT_ROSTER_TTL_SECONDS,
)

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec, RunState

# Cap recent thrash memory per conversation (FIFO).
_MAX_THRASH_RECORDS = 16
# 记忆存活窗 = 留人 roster 的空闲 TTL（复用同一常量，不另立口径）。本闸给出的出路是对该
# run 设 ``continue_from_run_id`` 带现场续派，而那要求现场还在 roster 里；现场一过期，再拒
# 就是把 CEO 指向一条已经不存在的路。过期只让闸【更少】开火，不新增任何拦截面。
_THRASH_TTL_SECONDS = DEFAULT_ROSTER_TTL_SECONDS
# 进程内最多记多少个会话（LRU 淘汰最久未访问）——与 SessionRegistry 同姿势：模块级 dict
# 无时间戳 / 无淘汰会随进程寿命单调增长。
_MAX_THRASH_CONVERSATIONS = DEFAULT_ROSTER_MAX_CONVERSATIONS


@dataclass(frozen=True, slots=True)
class ThrashRecord:
    """Fingerprint of a thrashing worker for rebrand collision checks.

    ``noted_at`` is stamped by :func:`note_thrashing_worker` when the record enters
    memory (0.0 on a bare fingerprint that was never remembered).
    """

    run_id: str
    task: str
    artifacts: tuple[str, ...] = ()
    role: str = ""
    noted_at: float = 0.0


@dataclass
class _ConversationThrash:
    """One conversation's thrash memory + its idle clock (the registry reaps on it)."""

    records: list[ThrashRecord] = field(default_factory=list)
    last_access: float = field(default_factory=time.time)


# conversation_id → recent thrashing workers (本对话), LRU-ordered.
_thrash_by_conversation: OrderedDict[str, _ConversationThrash] = OrderedDict()


def _reap_idle(now: float) -> None:
    """Drop whole conversations untouched within the TTL (lazy, no sweeper)."""
    idle = [
        cid
        for cid, bucket in _thrash_by_conversation.items()
        if (now - bucket.last_access) > _THRASH_TTL_SECONDS
    ]
    for cid in idle:
        del _thrash_by_conversation[cid]


def _live_records(bucket: _ConversationThrash, now: float) -> list[ThrashRecord]:
    """Drop expired records in place; return the list of what still stands."""
    bucket.records[:] = [
        r for r in bucket.records if (now - r.noted_at) <= _THRASH_TTL_SECONDS
    ]
    return bucket.records


def clear_thrash_registry(conversation_id: str | None = None) -> None:
    """Test helper: drop thrash memory for one conversation or all."""
    if conversation_id is None:
        _thrash_by_conversation.clear()
        return
    _thrash_by_conversation.pop(conversation_id, None)


def note_thrashing_worker(
    conversation_id: str,
    record: ThrashRecord,
) -> None:
    """Remember a thrashing worker for subsequent cold-delegate admission."""
    cid = (conversation_id or "").strip()
    if not cid or not record.run_id:
        return
    now = time.time()
    _reap_idle(now)
    bucket = _thrash_by_conversation.get(cid)
    if bucket is None:
        bucket = _ConversationThrash()
        _thrash_by_conversation[cid] = bucket
    bucket.last_access = now
    live = _live_records(bucket, now)
    # Newest wins on duplicate run_id.
    live[:] = [r for r in live if r.run_id != record.run_id]
    live.append(replace(record, noted_at=now))
    if len(live) > _MAX_THRASH_RECORDS:
        del live[: len(live) - _MAX_THRASH_RECORDS]
    _thrash_by_conversation.move_to_end(cid)
    while len(_thrash_by_conversation) > _MAX_THRASH_CONVERSATIONS:
        _thrash_by_conversation.popitem(last=False)


def recent_thrash_records(conversation_id: str) -> list[ThrashRecord]:
    """Unexpired thrash records for ``conversation_id`` (oldest→newest).

    Expiry is what keeps a 几十轮后 re-opened topic from being rejected by a memory
    nobody can act on any more (see ``_THRASH_TTL_SECONDS``).
    """
    cid = (conversation_id or "").strip()
    if not cid:
        return []
    now = time.time()
    _reap_idle(now)
    bucket = _thrash_by_conversation.get(cid)
    if bucket is None:
        return []
    bucket.last_access = now
    _thrash_by_conversation.move_to_end(cid)
    return list(_live_records(bucket, now))


def is_thrashing_run_state(state: RunState) -> bool:
    """True when a terminal RunState carries hard-ceiling thrashing backstop."""
    for esc in state.escalations or ():
        if not isinstance(esc, dict):
            continue
        if esc.get("source") == CEILING_BACKSTOP_SOURCE:
            return True
    return False


def thrash_record_from_node(
    node: RunSpec,
    state: RunState,
) -> ThrashRecord | None:
    """Build a :class:`ThrashRecord` when ``state`` is thrashing; else ``None``."""
    if not is_thrashing_run_state(state):
        return None
    artifacts: tuple[str, ...] = ()
    deliverable = getattr(node, "deliverable", None)
    if deliverable is not None:
        raw = getattr(deliverable, "artifacts", None) or ()
        artifacts = tuple(str(a) for a in raw if a)
    if not artifacts and state.files_touched:
        # Prefer declared artifacts; fall back to touched paths for fingerprint.
        artifacts = tuple(state.files_touched)
    return ThrashRecord(
        run_id=node.run_id,
        task=str(getattr(node, "task", None) or ""),
        artifacts=artifacts,
        role=str(getattr(node, "role", None) or getattr(node, "agent_name", None) or ""),
    )


def _artifacts_overlap(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    if not a or not b:
        return False
    na = {p.replace("\\", "/").lower().strip("/") for p in a}
    nb = {p.replace("\\", "/").lower().strip("/") for p in b}
    return bool(na & nb)


def _node_artifacts(node: Any) -> tuple[str, ...]:
    deliverable = getattr(node, "deliverable", None)
    if deliverable is None:
        return ()
    raw = getattr(deliverable, "artifacts", None) or ()
    return tuple(str(a) for a in raw if a)


def _node_task(node: Any) -> str:
    return str(getattr(node, "task", None) or "")


def find_thrash_collision(
    new_plan: RunPlan,
    thrash_records: list[ThrashRecord] | tuple[ThrashRecord, ...],
) -> tuple[Any, ThrashRecord] | None:
    """Return ``(cold_node, thrash_record)`` when a cold task collides with thrash memory.

    Nodes that already set ``continue_from_run_id`` are not cold — skip them.
    Newest thrash record wins on ties.
    """
    if not thrash_records or not new_plan.nodes:
        return None
    # Newest first so the most recent thrash is preferred in the reject message.
    ordered = list(reversed(thrash_records))
    for nn in new_plan.nodes:
        continue_from = (getattr(nn, "continue_from_run_id", None) or "").strip()
        if continue_from:
            continue  # 续派 — not a cold rebrand
        n_task = _node_task(nn)
        n_arts = _node_artifacts(nn)
        for rec in ordered:
            if tasks_similar(n_task, rec.task) or _artifacts_overlap(n_arts, rec.artifacts):
                return nn, rec
    return None


def thrash_reject_message(record: ThrashRecord) -> str:
    """Structured rejection body pointing to continue_from_run_id."""
    role = record.role or record.run_id
    return (
        "【再委派已拒绝·触顶换马甲】近期队员"
        f"（【{role}】`{record.run_id}`）因打转收口（DEGRADED / ceiling_backstop），"
        "本次冷派任务与旧题或同 artifacts 高度相似，禁止换马甲从零再读。"
        f"请对该 task 设 continue_from_run_id=`{record.run_id}` 带现场续派；"
        "确需另开请换角色或把任务写得不像旧题。"
    )


def record_thrashing_from_results(
    *,
    conversation_id: str,
    plan: RunPlan,
    results: dict[str, RunState],
) -> list[ThrashRecord]:
    """Scan terminal results and remember thrashing workers; return newly noted."""
    noted: list[ThrashRecord] = []
    for node in plan.nodes:
        state = results.get(node.run_id)
        if state is None:
            continue
        rec = thrash_record_from_node(node, state)
        if rec is None:
            continue
        note_thrashing_worker(conversation_id, rec)
        noted.append(rec)
    return noted
