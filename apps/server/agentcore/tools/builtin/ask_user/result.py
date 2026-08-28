"""Map ask_user checkpoint responses to tool results (live + resume shared)."""

from __future__ import annotations

from typing import Any

from agentcore.runtime.checkpoints import CheckpointDecision, CheckpointResponse
from agentcore.tools.protocol import ToolEffect, ToolResult


def _option_path(opt: Any) -> str:
    if not isinstance(opt, dict):
        return ""
    return str(opt.get("path") or "").strip()


def _option_label(opt: Any) -> str:
    if isinstance(opt, dict):
        return str(opt.get("label") or "").strip()
    return str(opt or "").strip()


def confirmed_defaults_summary(
    questions: list[dict[str, Any]] | None = None,
    assumptions: list[dict[str, Any]] | None = None,
) -> str:
    """Join card ``default`` / assumption labels for empty-continue inject (案 B).

    Path-bearing options（新建仓库/本地目录）：when ``default`` matches an option that
    carries ``path``, surface the path alongside the default label（53f08 同族加强）.
    """
    parts: list[str] = []
    for q in questions or []:
        if not isinstance(q, dict):
            continue
        default = str(q.get("default") or "").strip()
        if not default:
            continue
        prompt = str(q.get("prompt") or "").strip()
        path = ""
        for opt in q.get("options") or []:
            if _option_label(opt) == default:
                path = _option_path(opt)
                break
        if not path and ("/" in default or "\\" in default or default.startswith("~")):
            path = default
        head = f"{prompt}={default}" if prompt else default
        if path and path != default:
            head = f"{head}（路径={path}）"
        elif path and prompt:
            head = f"{prompt}={path}"
        parts.append(head)
    for a in assumptions or []:
        if not isinstance(a, dict):
            continue
        label = str(a.get("label") or "").strip()
        if not label:
            continue
        value = str(a.get("value") or "").strip()
        parts.append(f"{label}={value}" if value else label)
    return "；".join(parts)


def structured_options_summary(
    questions: list[dict[str, Any]] | None = None,
) -> str:
    """Join choice labels (+ path when present) for continue/pause restatement (d4d5)."""
    chunks: list[str] = []
    for q in questions or []:
        if not isinstance(q, dict):
            continue
        labels: list[str] = []
        for opt in q.get("options") or []:
            label = _option_label(opt)
            if not label:
                continue
            path = _option_path(opt)
            labels.append(f"{label}（路径={path}）" if path and path != label else label)
        if not labels:
            continue
        prompt = str(q.get("prompt") or "").strip()
        joined = " / ".join(labels)
        chunks.append(f"{prompt}：{joined}" if prompt else joined)
    return "；".join(chunks)


