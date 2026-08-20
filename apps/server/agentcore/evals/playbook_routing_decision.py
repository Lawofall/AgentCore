"""CEO first-move loop for playbook routing eval (stops at ASK / DELEGATE / DEBATE / DIRECT).

Owned by the eval package — not loaded from ``scripts/archive``. Consult / recon
execute and continue; worker fan-out does not. Credentials are the caller's
(``eval_credentials`` in the live runner); this module never reads
``PLATFORM_API_KEY``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from agentcore.evals.playbook_routing import parse_delegate_rich
from agentcore.llm.profiles import build_request
from agentcore.llm.provider.protocol import LLMMessage, TokenUsage
from agentcore.runtime.engine.governance import (
    LOCAL_RECON_TOOLS,
    create_loop_controller,
    maybe_inject_team_gate,
    resolve_openai_tool_defs,
)

_RECON = frozenset({"file_read", "file_list", "grep", "web_search", "read_url", "git"})
_TERMINAL = frozenset({"ask_user", "delegate", "debate", "replan"})
_CONSULT = frozenset({"consult", "consult_skill"})


@dataclass
class FirstMove:
    action: str
    tool_name: str | None
    rounds: int
    trail: list[str]
    reasoning: str
    first_action: str = ""
    detour: list[str] = field(default_factory=list)
    delegate_summary: dict | None = None
    detail: str = ""
    usage: dict[str, int] = field(default_factory=dict)


def _classify_action(first_tool: str | None) -> str:
    if first_tool is None:
        return "DIRECT"
    if first_tool == "ask_user":
        return "ASK"
    if first_tool == "delegate":
        return "DELEGATE"
    if first_tool in _CONSULT:
        return "CONSULT"
    if first_tool == "debate":
        return "DEBATE"
    if first_tool in _RECON:
        return "RECON_UNDECIDED"
    return "OTHER"


def _consult_names(calls) -> list[str]:
    names: list[str] = []
    for tc in calls:
        if tc.function.name not in _CONSULT:
            continue
        try:
            a = json.loads(tc.function.arguments or "{}")
            n = str(a.get("name") or a.get("topic") or "").strip() or "(unnamed)"
        except json.JSONDecodeError:
            n = "(bad_args)"
        names.append(n)
    return names


async def _feed_tool_calls(
    messages, calls, chat_tools, ctx, trail: list[str], *, assistant_content: str | None = None
) -> None:
    messages.append(
        LLMMessage(role="assistant", content=assistant_content or None, tool_calls=calls)
    )
    for tc in calls:
        trail.append(tc.function.name)
        try:
            tool = chat_tools.get(tc.function.name)
            args = json.loads(tc.function.arguments or "{}")
            tres = await tool.execute(args, ctx)
            out = tres.output if tres.success else f"(error: {tres.error})"
        except Exception as exc:  # noqa: BLE001 — 探针容错，单工具失败不拆整轮
            out = f"(probe exec error: {exc})"
        cap = 6000 if tc.function.name in _CONSULT else 1500
        messages.append(
            LLMMessage(role="tool", tool_call_id=tc.id, content=(out or "(empty)")[:cap])
        )


def _flatten_delegate_args(raw_json: str) -> str:
    try:
        dargs = json.loads(raw_json or "{}")
    except json.JSONDecodeError:
        return raw_json or "{}"
    if not isinstance(dargs, dict):
        return raw_json or "{}"
    raw_tasks = dargs.get("tasks")
    if isinstance(raw_tasks, str):
        try:
            parsed = json.loads(raw_tasks)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            dargs = {**dargs, "tasks": parsed}
    return json.dumps(dargs, ensure_ascii=False)


async def run_until_terminal(
    provider,
    profile,
    model: str,
    ceo_prompt: str,
    tool_defs,
    chat_tools,
    ctx,
    user_message: str,
    max_rounds: int,
) -> FirstMove:
    """CEO 决策环：探路 / consult 续跑，直到发卡、派团队、开辩或直答。"""
    messages = [
        LLMMessage(role="system", content=ceo_prompt),
        LLMMessage(role="user", content=user_message),
    ]
    usage = TokenUsage()
    trail: list[str] = []
    detour: list[str] = []
    first_action = ""
    last_reasoning = ""
    pre_action_reasoning = ""
    pre_action_frozen = False
    inv_tools = frozenset(_RECON)
    gate_controller = create_loop_controller(inv_tools)
    disabled_tools: set[str] = set()
    live_tool_defs = tool_defs

    def _freeze_pre_action(reasoning: str) -> None:
        nonlocal pre_action_reasoning, pre_action_frozen
        if pre_action_frozen:
            return
        pre_action_reasoning = (reasoning or "").strip() or last_reasoning
        pre_action_frozen = True

    def _pack(
        action: str,
        *,
        tool_name: str | None,
        rounds: int,
        detail: str,
        delegate_summary: dict | None = None,
    ) -> FirstMove:
        if not pre_action_frozen:
            _freeze_pre_action(last_reasoning)
        return FirstMove(
            action=action,
            tool_name=tool_name,
            rounds=rounds,
            trail=list(trail),
            reasoning=pre_action_reasoning or last_reasoning,
            first_action=first_action or action,
            detour=list(detour),
            delegate_summary=delegate_summary,
            detail=detail,
            usage={
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            },
        )

    for round_idx in range(max_rounds):
        request = build_request(
            profile,
            messages,
            tools=live_tool_defs,
            tool_choice="auto",
            stream=False,
            model=model,
        )
        resp = await provider.complete(request)
        usage = usage + resp.usage
        reasoning = resp.reasoning_content or ""
        if reasoning:
            last_reasoning = reasoning

        calls = resp.tool_calls or []
        if not calls:
            if not first_action:
                first_action = "DIRECT"
            _freeze_pre_action(reasoning or last_reasoning)
            return _pack(
                "DIRECT",
                tool_name=None,
                rounds=round_idx + 1,
                detail="正文: " + (resp.content or "").replace("\n", " ")[:80],
            )

        _freeze_pre_action(reasoning or last_reasoning)
        terminal = [c for c in calls if c.function.name in _TERMINAL]
        consults = [c for c in calls if c.function.name in _CONSULT]
        recon_only = (
            not terminal
            and not consults
            and all(c.function.name in _RECON for c in calls)
        )

        if terminal:
            dc = terminal[0]
            name = dc.function.name
            action = _classify_action(name)
            if not first_action:
                first_action = action
            delegate_summary = None
            detail = ""
            if name == "delegate":
                delegate_summary = parse_delegate_rich(
                    _flatten_delegate_args(dc.function.arguments or "{}")
                )
                detail = (
                    f"playbook={delegate_summary.get('playbook')!r} "
                    f"intensity={delegate_summary.get('intensity')!r} "
                    f"tasks={delegate_summary['task_count']}"
                )
            elif name == "ask_user":
                try:
                    parsed = json.loads(dc.function.arguments or "{}")
                    qs = parsed.get("questions") or []
                    detail = f"questions={len(qs)} | {(parsed.get('message') or '')[:50]}"
                except json.JSONDecodeError:
                    detail = "(ask args 非法 JSON)"
            else:
                detail = (dc.function.arguments or "")[:80]
            if detour:
                detail = (detail + f" | detour={detour}").strip(" |")
            return _pack(
                action,
                tool_name=name,
                rounds=round_idx + 1,
                detail=detail,
                delegate_summary=delegate_summary,
            )

        if consults:
            names = _consult_names(consults)
            if not first_action:
                first_action = "CONSULT"
            detour.extend(names)
            await _feed_tool_calls(
                messages, calls, chat_tools, ctx, trail, assistant_content=resp.content
            )
            continue

        if recon_only:
            if not first_action:
                first_action = f"RECON({calls[0].function.name})"
            await _feed_tool_calls(
                messages, calls, chat_tools, ctx, trail, assistant_content=resp.content
            )
            for tc in calls:
                name = tc.function.name
                if name in inv_tools:
                    gate_controller._investigation_calls += 1
                    if name in LOCAL_RECON_TOOLS:
                        gate_controller._local_recon_calls += 1
            if maybe_inject_team_gate(
                gate_controller,
                messages=messages,
                run_id=str(ctx.run_id or "eval-playbook"),
                round_idx=round_idx,
                role="captain",
                disabled_tools=disabled_tools,
                investigation_tools=inv_tools,
            ):
                trail.append("team_gate")
                live_tool_defs = resolve_openai_tool_defs(chat_tools, None, disabled_tools)
            continue

        if not first_action:
            first_action = _classify_action(calls[0].function.name)
        await _feed_tool_calls(
            messages, calls, chat_tools, ctx, trail, assistant_content=resp.content
        )
        for tc in calls:
            name = tc.function.name
            if name in inv_tools:
                gate_controller._investigation_calls += 1
                if name in LOCAL_RECON_TOOLS:
                    gate_controller._local_recon_calls += 1
        if maybe_inject_team_gate(
            gate_controller,
            messages=messages,
            run_id=str(ctx.run_id or "eval-playbook"),
            round_idx=round_idx,
            role="captain",
            disabled_tools=disabled_tools,
            investigation_tools=inv_tools,
        ):
            trail.append("team_gate")
            live_tool_defs = resolve_openai_tool_defs(chat_tools, None, disabled_tools)

    return _pack(
        "RECON_UNDECIDED",
        tool_name=None,
        rounds=max_rounds,
        detail=f"(未决，达轮上限) detour={detour}" if detour else "(仍在探路，达轮上限)",
    )
