"""Journal / history / process constants shared by EventSink."""

from __future__ import annotations

import copy
from typing import Any

from agentcore.runtime.events.disposition import DURABLE_EVENT_TYPES
from agentcore.runtime.events.types import EventType
from agentcore.runtime.interaction import JOURNAL_SURFACE_EVENTS

# 落 journal 的事件 = 处置表里所有 DURABLE（单一源见 events/disposition.py）。历史上这里手
# 维护第二份清单，与处置表两处易漂移、新增事件易静默遗漏；现直接复用派生集——新增 DURABLE
# 只在 disposition.py 声明一处，穷尽门禁（tests/test_event_disposition.py）保证不漏。
# 各 DURABLE 事件的落库理由见 disposition.py 的 EVENT_DISPOSITION 注释。
_JOURNAL_EVENT_TYPES = DURABLE_EVENT_TYPES

# Surface = 「客户端可见 journal 非空」的门槛事件。无 surface → execution_journal /
# runs_from_entries 对外清空 events（DURABLE 仍落 fact log）。
#
# 交互 required 事件从 INTERACTION_KIND_SPECS.journal_surface 派生（单一源；手维护
# 第二份清单易静默遗漏 — 曾经漏掉 stage_card_required）。非交互门槛事件（图 / 插话 /
# 升级闸）不是 InteractionKind，留在下面这张小表。
_NON_INTERACTION_JOURNAL_SURFACES = frozenset(
    {
        EventType.RUN_PLAN.value,
        EventType.GRAPH_APPEND.value,
        EventType.RUN_ESCALATION.value,
        EventType.USER_INTERJECTION.value,
    }
)
_JOURNAL_SURFACE_TYPES = _NON_INTERACTION_JOURNAL_SURFACES | JOURNAL_SURFACE_EVENTS

_PROCESS_RESULT_CAP = 8000

# Disk-bomb backstop for DURABLE display payloads that have **no** execution-side
# full-text twin (unlike ``tool_use_end.result`` ↔ ``tool_call.result``). Not a
# display budget: ``team_preview_resolved`` is median 98B / ~184KB mean / 7.9MB max
# — 1 MiB sits above legitimate notes and below "one row fills the disk". Marker
# includes original length so a cap is visible. ``run_context`` is excluded (own
# 16KB head+tail + ``channel=system`` exemption).
_JOURNAL_PAYLOAD_SAFETY_CAP = 1_048_576


def cap_process_result(result: object) -> object:
    """Cap a tool result string for process projection / history / journal replay.

    Shared by ``EventSink._accumulate_process``, journal persist of
    ``tool_use_end.result``, and the conformance oracle so live process, reload,
    and golden agree on oversized tool outputs. Live SSE still carries the
    uncapped wire payload; the execution-side full text stays on ``tool_call``."""
    if isinstance(result, str) and len(result) > _PROCESS_RESULT_CAP:
        return result[:_PROCESS_RESULT_CAP] + "…"
    return result


def cap_journal_safety_string(
    value: object, *, limit: int = _JOURNAL_PAYLOAD_SAFETY_CAP
) -> object:
    """Head-clip a pathological journal string; mark original length.

    Idempotent: a capped value's length equals ``limit``, so a second pass is a
    no-op. Short strings (ids, enums, notes) are untouched.
    """
    if not isinstance(value, str) or len(value) <= limit:
        return value
    marker = f"\n\n[journal_capped original_chars={len(value)} cap={limit}]"
    keep = limit - len(marker)
    if keep < 1:
        return value[: max(limit - 1, 0)] + "…"
    return value[:keep] + marker


def _cap_oversized_strings(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str):
                value[key] = cap_journal_safety_string(item)
            else:
                _cap_oversized_strings(item)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, str):
                value[index] = cap_journal_safety_string(item)
            else:
                _cap_oversized_strings(item)


def journal_payload_for_persist(
    event_type: str, payload: dict[str, Any] | None
) -> dict[str, Any]:
    """Copy a DURABLE payload for journal persist; leave the live SSE payload intact.

    ``tool_use_end.result`` uses the process-lane 8k budget. Other string fields get
    the 1 MiB safety cap, except ``run_context`` (already budgeted at emit).
    """
    persisted = copy.deepcopy(payload) if payload else {}
    if event_type == EventType.TOOL_USE_END.value:
        persisted["result"] = cap_process_result(persisted.get("result"))
    if event_type != EventType.RUN_CONTEXT.value:
        _cap_oversized_strings(persisted)
    return persisted


_HISTORY_SKIP_TYPES = frozenset(
    {
        EventType.TOOL_PROGRESS,
        # 工具执行阶段进度 (transport-only liveliness): like TOOL_PROGRESS, a running-tool
        # phase ping is live-stream only — not replayed on reload (the tool is already done)
        # and never journaled / accumulated into the process timeline.
        EventType.TOOL_USE_PROGRESS,
        EventType.RUN_TOOL_PROGRESS,
        EventType.MESSAGE_END,
        EventType.ERROR,
        EventType.WORKSPACE_OP_REQUIRED,
        EventType.HANDOFF_SNAPSHOT_DONE,
        EventType.HANDOFF_JOB_STARTED,
        EventType.HANDOFF_APPLY_DONE,
    }
)

_HISTORY_COALESCE_TURN = frozenset({EventType.CONTENT_DELTA, EventType.REASONING_DELTA})

_HISTORY_COALESCE_RUN = frozenset({EventType.RUN_OUTPUT_DELTA, EventType.RUN_REASONING_DELTA})
