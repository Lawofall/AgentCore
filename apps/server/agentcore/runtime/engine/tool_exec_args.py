"""Tool-call args sanitize / miss feedback / failure markers for one ReAct round."""

from __future__ import annotations

import json
import time
from typing import Any, Literal

from agentcore.core.error_codes import ErrorCode
from agentcore.core.logging import get_logger
from agentcore.core.secrets import redact_secrets
from agentcore.core.text import clip_preview
from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.events import EventSink, tool_use_end
from agentcore.runtime.loop_controller import (
    ERROR_CLASS_PERMANENT,
    EXEC_RUN_TOOL_NAMES,
    ToolAttempt,
    classify_segmented_write_reject,
)
from agentcore.tools.file_products import LANDING_TOOLS
from agentcore.tools.registry import ToolRegistry

from .tool_failure_face import tool_failure_fields

logger = get_logger(__name__)

# Marker in tool_use_start.arguments when JSON parse failed — must not look like a
# successfully parsed empty object ``{}`` (journal / UI 假象).
_ARGS_PARSE_FAILED_MARKER: dict[str, Any] = {"__args_parse_failed__": True}

# v1 taxonomy after ``sanitize_raw_tool_arguments`` (no ``residue`` — sanitize owns that).
ArgsParseClass = Literal["truncated", "escape", "other"]

# Orchestration tools: anti-wrap / anti-XML tip (keep regardless of class).
_ORCH_PARSE_TOOLS = frozenset({"delegate", "ask_user"})

# User-visible process-line copy for write-tool args parse failures (人话).
_USER_WRITE_PARSE_MSG = "长文保存失败，改成分段写入继续。"

# 工具失败机器尾注 (落盘失败归因 · 消费方见 runtime/runs/serialize.py):
# LLMMessage 无独立 success 字段；失败/拒绝路径在 tool content 末追加此 marker，让
# landing_write_failure_kind 能按 tool_call_id 关联出「写盘尝试失败」而非零尝试。
# 与产物自报尾注（tools/file_products.py）同构：producer 在此、consumer 在 serialize，
# 格式靠 round-trip 单测锁死。禁止用拒绝文案子串匹配。
TOOL_FAILED_MARKER = "<!--agentcore:tool_failed-->"

# Aggregable tip length for ``tool.execute_end`` reason (status=error).
_TOOL_ERROR_REASON_MAX = 200

# terminal / host(action=shell) 观测：命令可能含 token，先 redact 再 clip。
_SHELL_OBSERVE_TOOLS = frozenset({"run", "host"})
_SHELL_COMMAND_PREVIEW_MAX = 160
_SHELL_CWD_PREVIEW_MAX = 80
_URL_OBSERVE_TOOLS = frozenset({"read_url", "download_url"})
_URL_PREVIEW_MAX = 200


def _attempt_meta_with_landing_path(
    name: str,
    args: Any,
    base: dict[str, Any] | None = None,
    *,
    error: str = "",
    contract_failure: bool = False,
) -> dict[str, Any]:
    """Forward landing-tool path (+ write-reject class) into ``ToolAttempt.meta``."""
    from agentcore.runtime.runs.landing_product import landing_tool_path_from_args

    meta: dict[str, Any] = dict(base or {})
    path = landing_tool_path_from_args(name, args if isinstance(args, dict) else None)
    if path:
        meta["path"] = path
    reject_class = classify_segmented_write_reject(
        name, error=error, contract_failure=contract_failure
    )
    if reject_class:
        meta["segmented_write_reject"] = reject_class
    # Permanent liveness: first-fail retire of this tool (loop_controller).
    # ``run`` 族只记失败，不卸工具。
    if (
        meta.get("liveness_timeout")
        and "retire_tools" not in meta
        and name
        and name not in EXEC_RUN_TOOL_NAMES
    ):
        meta["error_class"] = ERROR_CLASS_PERMANENT
        meta["retire_tools"] = [name]
        if not meta.get("retire_message"):
            meta["retire_message"] = f"工具 `{name}` 因活性挂起已停用——请换路径推进，禁止原样重试。"
    return meta


