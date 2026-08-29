"""ask_user — the CEO pauses the turn to ask the user (the one asking primitive).

CEO-only: wired in ``runtime.pipeline`` next to ``delegate`` and deliberately NOT in
``build_builtin_registry`` (a delegated worker never talks to the user). This is the
single「向用户发问」primitive — it absorbed the former 引导式开场 (``kickoff``): whether
the CEO is **opening** a producible-but-underspecified request (做网站 / 文档…) or
hitting a **mid-execution** high-cost fork (A vs B / an irreversible step), it asks the
SAME way and through the SAME mechanism.

The turn pauses until the user answers: the card surfaces, the turn finalizes onto a
durable frame (``ToolEffect.SUSPEND``), and the answer returns via the cold
``POST .../resume`` path into the CEO's ReAct loop as this tool's result. 挂起+恢复
preserves any in-flight context — delegate results, read files — and subsumes the
opening 引导 at negligible cost, so the runtime — not the model — owns「该结束还是该挂起」.
可逆低杠杆不要发卡，在回复里写明假设即可。对比与决策见
docs/03-AI核心/Agent协作模式.md（向用户发问）.

The card's content is one adaptive shape (rich when opening, compact mid-task).
``message`` is still required (wire ``question`` / absorb / no-question fallback).
On ordinary cards it is **not** shown as a title — the user-visible question goes in
``questions[].prompt``; ``message`` may hold a batch reason the user may not see.
With no ``questions``, ``message`` remains the only stem. Dedicated cards still use
``message`` as the card title. Optional ``assumptions`` (起步计划 — low-impact
decisions the CEO made for the user, read-only chips), optional ``questions``
(each pre-fillable with a ``default`` so a 想省事 user one-clicks through).
A mid-task A/B is one ``questions`` item plus required ``message``.

A submit answer is ``ToolEffect.CONTINUE`` (the CEO resumes with the user's picks); a
stop is also ``CONTINUE`` with a拒答 breadcrumb + soft guidance (wire ``decision=stop``,
not empty-continue「按默认」) so the CEO sees the cancel and may short-close — same
shape as team_preview cancel / timeout. The question + answer are journaled
(``events._JOURNAL_EVENT_TYPES``) so a reload replays the exchange inline.

结构化挂起 2b + 挂起即收口 (②) / D11 (turn 级落盘 + ``POST .../resume``): like the
``delegate`` checkpoint hook, the suspend is backed by a durable frame — an
:class:`AskUserSuspension` is saved to ``paused_turns`` and the turn ends in place
(``SUSPEND→PAUSED``). All resumes — same session or after restart — go through the
single cold path ``POST .../resume``, which maps the user's answer back to this tool's
result and continues the CEO loop. If the frame cannot be saved ⇒ **explicit failure**
(no in-memory timed wait / no timeout auto-continue). The answer→result mapping is
:func:`result.ask_user_tool_result` so resume shares one source of truth.
"""

from agentcore.tools.builtin.ask_user.result import ask_user_tool_result
from agentcore.tools.builtin.ask_user.tool import AskUserTool

__all__ = ["AskUserTool", "ask_user_tool_result"]
