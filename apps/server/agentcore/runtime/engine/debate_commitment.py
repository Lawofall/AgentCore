"""Debate-commitment soft gate: user picked a debate form on kickoff, CEO never ran it.

Evidence (dev): conversation ``d63dfc35-d63e-4539-8ec4-543fac794e8b`` — kickoff card
question「辩论环节采用哪种形式？」default/answer「辩论（正反攻防）」, CEO later skipped
``debate`` citing「汇总已含论证」. Soft nudge only; never hard-blocks finalize.
Signal source = settled ask_user kickoff tool result / call defaults in the CEO
message window — if that signal is absent, the gate stays silent.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agentcore.llm.provider.protocol import LLMMessage
from agentcore.tools.builtin.ask_user.schema import option_label_is_recommended

# Affirmative form labels seen on kickoff cards (ask_user option labels + FORM_LABELS).
_FORM_AFFIRM = (
    "辩论（正反攻防）",
    "正反攻防",
    "正反辩论",
    "红队压测",
    "红队挑刺",
    "圆桌讨论",
    "多方圆桌",
)
# Explicit opt-out labels on the same question.
_FORM_DECLINE = (
    "不需要辩论环节",
    "不需要辩论",
    "无需辩论",
    "跳过辩论",
    "不要辩论",
)

_ASK_USER_SETTLED_PREFIXES = ("用户答复：", "用户选择：", "用户确认：", "用户确认默认：")
# 「辩论环节…：不需要辩论环节」style lines in the desktop-composed note.
_DECLINE_LINE_RE = re.compile(
    r"辩论[^\n：:]{0,24}[：:][^\n]{0,40}(?:" + "|".join(re.escape(d) for d in _FORM_DECLINE) + r")"
)
_AFFIRM_LINE_RE = re.compile(
    r"辩论[^\n：:]{0,24}[：:][^\n]{0,40}(?:"
    + "|".join(re.escape(a) for a in _FORM_AFFIRM)
    + r")"
)


def debate_gate_nudge_prompt() -> str:
    """One-shot soft reminder: honor the kickoff debate form or state an exemption."""
    return (
        "[系统提示] 辩论承诺复核：开工卡上用户已选择辩论形态，但本回合尚未执行 `debate`。"
        "收尾前请二选一——立即调用 `debate` 兑现；或在答复中明确向用户说明豁免理由"
        "（为何汇总/其他产出可替代辩论）。系统只提示、不代开辩论、不阻断收尾；此后不再打扰。"
    )


def _is_ask_user_settled(text: str) -> bool:
    return any(text.startswith(p) for p in _ASK_USER_SETTLED_PREFIXES)


def _text_selects_debate_form(text: str) -> bool | None:
    """True / False when the settled ask_user prose clearly picks a debate form.

    ``None`` = no debate-form signal in this text (caller may fall back to defaults).
    """
    if _DECLINE_LINE_RE.search(text):
        return False
    if _AFFIRM_LINE_RE.search(text):
        return True
    # Compact picks:「用户选择：辩论（正反攻防）」without the question prompt.
    if any(d in text for d in _FORM_DECLINE) and not any(a in text for a in _FORM_AFFIRM):
        return False
    if any(a in text for a in _FORM_AFFIRM):
        return True
    return None


def _parse_ask_user_args(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(raw or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _debate_question_default(args: dict[str, Any]) -> bool | None:
    """Inspect ask_user ``questions`` for a debate-form choice default."""
    questions = args.get("questions")
    if not isinstance(questions, list):
        return None
    for q in questions:
        if not isinstance(q, dict):
            continue
        prompt = str(q.get("prompt") or "")
        if "辩论" not in prompt:
            continue
        default = str(q.get("default") or "").strip()
        if not default:
            # No default — look at option-name recommendation markup only when user确认.
            options = q.get("options") if isinstance(q.get("options"), list) else []
            for opt in options:
                label = (
                    str(opt.get("label") or "").strip()
                    if isinstance(opt, dict)
                    else str(opt or "").strip()
                )
                if label in _FORM_DECLINE or any(d in label for d in _FORM_DECLINE):
                    continue
                if (
                    label in _FORM_AFFIRM or any(a in label for a in _FORM_AFFIRM)
                ) and option_label_is_recommended(label):
                    return True
            return None
        if any(d in default for d in _FORM_DECLINE):
            return False
        if any(a in default for a in _FORM_AFFIRM):
            return True
        return None
    return None


def _tool_result_for_call(messages: list[LLMMessage], call_id: str) -> str | None:
    if not call_id:
        return None
    for msg in messages:
        if msg.role == "tool" and msg.tool_call_id == call_id and msg.content:
            return msg.content
    return None


def user_selected_debate_form(messages: list[LLMMessage]) -> bool:
    """True when a settled kickoff ask_user committed to a debate form.

    Prefers the composed tool-result prose (desktop picks). Falls back to the
    ask_user call's debate-question default when the user only confirmed
    （「用户确认：按你提出的方向继续。」）. Silent ``False`` when neither signal exists.
    """
    for msg in messages:
        if msg.role == "tool" and msg.content and _is_ask_user_settled(msg.content):
            decided = _text_selects_debate_form(msg.content)
            if decided is not None:
                return decided

    for msg in messages:
        if msg.role != "assistant" or not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            if not tc.function or tc.function.name != "ask_user":
                continue
            args = _parse_ask_user_args(tc.function.arguments or "")
            if args is None:
                continue
            default_pick = _debate_question_default(args)
            if default_pick is None:
                continue
            result = _tool_result_for_call(messages, tc.id)
            if result is None or not _is_ask_user_settled(result):
                continue
            # Explicit decline/affirm in the result wins over the card default.
            decided = _text_selects_debate_form(result)
            if decided is not None:
                return decided
            # Bare「用户确认」/「用户确认默认」→ honor the card default.
            if result.startswith(("用户确认：", "用户确认默认：")):
                return default_pick
    return False