def _short_tool_error_reason(text: str, *, limit: int = _TOOL_ERROR_REASON_MAX) -> str:
    """Collapse whitespace and truncate for log aggregation (not the full transcript)."""
    collapsed = " ".join((text or "").split())
    if not collapsed:
        return "Unknown error"
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 1)].rstrip() + "…"


def _url_observe_log_fields(name: str, args: Any) -> dict[str, Any]:
    """``url`` / ``host`` for read_url / download_url execute_* logs."""
    if name not in _URL_OBSERVE_TOOLS or not isinstance(args, dict):
        return {}
    raw = args.get("url")
    if not isinstance(raw, str) or not raw.strip():
        return {}
    from agentcore.core.net import site_of

    url = redact_secrets(raw.strip())
    fields: dict[str, Any] = {}
    preview = clip_preview(url, _URL_PREVIEW_MAX)
    if preview:
        fields["url"] = preview
    host = site_of(url)
    if host:
        fields["host"] = host
    return fields


def _shell_observe_log_fields(name: str, args: Any) -> dict[str, Any]:
    """Facts for ``tool.execute_*`` on run / host / URL tools. No write-intent guess.

    CEO 可持 run/host，落盘不进 ``file_products``；查询时靠 preview + action
    由人判断是否写了工作区。命令可能含 token / key，故先 ``redact_secrets`` 再 clip。
    ``read_url`` / ``download_url`` 带 url/host，对照 in_flight 挂起用。
    """
    if not isinstance(args, dict):
        return {}
    fields: dict[str, Any] = {}
    if name in _SHELL_OBSERVE_TOOLS:
        command = args.get("command")
        if isinstance(command, str):
            # Redact BEFORE clipping: clipping first can cut a secret's recognizable
            # prefix and leave the tail in the log.
            preview = clip_preview(redact_secrets(command), _SHELL_COMMAND_PREVIEW_MAX)
            if preview:
                fields["command_preview"] = preview
        action = args.get("action")
        if isinstance(action, str):
            act = action.strip()
            if act:
                fields["action"] = act
        cwd = args.get("cwd")
        if isinstance(cwd, str):
            cwd_preview = clip_preview(cwd, _SHELL_CWD_PREVIEW_MAX)
            if cwd_preview:
                fields["cwd_preview"] = cwd_preview
    fields.update(_url_observe_log_fields(name, args))
    return fields


def with_tool_failed_marker(content: str) -> str:
    """Append the machine failure trailer (idempotent)."""
    body = (content or "").rstrip()
    if TOOL_FAILED_MARKER in body:
        return body
    return f"{body}\n{TOOL_FAILED_MARKER}" if body else TOOL_FAILED_MARKER


def _failed_tool_message(tool_call_id: str, content: str) -> LLMMessage:
    return LLMMessage(
        role="tool", content=with_tool_failed_marker(content), tool_call_id=tool_call_id
    )


def _leaked_cancel_quad(
    *,
    tool_call_id: str,
    name: str,
    args: dict[str, Any],
    fingerprint: str,
    started: float,
    event_run_id: str,
    sink: EventSink,
    error_msg: str,
) -> tuple[LLMMessage, None, ToolAttempt, list[Any]]:
    """Isolate a leaked child CancelledError (not a real Stop) as a failed tool."""
    duration_ms = int((time.monotonic() - started) * 1000)
    sink.emit(
        tool_use_end(
            tool_call_id,
            name,
            success=False,
            output=error_msg,
            failure=tool_failure_fields(code=ErrorCode.TOOL_ERROR),
            run_id=event_run_id,
        )
    )
    logger.warning(
        "tool.execute_end",
        tool=name,
        status="isolated_cancel",
        duration_ms=duration_ms,
        **_shell_observe_log_fields(name, args),
    )
    return (
        _failed_tool_message(tool_call_id, error_msg),
        None,
        ToolAttempt(
            fingerprint,
            name,
            success=False,
            error_summary=error_msg,
            meta=_attempt_meta_with_landing_path(name, args),
        ),
        [],
    )


