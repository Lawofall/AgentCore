"""Code execution tool — runs code in the workspace via ``ToolContext.backend``.

Thin shell: the backend (``ServerWorkspace`` today, ``LocalWorkspace`` later)
owns the ``SandboxProvider`` and sets the working directory to the workspace
root, so executed code sees the same files the file tools do.
"""

import json
import time
from collections.abc import Sequence
from typing import Any, Literal

from agentcore.core.errors import SandboxError
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.tools.builtin.code_execute_lock import code_execute_lock
from agentcore.tools.builtin.long_running import (
    long_running_command_match,
    long_running_redirect_message,
)
from agentcore.tools.builtin.project_verify import (
    project_verify_command_match,
    project_verify_redirect_message,
)
from agentcore.tools.builtin.source_inspect import (
    source_inspect_match,
    source_inspect_redirect_message,
)
from agentcore.tools.file_products import file_product
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_WORKER_ONLY,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
)
from agentcore.tools.sandbox.cloud_python import format_cloud_python_libs
from agentcore.tools.sandbox.exec_languages import (
    ALL_EXEC_LANGUAGES,
    language_labels,
)
from agentcore.tools.sandbox.protocol import ExecutionRequest

# Re-export for existing test imports.
__all__ = [
    "CodeExecuteTool",
    "code_execute_description",
    "long_running_command_match",
    "long_running_redirect_message",
    "project_verify_command_match",
    "project_verify_redirect_message",
    "source_inspect_match",
    "source_inspect_redirect_message",
]

# 结构化写回自报 (交付物台账事实口径 · 契约见 tools/file_products.py):
# 输出里的「已写回工作区：…」是给模型看的自然语言；产物则在 ``ToolResult.file_products``
# 上自报本次执行 EXACT 的写回路径，让「脚本间接落盘」与 file_write 走同一条台账通道 ——
# 不必解析那行中文散文（脆弱：文件名可能含分隔符「、」、措辞会变、被截断）。
#
# C3 边界：code_execute 写回本期明确不走 WriteCoordinator 硬拦（可观测即可）；
# file_write / append / str_replace / delete / move 才是互斥闭包。


def _permission_allows_restricted_network(raw: str | None) -> bool:
    """True when session ``permission_axes`` JSON allows restricted network in sandbox."""
    if not raw:
        return False
    try:
        from agentcore.core.types import PermissionAxes

        data = json.loads(raw) if raw.lstrip().startswith("{") else None
        if isinstance(data, dict):
            return PermissionAxes.from_mapping(data).auto_executes
    except (ValueError, TypeError, json.JSONDecodeError):
        pass
    # Python dict-repr fallback from early wiring.
    return "'command': 'auto'" in raw or '"command": "auto"' in raw


_USAGE_TAIL = (
    "\n短跑会自行退出的内联代码或短命令。"
    "本工具 ≠ test_run（装包 / 项目级 typecheck·build·整仓测，如 npm run build）"
    "≠ terminal（长驻如 npm run dev）"
    "≠ terminal（会退出+wait）或 host(action=shell)（异步 HTTP 轮询超约 60s；本工具硬顶 60s）。"
    "优先 python / javascript 内联（bash 在 Windows 等主机可能不可用）。"
    "工作区相对路径；区外真实 OS 路径读 `AGENTCORE_EXTERNAL_<别名>`。"
    "公开网页摘录 → read_url / web_search；"
    "大 zip → archive_extract / archive_create；"
    "看源码 / 搜符号 → file_read / grep / code_search。"
    "解析表、改文件、跑计算仍用本工具。"
)

# Local-only: when a short CLI truly belongs on code_execute, don't default to bash —
# Windows PATH bash is often a broken WSL trampoline that hangs until timeout.
_LOCAL_CLI_HINT = (
    " 本机若确需短 CLI，请优先 language=javascript（node）或 python；"
    "勿默认 language=bash——Windows 上 PATH 的 bash 常是不可用的 WSL 蹦床。"
    "项目 test/typecheck/build 请用 test_run，不要塞进本工具。"
)