def ask_user_tool_result(
    response: CheckpointResponse,
    *,
    questions: list[dict[str, Any]] | None = None,
    assumptions: list[dict[str, Any]] | None = None,
) -> ToolResult:
    """Map the user's ask_user answer to the tool result the CEO loop consumes.

    The single source of truth for both the live tool (``AskUserTool.execute``) and
    a durable resume (``runtime/pipeline.resume_chat_pipeline``): submit / stop /
    timeout all feed ``CONTINUE`` results so the CEO resumes (stop is **拒答**, not
    empty-continue「按默认」；wire stays ``decision=stop``). Soft guidance on stop
    mirrors team_preview cancel (``kickoff/cancel_guidance``): model sees the refuse
    and may close / rephrase / proceed with assumptions — no in-band ``INTERACT``
    terminal that skips the CEO round.

    答复正文 (α 答复模型): the desktop composes the user's per-question picks + style +
    free-form note into ONE readable ``note`` string (the picks live in the UI, so the
    answer is composed where the data is — no structured wire payload the only-reader CEO
    would just flatten back to prose anyway).

    Empty continue (no note/picks) with card defaults → inject「用户确认默认：…」so the
    resumed CEO must honor those defaults and mark「按确认默认」(案 0cb83288 · B)；
    empty continue with options but no default → inject「复述并沿用上轮确认选项」(d4d5)；
    no card default/options → legacy「按你提出的方向继续」fallback (Cursor 空 continue ≈ 接受默认).
    """
    decision = response.decision
    if decision is CheckpointDecision.ADJUST:
        raise ValueError("ask_user checkpoints do not accept ADJUST; use CONTINUE with note")
    picks = "、".join(response.selected)
    note = response.note.strip()
    if decision is CheckpointDecision.CONTINUE:
        if note and picks:
            output = f"用户选择：{picks}；并补充：\n{note}\n请据此继续。"
        elif note:
            # The desktop's composed answer (per-question picks + style + note) rides here.
            output = f"用户答复：\n{note}\n请据此继续。"
        elif picks:
            output = f"用户选择：{picks}。请按此继续。"
        else:
            defaults = confirmed_defaults_summary(questions, assumptions)
            if defaults:
                output = (
                    f"用户确认默认：{defaults}。"
                    "请按确认默认推进派工/正文，并在正文标「按确认默认」；"
                    "【禁止】借继续另拟一套，也【禁止】叠已结算的确认话术。"
                )
            else:
                options = structured_options_summary(questions)
                if options:
                    output = (
                        f"用户确认继续。请复述并沿用上轮确认选项：{options}。"
                        "【禁止】空转确认、不承接选项；"
                        "【禁止】另拟一套，也【禁止】叠已结算的确认话术。"
                    )
                else:
                    output = "用户确认：按你提出的方向继续。"
        return ToolResult(tool_call_id="", success=True, output=output)
    if decision is CheckpointDecision.STOP:
        # 拒答可见：回灌 CEO（对齐开工卡取消 / OpenAI reject→resume）；非空 continue。
        # 拒答后默认收口——真实回合里「换假设继续」被当成了平级选项，模型接着又起了
        # 一轮工具，用户只能去按硬停止。收口是默认，继续是例外。
        head = "用户取消了澄清，未作答。"
        guidance = (
            "默认据此收口：用正文说清已完成什么、卡在哪、建议的下一步；"
            "【禁止】再弹 ask_user 或开工卡追问（换个问法也不行）。"
            "仅当手上工作已能无歧义推进时才换假设继续，并在正文写明所换假设。"
        )
        output = (
            f"{head}用户留言：{note}\n{guidance}" if note else f"{head}\n{guidance}"
        )
        return ToolResult(tool_call_id="", success=True, output=output)
    # TIMEOUT — never silently picked a branch; let the CEO decide how to close.
    return ToolResult(
        tool_call_id="",
        success=True,
        output="用户未在时限内回应。请基于目前已掌握的信息，自行决定如何稳妥收尾。",
    )


def ask_user_organize_plan_result(
    response: CheckpointResponse, *, plan_id: str, kept_count: int
) -> ToolResult:
    """CONTINUE result for organize_plan — embeds plan_id for file_batch binding."""
    base = ask_user_tool_result(response)
    if response.decision is not CheckpointDecision.CONTINUE:
        return base
    suffix = (
        f"\n整理方案已确认：plan_id={plan_id}，保留 {kept_count} 项。"
        "请用 file_batch(organize_plan_id=该 id, operations=保留项) 分批执行"
        f"（每批≤50），勿再弹审批。完成后可用 file_batch(organize_undo=true) 撤销。"
    )
    return ToolResult(
        tool_call_id="",
        success=True,
        output=(base.output or "") + suffix,
    )


def ask_user_daily_review_result(
    response: CheckpointResponse,
    *,
    applied: int,
    skipped: int,
    errors: tuple[str, ...] = (),
) -> ToolResult:
    """CONTINUE/STOP result after server-side daily_review apply."""
    if response.decision is not CheckpointDecision.CONTINUE:
        return ask_user_tool_result(response)
    err_bit = f"；问题：{'；'.join(errors)}" if errors else ""
    output = (
        f"用户已确认复盘提案。服务端已落盘 {applied} 项"
        f"（跳过 {skipped}）{err_bit}。"
        "请用白话写一段短收尾说明落了什么；"
        "禁止再调用 remember / file_write / update_folder_profile 重复写入。"
    )
    return ToolResult(
        tool_call_id="",
        success=True,
        output=output,
        effect=ToolEffect.CONTINUE,
    )