def _missing_tool_feedback(
    missing: str,
    *,
    raw_name: str | None,
    registry: ToolRegistry,
) -> tuple[str, str, bool]:
    """Build user-facing text + log status + policy flag for a registry miss.

    Known declared tools that are absent from *this* registry are usually audience
    or assembly gates (CEO vs worker, cloud execution withheld) — not typos. Those
    get an actionable message and ``policy_failure`` so the run circuit breaker
    does not burn on repeated role mistakes.
    """
    from agentcore.tools.registration import (
        declared_tool_names,
        execution_class_tool_names,
        worker_only_tool_names,
    )

    worker_only = worker_only_tool_names()
    execution = execution_class_tool_names()
    declared = declared_tool_names()

    if missing in execution:
        return (
            (
                f"工具 '{missing}' 本回合未装配执行类工具（见 `<工作区>` 缺口），"
                "勿空转重试。"
            ),
            "not_assembled",
            True,
        )
    if missing in worker_only:
        return (
            (
                f"工具 '{missing}' 仅供委派 worker 使用，当前工具面不可用。"
                "请用 delegate 派工执行，勿亲自调用该工具。"
            ),
            "audience_deny",
            True,
        )
    if missing in declared:
        return (
            (
                f"工具 '{missing}' 本回合未装配到当前工具面（环境或角色门控）。"
                "请改用已提供的工具，勿空转重试同一名称。"
            ),
            "not_assembled",
            True,
        )

    from agentcore.runtime.resolve.ceo_surface import COORDINATION_GATED_TOOLS

    # 协调闸内工具（至少 wait）：未装配时勿 fuzzy 成 git 等无关工具。
    if missing in COORDINATION_GATED_TOOLS:
        if missing == "wait":
            return (
                (
                    f"工具 '{missing}' 当前未装配到工具面。"
                    "若团队协调已启动：请空响应等待下一批事件，勿改调其他工具占位。"
                ),
                "not_found",
                False,
            )
        return (
            (
                f"工具 '{missing}' 当前未装配到工具面（仅协调期提供）。"
                "请改用已提供的工具，或空响应等待；勿猜测相近工具名。"
            ),
            "not_found",
            False,
        )

    suggestions = registry.suggest_names(missing)
    did_you_mean = f"你是否想用：{' / '.join(suggestions)}？" if suggestions else ""
    if raw_name and raw_name != missing:
        error_msg = (
            f"Tool '{missing}' not found"
            f"（已剥离协议标签残留：{raw_name!r} → {missing!r}）。"
            f"{did_you_mean}"
            "请使用合法工具名（如 web_search）原样重试，勿夹带 XML/协议标签。"
        )
    else:
        error_msg = (
            f"Tool '{missing}' not found。{did_you_mean}请使用合法工具名原样重试，勿夹带协议标签。"
        )
    return error_msg, "not_found", False


def _classify_args_parse_failure(raw: str, exc: json.JSONDecodeError) -> ArgsParseClass:
    """Classify JSON args parse failure after sanitize (v1: truncated | escape | other)."""
    detail = (exc.msg or "").strip()
    pos = exc.pos if isinstance(exc.pos, int) else 0
    # Dominant truncated signals (write-tool heuristic kept as a general length/pos cue).
    if "Unterminated string" in detail or (len(raw) >= 4000 and pos < 200):
        return "truncated"
    # Typical mid-string structural / quote-escape failures (e.g. Expecting ',' delimiter).
    if "Expecting" in detail:
        return "escape"
    return "other"