def _supported_phrase(languages: Sequence[str]) -> str:
    labels = language_labels(tuple(languages))
    if not languages:
        return "当前无可用解释器"
    return f"支持 {labels}"


def code_execute_description(
    location: Literal["server", "local"] | None = None,
    *,
    languages: Sequence[str] | None = None,
) -> str:
    """Location-aware tool description (云端沙箱 vs 用户本机).

    ``languages`` trims the advertised surface for local/sidecar probes; ``None``
    keeps the full catalog phrase (cloud fixed surface / unprobed callers).
    """
    langs = tuple(languages) if languages is not None else ALL_EXEC_LANGUAGES
    support = _supported_phrase(langs)
    if location == "local":
        where = (
            f"在【用户本机】工作区目录中执行代码（{support}），"
            "可访问工作区内的文件。命令真实跑在用户机器上，除非确有必要，"
            "避免破坏性或不可逆的操作。"
        )
    elif location == "server":
        where = (
            f"在【服务端云端沙箱】工作区目录中执行代码（{support}），"
            "可访问工作区内的文件。沙箱触达不了用户的电脑、本机应用与本机文件。"
            f"沙箱 Python 已预装常用文档 / 数据库：{format_cloud_python_libs()}（画图含中文时先设置"
            "字体如 Noto Sans CJK SC）。代码写到工作区相对路径的文件会在执行结束后"
            "保存进工作区（结果会列出写回的文件），用户可直接预览 / 下载。"
        )
    else:
        # Catalog / unknown backend: stay honest without the old two-way hedge.
        where = (
            f"在当前对话工作区目录中执行代码（{support}），"
            "可访问工作区内的文件。具体是云端沙箱还是用户本机，取决于本回合工作区绑定"
            "（见 `<工作区>`）。"
        )
    tail = _USAGE_TAIL
    if location == "local" and "bash" in langs:
        # Schema still advertises bash → keep the WSL "don't default to bash" nudge.
        tail = _USAGE_TAIL + _LOCAL_CLI_HINT
    return where + tail


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


def _code_execute_engine_wall(
    location: Literal["server", "local"] | None,
) -> float | None:
    """Engine wait_for: cloud must cover desk boot + exec; local keeps category 90s."""
    if location != "server":
        return None
    from agentcore.config import settings

    return (
        float(settings.gvisor_desk_start_timeout_seconds)
        + float(settings.tool_execution_timeout_seconds)
    )


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


