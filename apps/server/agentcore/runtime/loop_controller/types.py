"""Shared types / constants for loop convergence governance.

Split from ``loop_controller`` — pure move. ``LANDING_TOOLS`` (写盘的笔) is re-exported
from its single declaration in ``agentcore.tools.file_products``; governance keys on it
only where there is no result to read (idle exemption / breaker / steer copy).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agentcore.tools.file_products import LANDING_TOOLS

DEFAULT_WINDOW = 8
DEFAULT_THRESHOLD = 3
# Consecutive empty-response rounds that trip a degraded finish (B2). The fallback
# retry sits inside this streak, so the default 2 = one empty → fallback retry → if
# still empty, degraded.
DEFAULT_EMPTY_THRESHOLD = 2
# Tool failure circuit breaker (B2): cumulative (run-scoped, args-agnostic) failure
# counts per tool. At the warn threshold the model is told to stop retrying that
# tool; at the disable threshold the tool is removed from the toolset for the rest
# of the run. Unlike REPEATED_FAILURE detection (which keys on the exact call
# fingerprint within the sliding window), this counts a tool failing *any* way and
# never resets — it catches "this tool just isn't working out, no matter the args".
DEFAULT_TOOL_FAILURE_WARN = 2
DEFAULT_TOOL_FAILURE_DISABLE = 3
# Idle hang / probe fail across code_execute+test_run: retire both after N hits.
# Disaster-wall forced stops are incomplete results, not this family path.
EXEC_ENV_TIMEOUT_FAMILY = frozenset({"code_execute", "test_run"})
DEFAULT_EXEC_ENV_TIMEOUT_RETIRE = 2
EXEC_ENV_TIMEOUT_RETIRE_STEER = (
    "本机执行环境连续超时（`code_execute` / `test_run`），本回合起停用这两项——"
    "请改静态核验/读文件取证，向协调者如实报告「执行环境不可用、验证未实跑」；"
    "禁止再原样重试跑命令。"
)
# Same-path consecutive classified write rejects → force_segmented early (策略机),
# before the cumulative per-tool disable threshold. Covers prose-append / code
# integrity / severe-shrink hard rejects (contract_failure) that skip the normal
# failure tally.
DEFAULT_PATH_WRITE_REJECT_STREAK = 2
# Validation / contract self-correct: same fingerprint consecutive failures →
# stop that path (steer), tool stays available (not a parallel disable tally).
DEFAULT_VALIDATION_PATH_STREAK = 2
# Error-class diversion (permanent / permission / validation / transient).
ERROR_CLASS_PERMANENT = "permanent"
ERROR_CLASS_PERMISSION = "permission"
ERROR_CLASS_VALIDATION = "validation"
ERROR_CLASS_TRANSIENT = "transient"
_PERMANENT_RETIRE_STEER = (
    "因不可恢复错误已停用——请换路径推进，禁止原样重试该工具。"
)
_VALIDATION_PATH_STOP_STEER = (
    "同因参数/契约错误已连续出现：请停止原样重试该调用路径，"
    "修正参数或换策略后再试；工具保持可用。"
)
# Landing tools that echoed a landed status / cleared stub back as write args.
_LANDED_SUMMARY_ECHO_STOP_STEER = (
    "同因把请求窗里的已落盘状态/清理占位当写盘参数："
    "下一拍必须先 file_read 该 path 取盘上真文，"
    "再 str_replace（优先）或按真文重填 content/new_string；"
    "禁止再次原样重发该只读状态条；工具保持可用。"
    "若同 path 再原样回灌将早停。"
)
# Consecutive *unproductive* rounds that trip an early stop (B2 无产出早停). An
# unproductive round = the model called ≥1 tool, every call FAILED, and it produced
# no content — it is "working" but getting nowhere. Distinct from an empty round
# (no tool call at all → degraded ladder).
DEFAULT_UNPRODUCTIVE_THRESHOLD = 3
# Progress tools that reset same-target investigation spin when a recent round
# succeeded (stage advance / delivery / handoff / ask). ``str_replace`` /
# ``write_section`` count: coding repair lands via patch, not only whole-file write.
# (Periodic B2 进度复盘 inject was retired — soft cadence had little effect and
# false-nagged interactive browser runs.)
PROGRESS_TOOLS = frozenset(
    {
        "delegate",
        "file_write",
        "file_append",
        "str_replace",
        "write_section",
        "handoff",
        "ask_user",
    }
)
# ``LANDING_TOOLS`` (imported above) = workspace landing tools: success clears
# delivery-idle thrashing; any attempt is "落盘意图" and exempts that round from the
# delivery-idle clock.
# Write tools that enter force_segmented when same-path reject streak trips
# (keep str_replace / write_section as the preferred segmented pens).
PATH_SEGMENT_FORCE_TOOLS = frozenset({"file_write", "file_append"})
# Dangerous landing action narrowed (disabled) once force_segmented latches —
# keep file_write / str_replace; stop append thrashing on prose / broken bodies.
FORCE_SEGMENTED_NARROW_TOOLS = frozenset({"file_append"})
# CEO orchestration primitives: parse-only thrashing must not retire them
# (same posture as LANDING_TOOLS keeping the pen — keep the dispatcher).
ORCHESTRATION_TOOLS = frozenset({"delegate", "ask_user"})
# Memory tools: parse-only thrashing must not retire them (same keep posture as
# ORCHESTRATION_TOOLS — independent set; do NOT fold into ORCHESTRATION_TOOLS).
MEMORY_TOOLS = frozenset({"remember"})


def classify_segmented_write_reject(
    tool_name: str,
    *,
    error: str = "",
    contract_failure: bool = False,
) -> str | None:
    """Classify a hard write reject that feeds the same-path force_segmented streak.

    Returns a stable class id (``prose_append`` / ``code_integrity`` /
    ``severe_shrink``) or ``None``.
    Does **not** cover length/oversized rejects (those hard gates were removed).
    Soft ``integrity_nudge`` is success-path only and never reaches here.
    """
    if not contract_failure or tool_name not in {"file_write", "file_append"}:
        return None
    text = error or ""
    if tool_name == "file_append" and "已落成篇正文" in text:
        return "prose_append"
    if tool_name == "file_write" and "拒绝整篇截断覆盖" in text:
        return "severe_shrink"
    if "结构不完整" in text or "省略标记" in text:
        return "code_integrity"
    return None


def _collapse_malformed_required_args(name: str, parsed: dict[str, object]) -> dict[str, object]:
    """Collapse empty-required-field / no-op edit calls so stuck detection sees one path.

    Distinct ``path`` / ``new_string`` with empty ``old_string`` must not mint a new
    fingerprint each time — that let workers burn token budgets on free validation
    retries. Non-empty identical ``old_string``/``new_string`` collapses per path
    (longdoc revise thrash: different noop payloads still melt). Sentinel shape is
    stable and intentional (not a real tool schema).

    Landed-summary / cleared-stub echo (same surface as
    ``is_cleared_write_stub_args``) collapses per path for write pens so different
    summary texts still trip validation path-stop.

    ``write_section`` invalid ``section`` (e.g. ``ch5-s0``) collapses per path so
    format thrash enters the same validation early-stop (08-08 定案①).
    """
    if name in {"file_write", "file_append", "str_replace"}:
        from agentcore.runtime.engine.write_args_clear import is_cleared_write_stub_args

        if is_cleared_write_stub_args(parsed):
            path = parsed.get("path")
            path_key = path.strip().replace("\\", "/") if isinstance(path, str) else ""
            return {"__malformed__": "landed_summary_echo", "path": path_key}
    if name == "str_replace":
        old = parsed.get("old_string")
        if old is None or (isinstance(old, str) and not old.strip()):
            return {"__malformed__": "old_string"}
        path = parsed.get("path")
        if path is None or (isinstance(path, str) and not path.strip()):
            return {"__malformed__": "path"}
        new = parsed.get("new_string")
        if (
            isinstance(old, str)
            and isinstance(new, str)
            and old == new
        ):
            path_key = path.strip().replace("\\", "/") if isinstance(path, str) else ""
            return {"__malformed__": "identical_edit", "path": path_key}
    if name in {"file_write", "file_append"}:
        path = parsed.get("path")
        if path is None or (isinstance(path, str) and not path.strip()):
            return {"__malformed__": "path"}
    if name == "write_section":
        from agentcore.runtime.runs.website_section import is_valid_section_id

        path = parsed.get("path")
        path_key = path.strip().replace("\\", "/") if isinstance(path, str) else ""
        section = parsed.get("section")
        if section is None or (
            isinstance(section, str) and not is_valid_section_id(section)
        ):
            return {"__malformed__": "section", "path": path_key}
    return parsed


def _norm_write_reject_path(path: object) -> str:
    if not isinstance(path, str):
        return ""
    return path.strip().replace("\\", "/")


def delivery_idle_nudge_prompt(
    *,
    rounds: int,
    recon: bool = False,
    report: bool = False,
    channel_dead: bool = False,
) -> str:
    """Soft steer for read-idle.

    Factory only arms the ``recon`` branch (conclude/handoff). Files/report copy
    remains for explicit LoopController construction; product delivery_idle is
    retired. ``channel_dead``: workspace write path is sticky-unavailable — never
    urge ``file_write`` / ``str_replace`` (complements Phase 1 tool retire).
    """
    if recon:
        return (
            f"[系统提示] 调查空转提醒（已连续 {rounds} 轮仅搜读、无结论交接）："
            "请立即基于已读内容给出结论，或 escalate / handoff 说明阻塞；"
            "禁止继续换文件通读摊大饼。不要为「再确认」再开一轮全仓 typecheck。"
        )
    if channel_dead:
        return (
            f"[系统提示] 交文件空转提醒（已连续 {rounds} 轮仅调查、零落盘）："
            "工作区写盘通道已不可用。请立即 handoff / escalate 说明阻塞与已读结论；"
            "禁止继续只搜不交，勿再尝试落盘。"
        )
    if report:
        return (
            f"[系统提示] 交文件空转提醒（已连续 {rounds} 轮仅调查、零落盘）："
            "任务要求写报告落盘。请立即基于已读证据 file_write 写出报告，"
            "或 handoff 交接阻塞；禁止继续只搜不写。"
            "检索工具仍可用，请转入成稿。"
        )
    return (
        f"[系统提示] 交文件空转提醒（已连续 {rounds} 轮仅调查、零落盘）："
        "任务要求写盘交付。请立即 str_replace / file_write 落地改动，或 handoff 交接阻塞；"
        "禁止继续大范围搜读空转。仍不落地将收窄调查类工具。"
    )


def delivery_idle_narrow_prompt(
    *, rounds: int, channel_dead: bool = False
) -> str | None:
    """After soft nudge: tools narrowed — still not FINALIZE.

    Factory never arms this for files_expected. Explicit construction may still
    set ``narrow_rounds``.

    ``channel_dead`` → ``None`` (caller must skip): narrow copy keeps write tools
    and would push落盘 after the channel is already sticky-dead.
    """
    if channel_dead:
        return None
    return (
        f"[系统提示] 交文件空转收窄（已连续 {rounds} 轮仅调查、零落盘）："
        "大范围调查类工具已收回；仅保留写盘 / 内环诊断 / handoff / 必要 file_read。"
        "请立即改文件或交接，勿再展开新调研。"
    )


class StuckReason(StrEnum):
    """Which mechanical loop pattern was observed."""

    REPEATED_CALL = "repeated_call"
    ALTERNATING = "alternating"
    REPEATED_FAILURE = "repeated_failure"


class Intervention(StrEnum):
    """What the engine should do this round."""

    CONTINUE = "continue"
    NUDGE = "nudge"
    FINALIZE = "finalize"


@dataclass(frozen=True)
class ToolAttempt:
    """One executed tool call in a round; ``success`` carries the failure signal."""

    fingerprint: str
    tool_name: str
    success: bool
    # Policy/environment/governance blocks (SSRF, egress breaker, approval denial) are
    # honest tool failures for the model but must not trip the run-scoped circuit
    # breaker — the tool itself is fine; the call was refused upstream.
    policy_failure: bool = False
    # Arguments string failed ``json.loads`` before the tool ran — still counts toward
    # the run-scoped breaker, but steers must not say「换不同的输入」(that pushes the
    # model to shorten/rewrite a DAG that only needed quote-escaping).
    parse_failure: bool = False
    # 参数契约拒绝: a deterministic argument/environment rejection (e.g. web_search A3
    # query 过长/过多, or file_ops path-not-found) whose error already tells the model
    # exactly how to fix it. Like ``policy_failure`` it is invisible to the run-scoped
    # circuit breaker — a same-round fan-out of over-long queries / missing paths must
    # not burn the disable threshold before the model can act on the fix tip — but unlike
    # it, this names a self-correctable参数打回, not an upstream block. It still lands in
    # the sliding window as an honest failure, so REPEATED_FAILURE detection, unproductive
    # early-stop, and round recording are unchanged; only the cumulative warn/disable
    # tally (``_tool_failures``) skips it.
    contract_failure: bool = False
    # Short error text for honest finalize / CEO synthesis (ignored on success /
    # policy_failure). Capped when recorded on the controller.
    error_summary: str = ""
    # Optional tool-result metadata forwarded for governance (e.g. delegate batch shape).
    meta: dict[str, Any] = field(default_factory=dict)


def resolve_error_class(attempt: ToolAttempt) -> str | None:
    """Classify a failed attempt for breaker diversion (or ``None`` on success).

    Prefer explicit ``meta.error_class``; else infer from existing markers
    (``retire_tools`` / ``liveness_timeout`` / ``policy_failure`` /
    ``contract_failure`` / ``parse_failure``). Unknown failures stay transient.
    """
    if attempt.success:
        return None
    meta = attempt.meta or {}
    raw = meta.get("error_class")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if meta.get("retire_tools") or meta.get("liveness_timeout"):
        return ERROR_CLASS_PERMANENT
    if attempt.policy_failure:
        return ERROR_CLASS_PERMISSION
    if attempt.contract_failure or attempt.parse_failure:
        return ERROR_CLASS_VALIDATION
    return ERROR_CLASS_TRANSIENT


def is_exec_env_timeout(attempt: ToolAttempt) -> bool:
    """True when ``code_execute`` / ``test_run`` hit idle hang or probe fail.

    Disaster-wall forced stops are incomplete results, not exec-env hangs.
    """
    if attempt.success or attempt.tool_name not in EXEC_ENV_TIMEOUT_FAMILY:
        return False
    meta = attempt.meta or {}
    if meta.get("exec_env_timeout"):
        return True
    if meta.get("timeout_kind") == "idle":
        return True
    code = meta.get("code")
    if code in ("exec_timeout",):
        return True
    err = attempt.error_summary or ""
    if "ExecEnvProbeFailed:" in err:
        return True
    if "Timeout: no output for" in err:
        return True
    # Legacy journals: verify_budget code / old wall-clock wording.
    if code == "verify_budget":
        return True
    if "Timeout: execution exceeded" in err:
        return True
    return "验证未在" in err and "预算内完成" in err


@dataclass(frozen=True)
class StuckSignal:
    """A detected stuck pattern plus the facts needed to anchor a nudge."""

    reason: StuckReason
    tool_name: str
    count: int

    def reflection_message(self) -> str:
        """Steer message anchored to the concrete observation.

        Anchoring to the real fact ("you called X 3 times") rather than a vague
        "think harder" is what keeps the injected reflection from diverging.
        """
        if self.reason is StuckReason.REPEATED_FAILURE:
            return (
                f"[系统提示] 工具 `{self.tool_name}` 已用相同方式失败 {self.count} 次，"
                "继续重试只会再次失败。请不要再以相同参数调用它："
                "改用不同的输入、换一个工具，或基于已有信息直接给出最终答案。"
            )
        if self.reason is StuckReason.ALTERNATING:
            return (
                f"[系统提示] 你在两个动作之间来回循环（其中之一是 `{self.tool_name}`）"
                "却没有取得进展。请跳出循环：选定一个能真正推进到答案的具体下一步，"
                "或现在就给出最终答案。"
            )
        return (
            f"[系统提示] 你已用相同参数调用 `{self.tool_name}` {self.count} 次，"
            "没有任何新进展。请停止重复：要么换一种实质不同的做法或参数，"
            "要么基于现有信息直接给出最终答案。"
        )


@dataclass(frozen=True)
class CircuitBreak:
    """Tools that crossed a cumulative-failure threshold this round (B2 熔断).

    ``warned`` hit the warn threshold (tell the model to stop retrying them);
    ``disabled`` hit the disable threshold (the engine removes them from the
    toolset for the rest of the run). Each is a tuple of tool names; both empty
    means nothing tripped this round.

    ``parse_only`` names tools whose failures so far are *all* argument-JSON parse
    failures — their steer text must guide format/strategy, never「换不同的输入」.

    ``force_segmented`` names write/landing tools that hit the disable threshold
    *or* the same-path classified write-reject streak, but stay enabled — steer
    forces skeleton + section writes instead of retiring the pen（长文落盘定案：
    失败换分段，不关写文件）. ``apply_circuit_breaker`` may still narrow
    ``file_append`` out of the live toolset while keeping ``file_write`` /
    ``str_replace``.

    ``retire_message`` is an optional hard-stop steer (e.g. browser egress
    unavailable) that replaces the generic「已多次失败」disable copy when set.

    ``liveness_warned`` names tools whose latest counted failure was a hang /
    no-response timeout (活性挂起) — warn steer forbids identical retry.

    ``validation_stop`` is a one-shot steer when the same validation fingerprint
    first hits the path-stop streak (tool stays available). A later re-hit of an
    already-stopped fingerprint latches thrashing / mid-loop hard stop instead of
    another steer (see :meth:`LoopController.take_validation_hard_stop`).
    """

    warned: tuple[str, ...] = ()
    disabled: tuple[str, ...] = ()
    parse_only: frozenset[str] = frozenset()
    force_segmented: frozenset[str] = frozenset()
    retire_message: str | None = None
    liveness_warned: frozenset[str] = frozenset()
    validation_stop: str | None = None

    def __bool__(self) -> bool:
        return bool(
            self.warned
            or self.disabled
            or self.force_segmented
            or self.validation_stop
        )

    def message(self) -> str | None:
        """The single ``[系统提示]`` to inject this round, or ``None``.

        Anchored to the concrete fact (which tool, what now happens) like the
        nudge messages — disable first (the stronger action), then force-segmented
        write steer, then warn. Parse-only write failures steer to segmented
        landing (not「原样重发」). ``read_url`` disable/warn uses a research-specific
        stop-read steer (do not say「换不同的输入」— that encourages URL thrashing
        after egress storms).
        """
        parts: list[str] = []
        if self.disabled:
            if self.retire_message:
                parts.append(self.retire_message.strip())
            else:
                parse_d = tuple(n for n in self.disabled if n in self.parse_only)
                other_d = tuple(n for n in self.disabled if n not in self.parse_only)
                read_d = tuple(n for n in other_d if n == "read_url")
                other_d = tuple(n for n in other_d if n != "read_url")
                if read_d:
                    from agentcore.tools.builtin.web._net import READ_URL_RETIRE_STEER

                    parts.append(READ_URL_RETIRE_STEER)
                if other_d:
                    names = "、".join(f"`{n}`" for n in other_d)
                    parts.append(
                        f"工具 {names} 已多次失败，本回合起停用，无法再调用——"
                        "请改用其他工具或基于已有信息推进。"
                    )
                if parse_d:
                    names = "、".join(f"`{n}`" for n in parse_d)
                    parts.append(
                        f"工具 {names} 因参数不是合法 JSON 已多次失败，本回合起停用，无法再调用——"
                        "请改用其他工具或基于已有信息推进。"
                    )
        if self.force_segmented:
            names = "、".join(f"`{n}`" for n in self.force_segmented)
            parts.append(
                f"工具 {names} 连续写盘失败：写文件能力保持可用（`file_write` / `str_replace`）。"
                "【强制】成篇后用 str_replace 修订；整文件覆盖须完整正文；"
                "若参数过大易失败可改短骨架 + 按节填空；"
                "`file_append` 已收窄；勿向用户讲解 JSON 转义。"
            )
        if self.warned:
            parse_w = tuple(n for n in self.warned if n in self.parse_only)
            other_w = tuple(n for n in self.warned if n not in self.parse_only)
            read_w = tuple(n for n in other_w if n == "read_url")
            other_w = tuple(n for n in other_w if n != "read_url")
            if read_w:
                parts.append(
                    "工具 `read_url` 已多次失败，请不要再换 URL / 同策略空转重读——"
                    "基于已有材料推进写作，或换一个非外网读页工具；"
                    "不要把继续 web_search 当默认出路。"
                )
            if other_w:
                live_w = tuple(n for n in other_w if n in self.liveness_warned)
                plain_w = tuple(n for n in other_w if n not in self.liveness_warned)
                if live_w:
                    names = "、".join(f"`{n}`" for n in live_w)
                    parts.append(
                        f"工具 {names} 已多次活性挂起（无响应超时），请不要原样重试："
                        "缩小范围、换路径策略或换工具，基于已有信息推进。"
                    )
                if plain_w:
                    names = "、".join(f"`{n}`" for n in plain_w)
                    parts.append(
                        f"工具 {names} 已多次失败，请不要再以相同方式调用它："
                        "换不同的输入、换一个工具，或基于已有信息直接推进。"
                    )
            if parse_w:
                write_pw = tuple(n for n in parse_w if n in LANDING_TOOLS)
                orch_pw = tuple(n for n in parse_w if n in ORCHESTRATION_TOOLS)
                memory_pw = tuple(n for n in parse_w if n in MEMORY_TOOLS)
                other_pw = tuple(
                    n
                    for n in parse_w
                    if n not in LANDING_TOOLS
                    and n not in ORCHESTRATION_TOOLS
                    and n not in MEMORY_TOOLS
                )
                if write_pw:
                    names = "、".join(f"`{n}`" for n in write_pw)
                    parts.append(
                        f"工具 {names} 的调用参数不是合法 JSON，已多次解析失败"
                        "（常见于整篇正文塞进一次调用导致截断）："
                        "【强制】可一次完整 file_write（须完整正文）或短骨架 + 分段 "
                        "file_append / str_replace；不要原样重发整段；成篇后修订用 "
                        "str_replace。"
                    )
                if orch_pw:
                    names = "、".join(f"`{n}`" for n in orch_pw)
                    parts.append(
                        f"工具 {names} 的调用参数不是合法 JSON，已多次解析失败："
                        "【强制】只发单一合法 JSON（禁止 XML/<parameter> 混入），"
                        "按 schema 精简重试；工具保持可用，勿改用空回复交差。"
                    )
                if memory_pw:
                    names = "、".join(f"`{n}`" for n in memory_pw)
                    parts.append(
                        f"工具 {names} 的调用参数不是合法 JSON，已多次解析失败："
                        "【强制】记规则时若因截断则完整一句重发或分次写入；"
                        "若因引号/转义错误则只修好转义后重试；"
                        "禁止截断时原样重发全部。工具保持可用。"
                    )
                if other_pw:
                    names = "、".join(f"`{n}`" for n in other_pw)
                    parts.append(
                        f"工具 {names} 的调用参数不是合法 JSON，已多次解析失败："
                        "若因截断则缩短或分次后重发完整合法 JSON；"
                        "若因引号/转义错误则修好转义后重发；"
                        "截断场景禁止原样重发全部。也可换一个工具或基于已有信息直接推进。"
                    )
        if self.validation_stop:
            parts.append(self.validation_stop.strip())
        if not parts:
            return None
        return "[系统提示] " + " ".join(parts)


def fingerprint_tool_call(name: str, arguments: str) -> str:
    """Stable hash of ``(tool_name, normalized args)``.

    Args are normalized via key-sorted JSON so semantically identical calls map
    to one fingerprint; malformed JSON falls back to the raw argument string so
    verbatim repeats are still caught. Empty required fields and identical
    str_replace no-ops collapse to a stable sentinel (see
    ``_collapse_malformed_required_args``).
    """
    try:
        parsed = json.loads(arguments) if arguments else {}
        if isinstance(parsed, dict):
            parsed = _collapse_malformed_required_args(name, parsed)
        normalized = json.dumps(parsed, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (json.JSONDecodeError, TypeError):
        normalized = arguments or ""
    return hashlib.sha1(f"{name}\x00{normalized}".encode()).hexdigest()
