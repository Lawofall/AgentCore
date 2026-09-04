"""Short-exec kernel — inline / short commands via ``ToolContext.backend``.

Not a model-facing Tool. ``RunTool`` (and tests) call :func:`execute_short`.
The backend owns the ``SandboxProvider``; executed code sees the same files
the file tools do.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any, Literal

from agentcore.core.errors import SandboxError
from agentcore.tools.builtin.code_execute_lock import code_execute_lock
from agentcore.tools.builtin.long_running import (
    long_running_command_match,
    long_running_redirect_message,
)
from agentcore.tools.builtin.package_install import permission_allows_restricted_network
from agentcore.tools.builtin.source_inspect import (
    source_inspect_match,
    source_inspect_redirect_message,
)
from agentcore.tools.file_products import file_product
from agentcore.tools.protocol import ToolContext, ToolResult
from agentcore.tools.sandbox.exec_languages import ALL_EXEC_LANGUAGES
from agentcore.tools.sandbox.protocol import ExecutionRequest

# Re-export for existing test imports.
__all__ = [
    "execute_short",
    "long_running_command_match",
    "long_running_redirect_message",
    "source_inspect_match",
    "source_inspect_redirect_message",
]

# 结构化写回自报 (交付物台账事实口径 · 契约见 tools/file_products.py):
# 输出里的「已写回工作区：…」是给模型看的自然语言；产物则在 ``ToolResult.file_products``
# 上自报本次执行 EXACT 的写回路径，让「脚本间接落盘」与 file_write 走同一条台账通道 ——
# 不必解析那行中文散文（脆弱：文件名可能含分隔符「、」、措辞会变、被截断）。
#
# C3 边界：短跑写回本期明确不走 WriteCoordinator 硬拦（可观测即可）；
# file_write / append / str_replace / delete / move 才是互斥闭包。


def _resolved_languages(languages: Sequence[str] | None) -> tuple[str, ...]:
    if languages is None:
        return ALL_EXEC_LANGUAGES
    return tuple(lang for lang in languages if lang in ALL_EXEC_LANGUAGES)


def _make_output_callback(
    context: ToolContext, env: dict[str, str] | None = None
):
    """Forward sandbox output chunks via ``on_progress`` when a live sink is wired."""
    on_progress = context.on_progress
    if on_progress is None:
        return None

    def callback(stream: str, chunk: str) -> None:
        from agentcore.core.ephemeral_env import scrub_env_values

        on_progress(
            "output", {"stream": stream, "chunk": scrub_env_values(chunk, env)}
        )

    return callback


def _sandbox_error_result(exc: SandboxError, start: float) -> ToolResult:
    duration_ms = int((time.monotonic() - start) * 1000)
    from agentcore.tools.sandbox.exec_env import (
        EXEC_ENV_SANDBOX_UNAVAILABLE_USER_MESSAGE,
        is_sandbox_unavailable_error,
        sandbox_unavailable_tool_meta,
    )

    if is_sandbox_unavailable_error(exc):
        msg = EXEC_ENV_SANDBOX_UNAVAILABLE_USER_MESSAGE
        return ToolResult(
            tool_call_id="",
            success=False,
            output=msg,
            error=msg,
            duration_ms=duration_ms,
            metadata=sandbox_unavailable_tool_meta(),
        )
    msg = exc.message or str(exc)
    # Local launcher / env start failures are self-correctable (switch language) —
    # mark contract_failure so the circuit breaker does not burn on them.
    launcher_fail = "代码执行环境启动失败" in msg
    return ToolResult(
        tool_call_id="",
        success=False,
        output=msg,
        error=msg,
        duration_ms=duration_ms,
        metadata={"code": "launcher_unavailable"} if launcher_fail else {},
        contract_failure=launcher_fail,
    )


async def execute_short(
    arguments: dict[str, Any],
    context: ToolContext,
    *,
    location: Literal["server", "local"] | None = None,
    languages: Sequence[str] | None = None,
) -> ToolResult:
    """Run a short, self-exiting snippet through the workspace sandbox."""
    start = time.monotonic()
    code = arguments.get("code", "")
    language = arguments.get("language", "python")
    timeout = min(arguments.get("timeout_seconds", 30), 60)  # cap at 60s
    allowed = _resolved_languages(languages)

    if not code.strip():
        return ToolResult(
            tool_call_id="",
            success=False,
            output="",
            error="缺少必填参数：code",
            duration_ms=0,
        )

    if language not in allowed:
        avail = "、".join(allowed) if allowed else "无"
        msg = (
            f"本机未装配 language={language}；可用：{avail}"
            "（见 `<工作区>` 解释器）。"
        )
        return ToolResult(
            tool_call_id="",
            success=False,
            output=msg,
            error=msg,
            duration_ms=int((time.monotonic() - start) * 1000),
            metadata={"code": "language_unavailable"},
            contract_failure=True,
        )

    from agentcore.core.ephemeral_env import EnvParseError, parse_ephemeral_env

    try:
        exec_env = parse_ephemeral_env(arguments.get("env"))
    except EnvParseError as exc:
        msg = exc.message
        return ToolResult(
            tool_call_id="",
            success=False,
            output=msg,
            error=msg,
            duration_ms=int((time.monotonic() - start) * 1000),
            metadata={"code": "env_invalid"},
            contract_failure=True,
        )

    matched = long_running_command_match(code)
    if matched is not None:
        msg = long_running_redirect_message(matched, location=location)
        return ToolResult(
            tool_call_id="",
            success=False,
            output="",
            error=msg,
            duration_ms=int((time.monotonic() - start) * 1000),
            metadata={"code": "long_running_redirect", "matched": matched},
            contract_failure=True,
        )

    inspect_hit = source_inspect_match(code)
    if inspect_hit is not None:
        msg = source_inspect_redirect_message(inspect_hit)
        inspect_code = (
            "source_dump_redirect"
            if inspect_hit.kind == "dump"
            else "source_grep_redirect"
        )
        return ToolResult(
            tool_call_id="",
            success=False,
            output="",
            error=msg,
            duration_ms=int((time.monotonic() - start) * 1000),
            metadata={
                "code": inspect_code,
                "matched": inspect_hit.matched,
                "kind": inspect_hit.kind,
            },
            contract_failure=True,
        )

    from agentcore.tools.sandbox.exec_env import EXEC_IDLE_TIMEOUT_DEFAULT_S

    request = ExecutionRequest(
        code=code,
        language=language,
        timeout_seconds=timeout,
        # Primary hang kill: silence; wall remains the short hard cap (≤60s).
        idle_timeout_seconds=min(int(timeout), EXEC_IDLE_TIMEOUT_DEFAULT_S),
        env=exec_env,
        on_output=_make_output_callback(context, exec_env),
        network_mode=(
            "restricted"
            if permission_allows_restricted_network(context.permission_axes)
            else "none"
        ),
    )

    # 工具执行阶段进度 (联网前端展示优化): the sandbox run is the slow blocking leg —
    # signal「正在执行」so the waiting row is live. Best-effort; ``on_phase`` is None on
    # unscoped call sites (tests / evals).
    if context.on_phase:
        context.on_phase("executing")
    try:
        # Per-conversation serial: same-session workers queue on short-exec only
        # (empty conversation_id → no lock; verify / long-running bypass this).
        # Cloud desk boot is prepare / resume — not this lock and not this call.
        async with code_execute_lock(context.conversation_id):
            result = await context.backend.execute(request)
    except SandboxError as e:
        return _sandbox_error_result(e, start)
    duration_ms = int((time.monotonic() - start) * 1000)

    from agentcore.core.ephemeral_env import scrub_env_values

    stdout = scrub_env_values(result.stdout or "", exec_env)
    stderr = scrub_env_values(result.stderr or "", exec_env)

    output_parts = []
    if stdout:
        output_parts.append(f"stdout:\n{stdout}")
    if stderr:
        output_parts.append(f"stderr:\n{stderr}")
    if not output_parts:
        output_parts.append("（无输出）")

    output = "\n".join(output_parts)
    if result.exit_code != 0:
        output += f"\n\n退出码：{result.exit_code}"

    # 产物写回: tell the model exactly which files landed in the workspace.
    if result.written_files:
        output += "\n\n已写回工作区：" + "、".join(result.written_files)
    # Render-oriented twin of ``output`` (工具结果富渲染): the client shows a
    # terminal-style view (stdout, stderr in red, exit-code badge) instead of
    # the flattened "stdout:\n…\nstderr:\n…" text. Kept structured so failures
    # (non-zero exit) surface stderr distinctly.
    display = {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": result.exit_code,
        "language": language,
    }
    if result.written_files:
        # Additive key (only when present) — desktop renders known keys and
        # ignores extras, so old fixtures/tests stay byte-identical.
        display["written_files"] = result.written_files

    # exit 127 + launcher message: environment contract, not a code bug —
    # allow same-round language switch without burning the circuit breaker.
    launcher_unavailable = (
        not result.success
        and result.exit_code == 127
        and "代码执行环境启动失败" in (result.stderr or "")
    )
    # gVisor rootless: sandbox network unsupported is permanent for this run —
    # first fail retires the unified ``run`` face (no warn=2 / disable=3 empty retries).
    stderr_l = (result.stderr or "").lower()
    sandbox_network_unsupported = (
        not result.success
        and "sandbox network isn't supported" in stderr_l
    )
    # Probe verdict that only takes out the requested language (see below):
    # honest failure, but a switch-the-language reject rather than a retire.
    probe_language_unavailable = False
    meta: dict[str, Any] = {}
    if launcher_unavailable:
        meta["code"] = "launcher_unavailable"
    elif sandbox_network_unsupported:
        meta["code"] = "sandbox_network_unsupported"
        meta["error_class"] = "permanent"
    else:
        stderr_text = result.stderr or ""
        from agentcore.tools.sandbox.exec_env import (
            EXEC_TIMEOUT_CODE,
            exec_env_probe_failure_code,
            exec_env_probe_failure_language,
            is_exec_env_probe_failure,
            probe_failure_retire_tools,
            should_retire_exec_env,
        )

        if is_exec_env_probe_failure(stderr_text):
            # Classified reason (missing interpreter / denied spawn) when the
            # real run proved one — else the generic env-fail code.
            probe_code = exec_env_probe_failure_code(stderr_text)
            meta["code"] = probe_code
            meta["exec_env_timeout"] = True
            # A dead python takes the verify path with it (every check is a python
            # script), any other language takes only itself, and a verdict
            # naming no language (gVisor runtime smoke) still takes the family.
            # Timeout never retires — that is slow user code, not a dead env.
            probe_language = exec_env_probe_failure_language(stderr_text)
            if should_retire_exec_env(
                probe_code, language=probe_language
            ) and not probe_failure_retire_tools(probe_language):
                # One interpreter missing — switch language. Do not retire run.
                probe_language_unavailable = True
        elif (not result.success) and "Timeout: execution exceeded" in stderr_text:
            meta["code"] = EXEC_TIMEOUT_CODE
            meta["exec_env_timeout"] = True
    return ToolResult(
        tool_call_id="",
        success=result.success,
        output=output,
        error=None if result.success else f"退出码 {result.exit_code}",
        duration_ms=duration_ms,
        display=display,
        metadata=meta,
        contract_failure=launcher_unavailable or probe_language_unavailable,
        # 结构化写回自报: 沙箱 ``written_files`` 的 EXACT 路径，交付物台账据此
        # 记账，永不解析那行「已写回工作区」中文散文（文件名可含「、」、措辞会变）。
        file_products=[file_product(p) for p in (result.written_files or [])],
    )
