"""One thought on the conversation graph.

A segment is that thought's one thing and its result, sealed when the thing
is done. Several things in the same thought sit side by side on the upstream
the thought opened from; the next thought hangs off all of them. A fork still
writes a conclusion onto the open call and seals it early.
"""

from __future__ import annotations

import json
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from agentcore.llm.provider.protocol import llm_content_text
from agentcore.runtime.events import run_completed, run_plan, run_started
from agentcore.runtime.events.types import SSEEvent

_SUMMARY_CLIP = 500


@dataclass
class StepChain:
    """Leaves the next call should hang off, plus the call now in flight."""

    execution_id: str
    upstream: list[str]
    goal: str = ""
    think_id: str = ""
    think_label: str = ""
    # Upstream captured when this thought opened, before it is sealed.
    opened_from: list[str] = field(default_factory=list)
    # Segments this thought opened, in order. One thought, one or many things.
    thing_ids: list[str] = field(default_factory=list)
    sealed: set[str] = field(default_factory=set)
    reply_sealed: bool = False
    had_think: bool = False
    opened_rounds: int = 0


_chain: ContextVar[StepChain | None] = ContextVar("conversation_step_chain", default=None)


def bind_step_chain(
    execution_id: str,
    upstream: list[str],
    *,
    goal: str = "",
) -> Token[StepChain | None]:
    chain = StepChain(
        execution_id=execution_id,
        upstream=list(upstream),
        goal=goal,
    )
    return _chain.set(chain)


def reset_step_chain(token: Token[StepChain | None]) -> None:
    _chain.reset(token)


def current_step_chain() -> StepChain | None:
    return _chain.get()


def segment_id(run_id: str, round_idx: int, index: int, action_id: str) -> str:
    token = "".join(ch for ch in action_id if ch.isalnum())[-12:] or "x"
    return f"seg_{run_id}_{round_idx}_{index}_{token}"


def clip_label(text: str) -> str:
    flat = " ".join((text or "").split())
    if not flat:
        return "这段"
    if len(flat) <= 24:
        return flat
    return flat[:24] + "…"


def clip_summary(text: str) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= _SUMMARY_CLIP:
        return flat
    return flat[:_SUMMARY_CLIP] + "…"


def open_think(
    chain: StepChain,
    *,
    run_id: str,
    round_idx: int,
    label: str = "",
) -> list[SSEEvent]:
    """Open the one segment for this thought. The first call uses ``chain.goal``."""
    if chain.think_id and chain.think_id not in chain.sealed:
        return []
    if chain.opened_rounds == 0 and chain.goal.strip():
        raw = chain.goal
    else:
        raw = label or "这段"
    # 卡上按行截断；侧面板要读全文，所以这里留下整句（上限与结论文相同）。
    label = clip_summary(raw) or "这段"
    seg = segment_id(run_id, round_idx, 0, "think")
    chain.think_id = seg
    chain.think_label = label
    chain.had_think = True
    chain.opened_rounds += 1
    depends = list(chain.upstream)
    chain.opened_from = depends
    chain.thing_ids = [seg]
    return [
        run_plan(
            execution_id=chain.execution_id or run_id,
            plan_type="multi_agent",
            task_summary=label,
            agents=[{"id": seg, "role": label, "thinking": True}],
            runs=[
                {
                    "id": seg,
                    "agent_id": seg,
                    "task": label,
                    "depends_on": depends,
                    "parent_run_id": run_id or None,
                    "kind": "agent",
                }
            ],
            act={"act_id": "act-1", "kind": "multi_agent"},
        ),
        run_started(seg, seg, parent_run_id=run_id or None, kind="agent"),
    ]