class CodeExecuteTool:
    """Execute code in the workspace environment for this turn's backend."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_WORKER_ONLY,
        execution_class=True,
        needs_location=True,
        accepts_exec_languages=True,
        # 间接落盘（沙箱 bind 写盘）也是落盘：自报 ``written_files`` 的 EXACT 路径。
        file_products=FileProductsContract.SELF_REPORT,
        # 沙箱预装库见 cloud_python.txt；无专用 md_to_* 导出器的 Office 走这里。
        produces_formats=(".xlsx", ".pptx"),
    )

    def __init__(
        self,
        *,
        location: Literal["server", "local"] | None = None,
        languages: Sequence[str] | None = None,
    ) -> None:
        self._location = location
        # None → full catalog surface (cloud / tests). Explicit list → probe trim.
        self._languages: tuple[str, ...] = (
            tuple(lang for lang in languages if lang in ALL_EXEC_LANGUAGES)
            if languages is not None
            else ALL_EXEC_LANGUAGES
        )

    @property
    def schema(self) -> ToolSchema:
        langs = list(self._languages)
        default_lang = (
            "python"
            if "python" in langs
            else (langs[0] if langs else "python")
        )
        return ToolSchema(
            name="code_execute",
            description=code_execute_description(
                self._location, languages=self._languages
            ),
            parameters={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的代码",
                    },
                    "language": {
                        "type": "string",
                        "enum": langs,
                        "description": "编程语言",
                        "default": default_lang,
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "最长执行时间（秒）",
                        "default": 30,
                    },
                    "purpose": {
                        "type": "string",
                        "description": (
                            "一句话中文说明这段代码要做什么；会展示给用户作为审批说明，"
                            "执行时忽略"
                        ),
                    },
                    "env": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": (
                            "当次进程环境变量。本回合已给的凭据填此代跑"
                            "（不落盘、不进 code / 工作区明文）。禁覆盖 PATH 等。"
                        ),
                    },
                },
                "required": ["code"],
            },
            category=ToolCategory.EXECUTION,
            approval=ToolApproval.GRANTABLE,
            timeout_seconds=_code_execute_engine_wall(self._location),
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        code = arguments.get("code", "")
        language = arguments.get("language", "python")
        timeout = min(arguments.get("timeout_seconds", 30), 60)  # cap at 60s

        if not code.strip():
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="缺少必填参数：code",
                duration_ms=0,
            )

        if language not in self._languages:
            avail = "、".join(self._languages) if self._languages else "无"
            msg = (
                f"本机未装配 language={language}；可用：{avail}"
                "（见 `<工作区>` 可用解释器）。"
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
            msg = long_running_redirect_message(matched, location=self._location)
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=msg,
                duration_ms=int((time.monotonic() - start) * 1000),
                metadata={"code": "long_running_redirect", "matched": matched},
                contract_failure=True,
            )

        verify_matched = project_verify_command_match(code)
        if verify_matched is not None:
            msg = project_verify_redirect_message(
                verify_matched,
                verify_policy=getattr(context, "verify_policy", "") or "",
            )
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=msg,
                duration_ms=int((time.monotonic() - start) * 1000),
                metadata={"code": "project_verify_redirect", "matched": verify_matched},
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
                if _permission_allows_restricted_network(context.permission_axes)
                else "none"
            ),
        )

        # 工具执行阶段进度 (联网前端展示优化): the sandbox run is the slow blocking leg —
        # signal「正在执行」so the waiting row is live. Best-effort; ``on_phase`` is None on
        # unscoped call sites (tests / evals).
        if context.on_phase:
            context.on_phase("executing")
        try:
            # Boot the cloud desk before the per-conversation exec lock so a
            # minutes-scale start_detach does not queue sibling workers.
            if self._location == "server":
                ensure = getattr(context.backend, "ensure_workspace_desk", None)
                if callable(ensure):
                    await ensure()
            # Per-conversation serial: same-session workers queue on code_execute only
            # (empty conversation_id → no lock; test_run / terminal bypass this).
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
        # first fail retires code_execute (no warn=2 / disable=3 empty retries).
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
            meta["retire_tools"] = ["code_execute"]
            meta["retire_message"] = (
                "工具 `code_execute` 因沙箱网络能力不可用已停用——"
                "请换路径推进（勿再依赖沙箱出网），禁止原样重试。"
            )
        else:
            stderr_text = result.stderr or ""
            from agentcore.tools.sandbox.exec_env import (
                EXEC_TIMEOUT_CODE,
                exec_env_probe_failure_code,
                exec_env_probe_failure_language,
                is_exec_env_probe_failure,
                probe_failure_retire_steer,
                probe_failure_retire_tools,
                should_retire_exec_env,
            )

            if is_exec_env_probe_failure(stderr_text):
                # Classified reason (missing interpreter / denied spawn) when the
                # real run proved one — else the generic env-fail code.
                probe_code = exec_env_probe_failure_code(stderr_text)
                meta["code"] = probe_code
                meta["exec_env_timeout"] = True
                # A dead python takes test_run with it (every check is a python
                # script), any other language takes only itself, and a verdict
                # naming no language (gVisor runtime smoke) still takes the family.
                # Timeout never retires — that is slow user code, not a dead env.
                probe_language = exec_env_probe_failure_language(stderr_text)
                if should_retire_exec_env(probe_code, language=probe_language):
                    retire = probe_failure_retire_tools(probe_language)
                    if retire:
                        meta["error_class"] = "permanent"
                        meta["retire_tools"] = list(retire)
                        meta["retire_message"] = probe_failure_retire_steer(
                            probe_code, language=probe_language
                        )
                    else:
                        # One interpreter is missing while the rest of the toolset
                        # is untouched — switch-the-language reject, not a retire.
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