def _strategy_for_args_parse(tool_name: str, parse_class: ArgsParseClass) -> str:
    """Family + class first-fail tip (model-facing). Landing never teaches user escaping."""
    if tool_name in LANDING_TOOLS:
        trunc_hint = (
            "【信号】输出长度截断导致参数 JSON 未闭合（finish_reason=length 同类）——"
            if parse_class == "truncated"
            else "【策略】这通常是整篇正文塞进一次工具调用导致的转义失败——"
        )
        return (
            trunc_hint + "不要原样重发整段导致再次截断；可一次完整 file_write（须完整正文）"
            "或改为短骨架 + 按节 file_append / str_replace 分段落盘"
            "（每节远小于一次输出上限）；成篇后修订用 str_replace。"
            "勿向用户讲解 JSON 引号转义。"
        )
    if tool_name in _ORCH_PARSE_TOOLS:
        if tool_name == "delegate":
            return (
                "【策略】payload 顶层直接放字段（delegate：`tasks` 或 `playbook`），"
                "禁止再包一层 `arguments` 字符串；参数须为单一合法 JSON 对象，"
                "禁止混入 XML/<parameter>/<object> 等协议标签；"
                "按工具 schema 重发精简参数，勿把整篇正文塞进 task 字段"
                "（细则进 deliverable / team_brief）。"
            )
        return (
            "【策略】参数必须是单一合法 JSON 对象，禁止混入 XML/"
            "<parameter>/<object> 等协议标签；按工具 schema 重发精简参数，"
            "勿把整篇正文塞进参数字段。"
        )
    if tool_name == "remember":
        if parse_class == "truncated":
            return (
                "【信号】输出长度截断导致参数 JSON 未闭合——"
                "请用完整一句规则重发（勿省略号收口）；多条规则请分多次 remember；"
                "禁止原样重发全部半截参数。"
            )
        if parse_class == "escape":
            return "【策略】请修复转义（尤其是 content 字符串内的引号）后重发合法 JSON 参数。"
        return (
            "【策略】请按工具 schema 重发精简合法 JSON；完整一句、勿省略号收口；"
            "多条分次；禁止原样重发全部参数。"
        )
    if parse_class == "truncated":
        return (
            "【信号】输出长度截断导致参数 JSON 未闭合——"
            "不要原样重发全部参数；请缩短单次参数或拆成多次调用后重发合法 JSON。"
        )
    if parse_class == "escape":
        return "请修复转义（尤其是字符串内的引号）后，原样重发全部参数；禁止改写、缩短或删减内容。"
    return "请按工具 schema 修复并重发合法 JSON 参数对象。"


def _format_args_parse_error(
    tool_name: str, raw: str, exc: json.JSONDecodeError
) -> tuple[str, str, ArgsParseClass]:
    """Return ``(model_facing, user_facing, parse_class)`` for illegal tool-call JSON.

    Write/landing tools get a segmented-write steer for the model and a short human
    line for ``tool_use_end.failure`` — never「请修复转义后原样重发」exposed to users.
    Other tools keep the technical tip for both surfaces; strategy text is class-aware.
    """
    parse_class = _classify_args_parse_failure(raw, exc)
    pos = exc.pos if isinstance(exc.pos, int) else 0
    # Window around the failure so the model can spot unescaped quotes without a full dump.
    left = max(0, pos - 24)
    right = min(len(raw), pos + 24)
    snippet = raw[left:right].replace("\n", "\\n").replace("\r", "\\r")
    if left > 0:
        snippet = "…" + snippet
    if right < len(raw):
        snippet = snippet + "…"
    detail = (exc.msg or "JSON decode error").strip()
    technical = (
        f"工具 '{tool_name}' 的参数不是合法 JSON（{detail}；失败位置 {pos}，附近片段：{snippet}）。"
    )
    model_msg = technical + _strategy_for_args_parse(tool_name, parse_class)
    if tool_name in LANDING_TOOLS:
        return model_msg, _USER_WRITE_PARSE_MSG, parse_class
    return model_msg, model_msg, parse_class
