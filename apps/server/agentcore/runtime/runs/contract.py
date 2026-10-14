"""Contract gate: mechanical checks on a worker run's output.

A worker's product is accepted only if it satisfies its node's delivery spec
(:class:`Deliverable`). The mechanical layer is non-empty product (always on)
and declarative ``artifacts`` existence against the workspace
(``artifact_dir`` miss is not a reminder). Prose quality is not an engine gate.

Pinned-path delivery (:func:`is_file_deliverable` — non-empty ``artifacts`` /
non-empty ``artifact_dir``) treats successful writes or a usable handoff brief as
product. Placeholder / self-note scans and static page QA are gone.

Disposition (retry with feedback; still short → COMPLETED with reminders, never
FAILED for ``strict``) lives in the executor. This module is a pure function.

→ 见设计: docs/03-AI核心/执行引擎架构设计.md §八（Run 模型）
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from agentcore.runtime.runs.artifact_paths import (
    is_opaque_source_data_path,
    is_table_deliverable_path,
)
from agentcore.runtime.runs.types import Deliverable, deliverable_expects_landing
from agentcore.workspace._paths import strip_root_label_prefix

# Sandbox absolute paths models declare (``/workspace/…``) must compare as
# workspace-relative — same rewrite file tools use before the containment guard.
_ARTIFACT_ROOT_LABEL = "workspace"

# Downstream handoff: non-empty closing-round 便条 (stored as debrief.summary).
# Historical key_points-only briefs do not satisfy the gate. No word-count floor.


@dataclass
class ContractVerdict:
    """Outcome of checking one output against its contract."""

    ok: bool
    failures: list[str] = field(default_factory=list)
    # Soft signals (e.g.「示例数据」「虚构」) — never flip ``ok`` by themselves.
    warnings: list[str] = field(default_factory=list)
    # Structured stamps for the same ``warnings`` (reason / severity). Executor
    # copies these onto ``delivery_gaps`` so CEO collect/format read fields, not copy.
    warning_rows: list[dict[str, str]] = field(default_factory=list)
    # Unused leftover (always empty). Keep the slot so executor copies stay stable.
    soft_failures: list[str] = field(default_factory=list)
    # P1c visual critic critical findings — flip ``ok`` for up to 2 reworks, then
    # demote to warnings (partial). Populated by the executor after hard gates.
    visual_failures: list[str] = field(default_factory=list)


def is_file_deliverable(deliverable: Deliverable | None) -> bool:
    """Whether the deliverable's product is expected to land as workspace files.

    Non-empty ``artifacts`` or non-empty ``artifact_dir`` mean the product is
    on disk — so content checks read landed files alongside the chat body.
    Omitted / empty / ``None`` keep body-only semantics.
    """
    return deliverable_expects_landing(deliverable)


def _stamp_warning_rows(
    warnings: list[str],
    *,
    reason: str,
    severity: str = "",
) -> list[dict[str, str]]:
    """Attach a structured reason/severity to each warning string."""
    rows: list[dict[str, str]] = []
    for text in warnings:
        if not text:
            continue
        row: dict[str, str] = {"description": text, "reason": reason}
        if severity:
            row["severity"] = severity
        rows.append(row)
    return rows


def _normalize_source_relpath(path: str) -> str:
    """Workspace-relative POSIX form for source-path comparison."""
    return path.replace("\\", "/").strip().lstrip("./")


def collect_opaque_source_data_paths(
    *,
    material_paths: Iterable[str] | None = None,
    workspace_paths: Iterable[str] | None = None,
    landed_paths: Iterable[str] | None = None,
) -> list[str]:
    """This-turn source files workers cannot reliably parse without execution.

    Provenance, not names: this-turn attachments (``material_paths``) plus
    pre-existing workspace files of opaque types. Historical ``attachments/``
    entries that are not this-turn materials are skipped. This-run writes
    (``landed_paths``) are not sources. ``AgentCore/`` draft tree leftovers
    are not user source files.
    """
    from agentcore.workspace.sparse_listing import is_attachment_path

    def _norm(raw: str) -> str:
        return _normalize_source_relpath(raw)

    landed = {_norm(p) for p in (landed_paths or ()) if p}
    material_set = {_norm(p) for p in (material_paths or ()) if p}
    out: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        if not path or path in seen or not is_opaque_source_data_path(path):
            return
        seen.add(path)
        out.append(path)

    for raw in material_paths or ():
        _add(_norm(raw))
    for raw in workspace_paths or ():
        path = _norm(raw)
        if not path or path in landed:
            continue
        if is_attachment_path(path) and path not in material_set:
            continue
        if path == "AgentCore" or path.startswith("AgentCore/"):
            continue
        _add(path)
    return out


def _no_exec_table_gap(
    *,
    can_execute: bool,
    workspace_paths: list[str] | None,
    source_data_paths: list[str] | None,
) -> tuple[str, dict[str, str]] | None:
    """Hard gap: no-exec + opaque source data file + landed table file.

    Premise is structural (this-turn attachments / workspace source type).
    Inline data with no source file is not a gap — landing csv/xlsx is fine.
    """
    if can_execute:
        return None
    has_opaque_source = any(
        p and is_opaque_source_data_path(p) for p in (source_data_paths or ())
    )
    if not has_opaque_source:
        return None
    from agentcore.runtime.runs.gap_reasons import REASON_NO_EXEC_TABLE

    source_set = {
        _normalize_source_relpath(p) for p in (source_data_paths or ()) if p
    }
    seen: set[str] = set()
    paths: list[str] = []
    for raw in workspace_paths or []:
        if not raw or not is_table_deliverable_path(raw):
            continue
        rel = _normalize_source_relpath(raw)
        if not rel or rel in seen or rel in source_set:
            continue
        seen.add(rel)
        paths.append(raw)
    if not paths:
        return None
    listed = "、".join(f"`{p}`" for p in paths[:6])
    more = f" 等 {len(paths)} 个" if len(paths) > 6 else ""
    text = (
        f"无执行环境却落了表文件：{listed}{more}。"
        "本回合完整交付应是结构报告 + 待跑脚本，禁止用手抄表交差。"
    )
    return text, {"description": text, "reason": REASON_NO_EXEC_TABLE}


# 定案 B · 终态可见性：per-worker soft tip 前缀，CEO / delivery_status 按角色辨认。
_MEMBER_WAVE_UNDELIVERED = "本队员本波未交卷"


def zero_files_gap_message(*, landing_failure_kind: str | None = None) -> str:
    """User/admin-facing zero-disk gap copy, attributed by real cause when known.

    Leads with ``本队员本波未交卷`` so CEO / ``delivery_status`` can attribute the
    soft tip per worker (定案 B). Keeps the shared marker ``未把产物写入工作区``
    so :func:`is_zero_files_gap` and delivery projection stay aligned. Does
    **not** invent new gap reason codes — callers keep ``files_not_landed``.
    """
    head = f"{_MEMBER_WAVE_UNDELIVERED}："
    if landing_failure_kind == "channel_dead":
        # Align with WORKSPACE_CHANNEL_DEAD_RETIRE_STEER: prose/handoff close-out,
        # not "don't paste to fake landing" (that framing fights dead-channel steer).
        return (
            f"{head}未把产物写入工作区：写盘通道不可用（工作区/本地文件连不上），"
            "落盘工具已失败——"
            "请在 handoff 或正文交结论，禁止再尝试落盘；"
            "可请用户恢复工作区通道后重试"
        )
    from agentcore.runtime.runs.serialize import format_file_landing_tools_slash

    # 落盘工具清单一律由 serialize 的格式化函数生成——手写子集会漏笔。
    tools = format_file_landing_tools_slash()
    if landing_failure_kind == "write_failed":
        return (
            f"{head}未把产物写入工作区：已尝试写盘但未成功落盘（工具失败），"
            f"请用 {tools} 修复后重写——"
            "此缺口来自写盘失败，而非「粘在回复正文」"
        )
    return (
        f"{head}未把产物写入工作区：交付物须用 {tools} 落盘，而非粘在回复正文里"
    )


def check_contract(
    content: str,
    deliverable: Deliverable | None,
    *,
    files_written: int = 0,
    debrief: dict[str, Any] | None = None,
    workspace_paths: list[str] | None = None,
    landing_failure_kind: str | None = None,
    can_execute: bool = True,
    source_data_paths: list[str] | None = None,
) -> ContractVerdict:
    """Check ``content`` against ``deliverable``; return a verdict + human reasons.

    The non-empty baseline always applies — an empty product is never acceptable,
    even with no deliverable. When a deliverable is given, path existence is a
    soft reminder, not a hard fail.

    ``files_written`` is the count of workspace paths the run actually landed (from
    ``files_touched_from_transcript``). A pinned-path landing with zero successful
    writes becomes a soft ``warnings`` tip. ``landing_failure_kind`` (optional)
    attributes the tip: ``channel_dead`` / ``write_failed`` vs paste framing.

    ``workspace_paths`` is the flat path index used to reconcile ``artifacts``
    patterns. Callers pass the live workspace listing unioned with this run's
    ``files_touched``; ``None`` / empty means the workspace looks empty.

    ``can_execute`` is the turn's execution-class fact. False + a this-turn opaque
    source data file + a landed spreadsheet/table file is a gap — hand-copied
    result sheets are not no-exec complete delivery.

    Workers often finish with ``write`` + ``handoff`` and no streamed prose.
    The baseline also accepts workspace writes (``files_written > 0``) or a usable
    ``handoff`` debrief.
    """
    text = content.strip()
    if not _has_product_signal(text, files_written, debrief):
        return ContractVerdict(ok=False, failures=["产出为空"])

    zero_files_warnings: list[str] = []
    path_mismatch_warnings: list[str] = []
    if deliverable is not None:
        if is_file_deliverable(deliverable) and files_written <= 0:
            zero_files_warnings.append(
                zero_files_gap_message(landing_failure_kind=landing_failure_kind)
            )
        if deliverable.artifacts:
            missing = missing_artifacts(deliverable.artifacts, workspace_paths or [])
            if missing:
                listed = "、".join(f"`{p}`" for p in missing)
                path_mismatch_warnings.append(f"声明的交付物路径未落盘：{listed}")

    from agentcore.runtime.runs.gap_reasons import (
        REASON_FILES_NOT_LANDED,
        REASON_PATH_HINT,
    )

    warnings = [
        *zero_files_warnings,
        *path_mismatch_warnings,
    ]
    warning_rows = [
        *_stamp_warning_rows(
            zero_files_warnings, reason=REASON_FILES_NOT_LANDED, severity="warning"
        ),
        *_stamp_warning_rows(
            path_mismatch_warnings, reason=REASON_PATH_HINT, severity="warning"
        ),
    ]
    table_gap = _no_exec_table_gap(
        can_execute=can_execute,
        workspace_paths=workspace_paths,
        source_data_paths=source_data_paths,
    )
    if table_gap:
        warnings.append(table_gap[0])
        warning_rows.append(table_gap[1])
    return ContractVerdict(
        ok=True,
        warnings=warnings,
        warning_rows=warning_rows,
    )


def missing_artifacts(patterns: list[str], workspace_paths: list[str]) -> list[str]:
    """Return artifact patterns with no match in ``workspace_paths`` (stable order)."""
    return [p for p in patterns if p and not artifact_present(p, workspace_paths)]


def artifact_present(pattern: str, workspace_paths: list[str]) -> bool:
    """Whether ``pattern`` (exact path / directory / glob) hits any workspace path."""
    return bool(matching_artifact_paths(pattern, workspace_paths))


def _normalize_artifact_relpath(path: str) -> str:
    """Workspace-relative POSIX form for artifact pattern / index comparison.

    Models often declare sandbox absolutes (``/workspace/index.html``) while
    ``index_files`` / successful writes expose relative paths (``index.html``).
    A bare ``lstrip("./")`` turns ``/workspace/…`` into ``workspace/…``, which
    never equals the relative index entry — false「未落盘」. Strip the root
    label first (same primitive as file-tool path rescue), then drop ``./``.
    """
    raw = path.replace("\\", "/").strip()
    if not raw:
        return ""
    stripped = strip_root_label_prefix(raw, _ARTIFACT_ROOT_LABEL)
    # Bare ``/workspace`` → ``.``; treat as empty (no matchable file path).
    if stripped in (".", ""):
        return ""
    return stripped.lstrip("./")


def matching_artifact_paths(pattern: str, workspace_paths: list[str]) -> list[str]:
    """Workspace paths matching ``pattern`` (exact / directory prefix / glob), stable order."""
    pat = _normalize_artifact_relpath(pattern)
    if not pat:
        return []
    normalized = [_normalize_artifact_relpath(p) for p in workspace_paths if p]
    hits: list[str] = []
    for p in normalized:
        if not p:
            continue
        if pat.endswith("/"):
            prefix = pat
            bare = pat.rstrip("/")
            if p == bare or p.startswith(prefix):
                hits.append(p)
        elif any(ch in pat for ch in "*?["):
            if fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(p.rsplit("/", 1)[-1], pat):
                hits.append(p)
        elif p == pat or p.endswith("/" + pat):
            hits.append(p)
        elif not pat.endswith("/") and not any(ch in pat for ch in "*?["):
            from agentcore.workspace._paths import sanitize_write_relpath

            if p == sanitize_write_relpath(pat):
                hits.append(p)
    return hits


# Shared marker for zero-landing soft tips (contract warnings / delivery projection).
# 甲⁺：零落盘已降为 soft warning，不再进 failures，故不再驱动 write_pass。
_ZERO_FILES_GAP_MARKER = "未把产物写入工作区"


def is_zero_files_gap(verdict: ContractVerdict) -> bool:
    """True when failures still carry a hard zero-disk gap (legacy / residual).

    甲⁺：``check_contract`` puts zero-landing into ``warnings`` only, so this
    returns False for current verdicts and write_pass is not triggered.
    """
    if verdict.ok or not verdict.failures:
        return False
    return any(_ZERO_FILES_GAP_MARKER in str(f) for f in verdict.failures)


def format_write_pass_feedback(verdict: ContractVerdict) -> str:
    """Correction prompt for one short write-to-disk pass (no re-investigation)."""
    items = "\n".join(f"- {f}" for f in (verdict.failures or []))
    return (
        "你尚未把产物写入工作区。本轮是【短写盘 pass】——工具面已收窄为写盘/handoff："
        f"\n{items}\n\n"
        "请立即用 write / edit（或等价落盘）把产物写进工作区，"
        "然后调用 handoff。"
        "禁止重新调查、禁止全仓巡读、禁止只把内容贴在回复正文里。"
    )


def has_salvageable_half_product(
    content: str,
    files_touched: list[str] | None,
    debrief: dict[str, Any] | None = None,
) -> bool:
    """True when there is half-finished work worth summarizing / salvage.

    Empty body ∧ zero disk ∧ no qualified brief → not salvageable (skip empty
    ``degraded_synth`` / meaningless finalize LLM).
    """
    if (content or "").strip():
        return True
    if files_touched:
        return True
    return debrief_meets_minimum(debrief)


def transcript_has_tool_inventory(messages: list[Any] | tuple[Any, ...] | None) -> bool:
    """True when the transcript holds at least one successful non-empty tool result.

    Used by force-finalize: investigation-only workers may have zero prose / disk /
    brief yet still have readable tool inventory worth one salvage LLM round.
    Failed tool messages (``<!--agentcore:tool_failed-->`` trailer) do **not** count —
    otherwise unproductive all-fail loops would burn a useless salvage call.
    Does **not** widen :func:`has_salvageable_half_product` (keeps degraded_synth
    from minting empty briefs off tool chatter alone).
    """
    if not messages:
        return False
    from agentcore.runtime.engine.tool_exec import TOOL_FAILED_MARKER

    for msg in messages:
        if getattr(msg, "role", None) != "tool":
            continue
        content = getattr(msg, "content", None)
        text = str(content) if content is not None else ""
        if not text.strip():
            continue
        if TOOL_FAILED_MARKER in text:
            continue
        return True
    return False


def should_attempt_force_finalize_salvage(
    content: str,
    files_touched: list[str] | None,
    debrief: dict[str, Any] | None = None,
    *,
    messages: list[Any] | tuple[Any, ...] | None = None,
) -> bool:
    """Whether force_finalize should spend an LLM salvage round.

    Half-product (body / disk / brief) **or** non-empty tool inventory in
    ``messages``. Empty everything → skip (``force_finalize_skipped_empty``).
    """
    if has_salvageable_half_product(content, files_touched, debrief):
        return True
    return transcript_has_tool_inventory(messages)


def format_feedback(verdict: ContractVerdict) -> str:
    """Render a verdict's failures as a correction instruction for the retry.

    This is the worker's single rework shot, so it's told to spend it on the
    product itself — emit the complete corrected output, no meta-commentary —
    rather than burning the turn on an apology or an explanation.

    Soft ``warnings`` are appended when present so the retry prompt also carries
    reminders; warnings alone never produce a retry instruction.
    """
    issues = [*verdict.failures, *verdict.soft_failures, *verdict.visual_failures]
    if verdict.ok or not issues:
        return ""
    items = "\n".join(f"- {f}" for f in issues)
    soft = ""
    if verdict.warnings:
        soft_items = "\n".join(f"- {w}" for w in verdict.warnings)
        soft = f"\n另有未阻断提醒（请一并处置或交接说明）：\n{soft_items}"
    return (
        f"你上一次的产出未达到以下要求：\n{items}{soft}\n"
        "请直接输出修正后的【完整最终产出】（补齐上述差距，其余内容保持原样），"
        "不要解释、不要道歉、不要附带任何说明文字。"
    )


def format_interrupted_pass_note() -> str:
    """Prefix for a retry whose previous pass died on an LLM transport failure.

    Without it the worker reads a bare 「产出为空」 as its own authoring failure —
    观测到的真实回归: 一次断流后 worker 的 reasoning 变成「上一轮我写空了」，于是它
    不重写正文、只是又调了一次 handoff，白烧一轮。The pass's
    finish reason (ERROR / DEGRADED) is the executor's only reliable signal that
    the round never came back, so it — not the verdict — decides this note.
    """
    return (
        "[系统提示] 上一轮的模型响应在传输中被中断（网络 / 上游断流），"
        "系统只收到片段甚至完全没收到正文——下面的契约判定是基于这份残缺产出。"
    )


def format_soft_reminders(verdict: ContractVerdict) -> str:
    """Render soft contract warnings as a handoff-style reminder (never a hard fail).

    Empty when there are no ``warnings``. Safe to attach to debrief / CEO notes without
    flipping acceptance.
    """
    if not verdict.warnings:
        return ""
    items = "\n".join(f"- {w}" for w in verdict.warnings)
    return (
        f"契约软提醒（未阻断验收，请 worker / CEO 确认处置）：\n{items}"
    )


# Node contract: which channel is the product. Identity is only ``<身份>``.
# HOW / handoff topology / write-tool caution live on tools, consult, or the model.
def describe_deliverable(deliverable: Deliverable | None) -> str:
    """This node's contract for the worker opening: instance facts only.

    Paths / a declared directory render when present. No HOW line for
    write-vs-chat. ``None`` / no instance facts → empty (omit the channel).
    """
    if deliverable is None:
        return ""
    lines: list[str] = []
    dir_norm = (deliverable.artifact_dir or "").replace("\\", "/").rstrip("/")
    if dir_norm:
        lines.append(f"- 落点目录：`{dir_norm}/`")
    if deliverable.artifacts:
        dir_prefix = f"{dir_norm}/" if dir_norm else ""
        listed_paths = [
            p
            for p in deliverable.artifacts
            if p
            and (
                not dir_prefix
                or (
                    p.replace("\\", "/").rstrip("/") + "/" != dir_prefix
                    and p.replace("\\", "/") != dir_norm
                )
            )
        ]
        if listed_paths:
            listed = "、".join(f"`{p}`" for p in listed_paths)
            lines.append(f"- 交付路径：{listed}")
        elif not dir_norm:
            listed = "、".join(f"`{p}`" for p in deliverable.artifacts if p)
            if listed:
                lines.append(f"- 交付路径：{listed}")
    return "\n".join(lines)


def _has_product_signal(
    text: str,
    files_written: int,
    debrief: dict[str, Any] | None,
) -> bool:
    """Whether the run has any non-empty product channel (body / disk / handoff)."""
    return bool(text or files_written > 0 or debrief is not None)


def debrief_meets_minimum(debrief: dict[str, Any] | None) -> bool:
    """True when a handoff brief has a non-empty ``summary``.

    Historical ``key_points`` do not substitute — new rounds write the whole
    note into ``summary``. Short notes count; empty does not.
    """
    if not debrief:
        return False
    return bool(str(debrief.get("summary") or "").strip())


def worker_expects_handoff(plan: Any, run_id: str) -> bool:
    """Whether this node must submit a minimum-quality handoff brief.

    One sentence: has a next hop → must; last hop → skip (incremental only,
    never gated). CEO already sees the leaf body or landed paths.
    """
    return node_has_dependents(plan, run_id)


def handoff_expectation_met(debrief: dict[str, Any] | None) -> bool:
    """Whether an expected (downstream) brief meets the information floor."""
    return debrief_meets_minimum(debrief)


def format_handoff_feedback() -> str:
    """Correction instruction that forces one handoff.

    Only issued when the node has dependents and still has no brief — leaves
    are not gated. A present (even short) note is accepted as-is.
    """
    return (
        "你有下游队员依赖本次交接，但尚未调用 handoff。"
        "请在收尾轮正文写一句话结论（现在什么已成立），"
        "并写清下一棒要接的路径/数字/决定，"
        "再调用 handoff。调用即收尾。"
    )


def synthesize_debrief(
    content: str,
    files_touched: list[str],
) -> dict[str, Any]:
    """Engine-built degraded debrief when a required handoff is still missing.

    Marked ``degraded=True`` so CEO / downstream know it is a fallback, not author intent.
    """
    parts: list[str] = []
    prose = content.strip()
    if prose:
        parts.append(prose[:200])
    if files_touched:
        parts.append("已落盘：" + "、".join(files_touched[:8]))
    summary = "；".join(parts) or "（引擎降级合成：无正文与落盘记录）"
    key_points = [f"文件：{p}" for p in files_touched[:4]] if files_touched else []
    out: dict[str, Any] = {"summary": summary, "degraded": True}
    if key_points:
        out["key_points"] = key_points
    return out


def node_has_dependents(plan: Any, run_id: str) -> bool:
    """True when any plan node lists ``run_id`` in its ``depends_on``."""
    nodes = getattr(plan, "nodes", None) or []
    return any(run_id in (getattr(n, "depends_on", None) or []) for n in nodes)