def describe_thing(name: str, arguments: str) -> str:
    """The phrase for one thing. Prefer what it is doing over the tool name."""
    payload: Any = None
    if arguments and arguments.strip().startswith("{"):
        try:
            payload = json.loads(arguments)
        except json.JSONDecodeError:
            payload = None
    text = ""
    if isinstance(payload, dict):
        for key in ("goal", "query", "path", "url", "command", "pattern"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break
    if not text:
        text = (name or "").strip() or "这件事"
    return clip_summary(text)


def things_from_calls(
    calls: list[Any] | None,
    results: list[Any] | None,
) -> list[tuple[str, str]]:
    """Pair each ordinary tool call with its result.

    A fork (``graph``) owns the open segment and its conclusion, so a batch
    that contains one yields nothing here.
    """
    if not calls:
        return []
    if any(_call_name(call) == "graph" for call in calls):
        return []
    by_id: dict[str, str] = {}
    for message in results or []:
        call_id = str(getattr(message, "tool_call_id", "") or "")
        if call_id:
            by_id[call_id] = llm_content_text(getattr(message, "content", None))
    paired: list[tuple[str, str]] = []
    for call in calls:
        function = getattr(call, "function", None)
        arguments = str(getattr(function, "arguments", "") or "")
        paired.append(
            (
                describe_thing(_call_name(call), arguments),
                by_id.get(str(getattr(call, "id", "") or ""), ""),
            )
        )
    return paired


def _call_name(call: Any) -> str:
    function = getattr(call, "function", None)
    return str(getattr(function, "name", "") or "")


def open_parallel(
    chain: StepChain,
    *,
    run_id: str,
    round_idx: int,
    labels: list[str],
) -> list[SSEEvent]:
    """Open one segment per thing, all on the upstream this thought can see."""
    if chain.think_id and chain.think_id not in chain.sealed:
        return []
    if len(labels) < 2:
        return open_think(
            chain,
            run_id=run_id,
            round_idx=round_idx,
            label=labels[0] if labels else "",
        )
    base = list(chain.upstream)
    chain.opened_from = base
    ids: list[str] = []
    runs: list[dict[str, Any]] = []
    for index, label in enumerate(labels):
        seg = segment_id(run_id, round_idx, index, "thing")
        shown = clip_summary(label) or "这段"
        ids.append(seg)
        runs.append(
            {
                "id": seg,
                "agent_id": seg,
                "task": shown,
                "depends_on": base,
                "parent_run_id": run_id or None,
                "kind": "agent",
            }
        )
    chain.think_id = ids[0]
    chain.think_label = str(runs[0]["task"])
    chain.thing_ids = ids
    chain.had_think = True
    chain.opened_rounds += 1
    events: list[SSEEvent] = [
        run_plan(
            execution_id=chain.execution_id or run_id,
            plan_type="multi_agent",
            task_summary=chain.think_label,
            agents=[{"id": run["id"], "role": run["task"], "thinking": True} for run in runs],
            runs=runs,
            act={"act_id": "act-1", "kind": "multi_agent"},
        )
    ]
    events.extend(
        run_started(seg, seg, parent_run_id=run_id or None, kind="agent") for seg in ids
    )
    return events


def seal_open_things(chain: StepChain, results: list[str]) -> list[SSEEvent]:
    """Seal every segment this thought opened. The next thought hangs off all of them."""
    ids = [seg for seg in chain.thing_ids if seg and seg not in chain.sealed]
    if not ids and chain.think_id and chain.think_id not in chain.sealed:
        ids = [chain.think_id]
    if not ids:
        chain.thing_ids = []
        return []
    if len(ids) == 1:
        sealed = seal_think(chain, results[0] if results else "")
        chain.thing_ids = []
        return [sealed] if sealed is not None else []
    events: list[SSEEvent] = []
    sealed_ids: list[str] = []
    first = seal_think(chain, results[0] if results else "")
    if first is not None:
        events.append(first)
        sealed_ids.append(ids[0])
    for index, seg in enumerate(ids[1:], start=1):
        if seg in chain.sealed:
            sealed_ids.append(seg)
            continue
        chain.sealed.add(seg)
        sealed_ids.append(seg)
        result = results[index] if index < len(results) else ""
        text = clip_summary(result) if (result or "").strip() else ""
        events.append(
            run_completed(
                seg,
                seg,
                output_summary=text,
                duration_ms=0,
                role="这段",
            )
        )
    chain.upstream = sealed_ids or ids
    chain.thing_ids = []
    chain.think_id = ""
    return events


def seal_think(chain: StepChain, summary: str = "") -> SSEEvent | None:
    """Seal the open call. Empty summary leaves no conclusion line.

    Tool payloads are not a conclusion, so this never writes a debrief.
    """
    seg = chain.think_id
    if not seg or seg in chain.sealed:
        return None
    chain.sealed.add(seg)
    text = clip_summary(summary) if (summary or "").strip() else ""
    chain.upstream = [seg]
    chain.think_id = ""
    return run_completed(
        seg,
        seg,
        output_summary=text,
        duration_ms=0,
        role="这段",
    )


def reply_events(
    chain: StepChain,
    *,
    run_id: str,
    text: str,
) -> list[SSEEvent]:
    """The words spoken at the end of this execution are one segment."""
    if chain.reply_sealed:
        return []
    body = (text or "").strip() or "这段没有另开节点。"
    label = clip_label(body)
    seg = segment_id(run_id, 0, 0, "reply")
    if seg in chain.sealed or seg in chain.upstream:
        seg = segment_id(run_id, len(chain.sealed) + 1, 0, "reply")
    chain.reply_sealed = True
    chain.sealed.add(seg)
    depends = list(chain.upstream)
    chain.upstream = [seg]
    summary = clip_summary(body)
    return [
        run_plan(
            execution_id=chain.execution_id or run_id,
            plan_type="multi_agent",
            task_summary=label,
            agents=[{"id": seg, "role": label, "thinking": True}],
            runs=[
                {
                    "id": seg,
                    "agent_id": seg,
                    "task": label,
                    "depends_on": depends,
                    "parent_run_id": run_id or None,
                    "kind": "agent",
                }
            ],
            act={"act_id": "act-1", "kind": "multi_agent"},
        ),
        run_started(seg, seg, parent_run_id=run_id or None, kind="agent"),
        run_completed(
            seg,
            seg,
            output_summary=summary,
            duration_ms=0,
            role="这段",
        ),
    ]
