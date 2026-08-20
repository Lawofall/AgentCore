"""Delegate completion soft checks (S3: no completion_criteria kind).

Batch acceptance kinds and kind-based binding hard gates are retired.
Wrong-close handoff = CEO prompt review + deliverable/contract/landing soft + human review.
Soft overlays (TS verify remind, import-graph scan) and capability soft warnings remain.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.llm.provider.protocol import LLMMessage, llm_content_text
from agentcore.runtime.runs.types import RunPhase, RunState

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan

logger = get_logger(__name__)

_TYPESCRIPT_SUFFIXES = frozenset({".ts", ".tsx"})

_GRAPH_SOURCE_SUFFIXES = frozenset({".ts", ".tsx", ".vue"})

_VERIFY_COMMAND_RE = re.compile(
    r"\b(?:"
    r"tsc\b|vue-tsc\b|typecheck\b|"
    r"(?:npm|pnpm|yarn)\s+run\s+(?:test|typecheck|build|lint)\b|"
    r"(?:npm|pnpm|yarn)\s+test\b|"
    r"pytest\b|cargo\s+(?:test|check|build)\b|go\s+test\b|"
    r"(?:mvn|gradlew?)\s+test\b"
    r")",
    re.IGNORECASE,
)

_EXECUTION_TASK_HINTS = re.compile(
    r"(运行|启动|安装|跑通|联调|验收|测试通过|"
    r"npm\s+(run|start)|pnpm\s+(run|start)|yarn\s+(run|start|dev)|"
    r"python\s+-m|uv\s+run|pip\s+run|cargo\s+run|go\s+run|进程)",
    re.IGNORECASE,
)

_RUNTIME_READY_TASK_HINTS = re.compile(
    r"(?:"
    r"启动(?:项目|应用|服务|服务器|开发服务器|dev)"
    r"|把(?:这个|该)?项目跑起来|把服务跑起来|跑起来(?:项目|服务|应用|开发服务器)?"
    r"|开发服务器|dev\s*server|长驻|后台进程|wait_for"
    r"|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:dev|start)\b"
    r"|(?:npx|bunx)\s+(?:vite|next|nuxt|webpack-dev-server)\b"
    r"|vite\s+--host|next\s+dev|uvicorn\b|runserver\b|flask\s+run\b"
    r")",
    re.IGNORECASE,
)

_VERIFY_TASK_HINTS = re.compile(
    r"(?:"
    r"\btsc\b|vue-tsc\b|typecheck|type-check|pytest|vitest|\bjest\b|unittest"
    r"|(?:npm|pnpm|yarn)\s+run\s+(?:test|build|typecheck|lint)\b"
    r"|(?:npm|pnpm|yarn)\s+test\b"
    r"|cargo\s+(?:test|check|build)\b|go\s+test\b"
    r"|(?:mvn|gradlew?)\s+test\b"
    r"|跑通测试|单元测试|集成测试|编译检查|类型检查|build\s*通过|测试通过"
    r")",
    re.IGNORECASE,
)

# ``.docx`` / ``.pdf`` are deliberately absent: ``md_to_docx`` / ``md_to_pdf`` are
# deterministic FILESYSTEM exporters registered unconditionally, so a Word / PDF
# target still lands with the execution sandbox withheld.
_BINARY_ARTIFACT_HINTS = re.compile(
    r"(python-pptx|openpyxl|ffmpeg|可播放|可直接播放|二进制|可执行文件|"
    r"\.pptx|\.xlsx|\.exe|\.apk|\.mp4|\.mp3|\.wav|\.avi|\.mov)",
    re.IGNORECASE,
)

# Office targets with no deterministic exporter — these really do need execution.
_EXEC_OFFICE_SUFFIXES = frozenset({".pptx", ".xlsx", ".odt", ".rtf"})

_EXEC_OFFICE_HINTS = re.compile(
    r"(?:"
    r"\.pptx|\.xlsx|\.odt|\.rtf|"
    r"python-pptx|openpyxl|"
    r"幻灯片|演示文稿|课件|"
    r"\bPPTX?\b|PowerPoint|"
    r"Excel(?:表|表格|文件)?"
    r")",
    re.IGNORECASE,
)

def _clean_how_fixed(*parts: Any) -> str:
    """First non-empty how-fixed string (verify_command / description / playbook slot)."""
    for part in parts:
        if part is None:
            continue
        text = str(part).strip()
        if text:
            return text
    return ""

def extract_playbook_how_fixed(playbook_args: Any) -> str:
    """``verify`` / ``verify_command`` / ``acceptance`` slot from playbook_args."""
    if not isinstance(playbook_args, dict):
        return ""
    return _clean_how_fixed(
        playbook_args.get("verify_command"),
        playbook_args.get("verify"),
        playbook_args.get("acceptance"),
    )

def validate_repair_how_fixed(
    *,
    playbook: str | None = None,
    playbook_args: Any = None,
) -> str | None:
    """Reject ``repair_code`` playbooks that omit structured「怎么算修好」.

    S3: no longer tied to ``completion_criteria`` / ``code_verified`` kind.
    How-fixed comes from ``playbook_args.verify`` / ``verify_command`` / ``acceptance``.
    """
    pb = (playbook or "").strip()
    if pb != "repair_code":
        return None
    how = extract_playbook_how_fixed(playbook_args)
    if how:
        return None
    return (
        "修码收口契约：playbook=repair_code 须写清「怎么算修好」。"
        "在 playbook_args 填 verify（或 verify_command / acceptance），"
        '例如 verify="pytest tests/test_foo.py -q"、'
        "verify=\"python -c 'from app import foo; assert foo()'\"、或 "
        'verify="打开 /app 白屏消失+snapshot 可见主内容"。'
    )

def plan_suggests_code_verification(plan: RunPlan) -> bool:
    """True when any worker task reads like run/open/install acceptance."""
    for node in plan.nodes:
        text = (node.task or "").strip()
        if text and _EXECUTION_TASK_HINTS.search(text):
            return True
    return False

def plan_suggests_runtime_ready(plan: RunPlan) -> bool:
    """True when any task reads like start-a-long-running-process acceptance."""
    for node in plan.nodes:
        text = (node.task or "").strip()
        if text and _RUNTIME_READY_TASK_HINTS.search(text):
            return True
    return False

def plan_suggests_verify(plan: RunPlan) -> bool:
    """True when any task reads like compile/test/build verify acceptance."""
    for node in plan.nodes:
        text = (node.task or "").strip()
        if text and _VERIFY_TASK_HINTS.search(text):
            return True
    return False

def plan_declares_artifacts(plan: RunPlan) -> bool:
    """True when any worker deliverable declares a non-empty ``artifacts`` list."""
    for node in plan.nodes:
        d = node.deliverable
        if d is not None and d.artifacts:
            return True
    return False

def plan_declares_files_form(plan: RunPlan) -> bool:
    """True when any worker deliverable declares ``form=files``."""
    for node in plan.nodes:
        d = node.deliverable
        if d is not None and d.form == "files":
            return True
    return False

def plan_all_workers_prose(plan: RunPlan) -> bool:
    """True when every worker explicitly declares ``form=prose`` (non-empty plan)."""
    if not plan.nodes:
        return False
    for node in plan.nodes:
        d = node.deliverable
        if d is None or d.form != "prose":
            return False
    return True

def plan_has_writable_worker(plan: RunPlan) -> bool:
    """True when at least one worker can land files (not ``form=prose``).

    ``form`` omitted (legacy) keeps write tools; only explicit ``prose`` withholds them.
    """
    if not plan.nodes:
        return False
    for node in plan.nodes:
        d = node.deliverable
        if d is None or d.form != "prose":
            return True
    return False

def validate_cold_start_explore_deliverables(
    plan: RunPlan,
    *,
    explicit_criteria: Any = None,
) -> str | None:
    """Hard-reject thin explore teams while cold-start explore is pending.

    ``form`` / ``artifacts`` are orthogonal to explore-pending: workers may land
    notes under ``write_scope=explore_memory`` (enforced at write-tool layer).
    Explore teams must fan out ≥2 angles (1 worker 包办整仓 is rejected).
    ``explicit_criteria`` is retained for call-site compat (unused).
    Returns CEO-facing error text, or ``None`` when the batch is fine.
    """
    del explicit_criteria  # API compat; form/artifacts no longer gated here.
    if len(plan.nodes) < 2:
        return (
            "冷启动探索未完成：探路委派须 ≥2 角并行（例：目录/入口 vs 设计·约定文档），"
            "禁止 1 人包办整仓摸底。请拆成至少两名调研 worker 后重调 delegate。"
        )
    return None

def plan_mentions_binary_artifact(plan: RunPlan) -> bool:
    """True when any worker task reads like a binary / playable deliverable."""
    for node in plan.nodes:
        text = (node.task or "").strip()
        if text and _BINARY_ARTIFACT_HINTS.search(text):
            return True
    return False

def _path_looks_exec_office(path: str) -> bool:
    lowered = path.lower().replace("\\", "/")
    return any(
        lowered.endswith(suf) or lowered.endswith(f"*{suf}") or f"*{suf}" in lowered
        for suf in _EXEC_OFFICE_SUFFIXES
    )

def plan_suggests_exec_office_deliverable(plan: RunPlan) -> bool:
    """True when any worker task/artifacts read like an Office target needing execution.

    ``.docx`` / ``.pdf`` are excluded on purpose — ``md_to_docx`` / ``md_to_pdf``
    produce them deterministically without a sandbox.
    """
    for node in plan.nodes:
        text = (node.task or "").strip()
        if text and _EXEC_OFFICE_HINTS.search(text):
            return True
        d = node.deliverable
        if d is None:
            continue
        name = str(getattr(d, "name", "") or "").strip()
        if name and _path_looks_exec_office(name):
            return True
        for art in d.artifacts or []:
            if art and _path_looks_exec_office(str(art)):
                return True
    return False

def node_holds_write_tools(spec: Any) -> bool:
    """真纯丙：不再用 ``spec.tools`` 白名单判断写盘能力；默认视为具备。

    H2 已取消 ``form=prose`` 硬卸写盘；本函数恒 True（写盘仍过用户授权 / write_scope）。
    """
    del spec
    return True

def execution_capability_warning(
    plan: RunPlan,
    backend: Any,
    permission_axes: Any = None,
) -> str | None:
    """Soft warning: binary-artifact / run-flavoured smell with no execution class.

    Never blocks. S3: no kind-based hard-gate counterpart.
    """
    if not (plan_suggests_code_verification(plan) or plan_mentions_binary_artifact(plan)):
        return None
    from agentcore.runtime.delegate.exec_env_remediation import exec_env_remediation_zh
    from agentcore.tools.builtin import execution_class_enabled_for

    if execution_class_enabled_for(backend, permission_axes):
        if (
            plan_suggests_runtime_ready(plan)
            and getattr(backend, "location", None) != "local"
        ):
            return exec_env_remediation_zh(
                backend=backend, kind="capability_runtime_ready"
            )
        return None
    if plan_suggests_exec_office_deliverable(plan):
        return exec_env_remediation_zh(backend=backend, kind="capability_office")
    return exec_env_remediation_zh(backend=backend, kind="capability_run")

def node_holds_execution_tools(spec: Any) -> bool:
    """真纯丙：不再用 ``spec.tools`` 白名单判断执行类工具；默认视为具备。

    环境是否真装配 ``code_execute`` / ``test_run`` / ``terminal`` 仍由 registry /
    能力行回答，与名单无关。
    """
    del spec
    return True

def _code_execute_succeeded_in_transcript(transcript: list[LLMMessage]) -> bool:
    """True when at least one ``code_execute`` call completed without a non-zero exit."""
    call_names: dict[str, str] = {}
    for msg in transcript:
        if msg.role != "assistant" or not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            call_names[tc.id] = tc.function.name
    for msg in transcript:
        if msg.role != "tool" or not msg.tool_call_id:
            continue
        if call_names.get(msg.tool_call_id) != "code_execute":
            continue
        content = llm_content_text(msg.content)
        if "退出码" not in content:
            return True
    return False

def _test_run_succeeded_in_transcript(transcript: list[LLMMessage]) -> bool:
    """True when at least one ``test_run`` completed with a passing verify signal.

    Accepts structured test summaries (``通过：``) and the bounded-verify header
    ``## 验证结果：通过`` (typecheck / build / command checks).
    """
    call_names: dict[str, str] = {}
    for msg in transcript:
        if msg.role != "assistant" or not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            call_names[tc.id] = tc.function.name
    for msg in transcript:
        if msg.role != "tool" or not msg.tool_call_id:
            continue
        if call_names.get(msg.tool_call_id) != "test_run":
            continue
        content = llm_content_text(msg.content)
        if "测试未通过" in content or "验证未通过" in content:
            continue
        if "预算耗尽" in content or "验证未完成" in content:
            continue
        if "## 验证结果：通过" in content:
            return True
        fail_m = re.search(r"失败：(\d+)", content)
        err_m = re.search(r"错误：(\d+)", content)
        if fail_m and int(fail_m.group(1)) > 0:
            continue
        if err_m and int(err_m.group(1)) > 0:
            continue
        if "通过：" in content:
            return True
    return False

def _tool_call_args_map(transcript: list[LLMMessage]) -> dict[str, tuple[str, str]]:
    """Map ``tool_call_id → (tool_name, arguments_json)`` from assistant turns."""
    out: dict[str, tuple[str, str]] = {}
    for msg in transcript:
        if msg.role != "assistant" or not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            out[tc.id] = (tc.function.name, tc.function.arguments or "")
    return out

def _terminal_verify_succeeded_in_transcript(transcript: list[LLMMessage]) -> bool:
    """True when a ``terminal`` call ran a verify-shaped command and exited cleanly."""
    calls = _tool_call_args_map(transcript)
    for msg in transcript:
        if msg.role != "tool" or not msg.tool_call_id:
            continue
        name, args_json = calls.get(msg.tool_call_id, ("", ""))
        if name != "terminal":
            continue
        if not _VERIFY_COMMAND_RE.search(args_json):
            continue
        content = llm_content_text(msg.content)
        # Prefer exited 0; also accept matched ready from a one-shot wait_for.
        if "status: exited" in content and "exit_code: 0" in content:
            return True
        if re.search(r"exit_code:\s*0\b", content) and "status: exited" in content:
            return True
        # wait_for hit on a verify command (unusual but honest).
        if ("matched: True" in content or "matched: true" in content) and (
            "status: running" in content or "status: exited" in content
        ):
            return True
    return False

def _code_execute_verify_succeeded_in_transcript(transcript: list[LLMMessage]) -> bool:
    """``code_execute`` whose code looks like typecheck/test/build and exited 0.

    Requires an explicit ``退出码：0`` (or ``退出码:0``) in the tool result — bare
    success text or missing exit marker does not count.
    """
    calls = _tool_call_args_map(transcript)
    for msg in transcript:
        if msg.role != "tool" or not msg.tool_call_id:
            continue
        name, args_json = calls.get(msg.tool_call_id, ("", ""))
        if name != "code_execute":
            continue
        if not _VERIFY_COMMAND_RE.search(args_json):
            continue
        content = llm_content_text(msg.content)
        if re.search(r"退出码[：:]\s*0\b", content):
            return True
    return False

def _run_verified_in_transcript(transcript: list[LLMMessage]) -> bool:
    """Honest verify only: test_run / verify-shaped code_execute / terminal.

    Non-verify ``code_execute`` success is intentionally excluded (delivery_status
    still uses ``_code_execute_succeeded_in_transcript`` for writeback sniffing).
    """
    if not transcript:
        return False
    if _test_run_succeeded_in_transcript(transcript):
        return True
    if _code_execute_verify_succeeded_in_transcript(transcript):
        return True
    return _terminal_verify_succeeded_in_transcript(transcript)

def _is_typescript_path(path: str) -> bool:
    from pathlib import PurePosixPath

    suffix = PurePosixPath(path.replace("\\", "/")).suffix.lower()
    return suffix in _TYPESCRIPT_SUFFIXES

def _is_graph_source_path(path: str) -> bool:
    from pathlib import PurePosixPath

    suffix = PurePosixPath(path.replace("\\", "/")).suffix.lower()
    return suffix in _GRAPH_SOURCE_SUFFIXES

def _batch_landed_typescript(completed: list[RunState]) -> bool:
    """True when any COMPLETED worker landed a ``.ts`` / ``.tsx`` path."""
    for state in completed:
        for path in state.files_touched or []:
            if path and _is_typescript_path(path):
                return True
        if state.transcript:
            for path in _files_from_transcript(state.transcript):
                if path and _is_typescript_path(path):
                    return True
    return False

def _batch_landed_graph_sources(completed: list[RunState]) -> bool:
    """True when any COMPLETED worker landed a ``.ts`` / ``.tsx`` / ``.vue`` path."""
    for state in completed:
        for path in state.files_touched or []:
            if path and _is_graph_source_path(path):
                return True
        if state.transcript:
            for path in _files_from_transcript(state.transcript):
                if path and _is_graph_source_path(path):
                    return True
    return False

def _collect_graph_source_paths(completed: list[RunState]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for state in completed:
        paths = list(state.files_touched or [])
        if state.transcript:
            paths.extend(_files_from_transcript(state.transcript))
        for path in paths:
            if path and _is_graph_source_path(path) and path not in seen:
                seen.add(path)
                out.append(path)
    return out

def _graph_gap_message() -> str:
    return (
        "import 图不闭合：已落盘 .ts/.tsx/.vue 存在悬空相对路径或 `@/` 引用"
        "（缺文件；须同批补齐或修正 import）"
    )

def _append_graph_gaps(
    gaps: list[str],
    *,
    completed: list[RunState],
    backend: Any = None,
    file_map: dict[str, str] | None = None,
) -> None:
    """Append graph_consistent gaps when source texts are available."""
    from agentcore.runtime.delegate.graph_integrity import (
        format_graph_gap,
        load_source_file_map_sync,
        resolve_missing_imports,
    )

    paths = _collect_graph_source_paths(completed)
    if not paths:
        return
    texts: dict[str, str] = dict(file_map or {})
    if not texts and backend is not None:
        texts = load_source_file_map_sync(backend, paths)
    if not texts:
        # No readable sources — cannot honestly claim a miss; skip (drive_finalize
        # should pass an async-loaded file_map for cloud/local channel backends).
        return
    missing = resolve_missing_imports(texts)
    if not missing:
        return
    msg = format_graph_gap(missing) or _graph_gap_message()
    if msg not in gaps:
        gaps.append(msg)

def _overlay_verify_soft_note() -> str:
    """Soft reminder when .ts/.tsx landed without a verify signal (D2 overlay)."""
    return (
        "提醒（不阻断验收）：已落盘 .ts/.tsx，建议补一次验证"
        "（code_execute / test_run / terminal 跑通 tsc|typecheck|test|build；"
        "启动开发服务器不算）"
    )

def _as_overlay_soft_note(msg: str) -> str:
    """Mark auto-scan / overlay copy as soft for delivery_status (warning / notes)."""
    if "不阻断验收" in (msg or ""):
        return msg
    return f"提醒（不阻断验收）：{msg}"

def _worker_files_written(state: RunState) -> bool:
    if state.files_touched:
        return True
    return bool(state.transcript and _files_from_transcript(state.transcript))

def _files_from_transcript(transcript: list[LLMMessage]) -> list[str]:
    from agentcore.runtime.runs.serialize import files_touched_from_transcript

    return files_touched_from_transcript(transcript)

def collect_completion_soft_notes(
    results: dict[str, RunState],
    *,
    backend: Any = None,
    file_map: dict[str, str] | None = None,
) -> list[str]:
    """Soft overlay notes after workers finish (never blocks acceptance).

    S3: no ``completion_criteria`` kind / binding gate. Remains: D2 (.ts/.tsx
    without verify) and auto import-graph scan on .ts/.tsx/.vue. Deliverable /
    contract / landing soft lives in delivery_status + collect_worker_gaps.
    """
    completed = [s for s in results.values() if s.phase is RunPhase.COMPLETED]
    if not completed:
        return []

    soft_notes: list[str] = []
    if _batch_landed_typescript(completed) and not any(
        _run_verified_in_transcript(s.transcript or []) for s in completed
    ):
        soft_notes.append(_overlay_verify_soft_note())

    if _batch_landed_graph_sources(completed):
        overlay_graph: list[str] = []
        _append_graph_gaps(
            overlay_graph, completed=completed, backend=backend, file_map=file_map
        )
        for msg in overlay_graph:
            note = _as_overlay_soft_note(msg)
            if note not in soft_notes:
                soft_notes.append(note)

    return soft_notes

def collect_delivered_files(results: dict[str, RunState]) -> list[str]:
    """Ordered, deduped workspace paths COMPLETED workers wrote."""
    seen: set[str] = set()
    out: list[str] = []
    for state in results.values():
        if state is None or state.phase is not RunPhase.COMPLETED:
            continue
        for path in state.files_touched or []:
            if path and path not in seen:
                seen.add(path)
                out.append(path)
    return out

def _tool_result_failed(content: str) -> bool:
    """True when tool_exec stamped the machine failure trailer on this tool message."""
    return "<!--agentcore:tool_failed-->" in (content or "")

def _browser_navigate_failed_in_transcript(transcript: list[LLMMessage]) -> bool:
    """True when a navigate-shaped browser result carries the tool-failed trailer.

    Dual-recognizes live ``browser`` + ``action=navigate`` and pre-merge
    ``browser_navigate``.
    """
    if not transcript:
        return False
    from agentcore.runtime.browser.call_identity import is_browser_navigate_call

    calls = _tool_call_args_map(transcript)
    for msg in transcript:
        if msg.role != "tool" or not msg.tool_call_id:
            continue
        name, args_json = calls.get(msg.tool_call_id, ("", ""))
        if not is_browser_navigate_call(name, args_json):
            continue
        if _tool_result_failed(llm_content_text(msg.content)):
            return True
    return False

def _test_run_budget_exhausted_in_transcript(transcript: list[LLMMessage]) -> bool:
    """True when a ``test_run`` result reports timeout incomplete (已中止)."""
    if not transcript:
        return False
    calls = _tool_call_args_map(transcript)
    for msg in transcript:
        if msg.role != "tool" or not msg.tool_call_id:
            continue
        name, _ = calls.get(msg.tool_call_id, ("", ""))
        if name != "test_run":
            continue
        content = llm_content_text(msg.content)
        if "预算耗尽" in content or "验证未完成" in content:
            return True
        if "未完成（预算耗尽）" in content:
            return True
        if "未完成（执行无响应）" in content or "未完成（强制中止）" in content:
            return True
        if "已跑满灾难顶" in content or "按挂起中止" in content:
            return True
        if "执行超过" in content and "无输出" in content:
            return True
    return False

def _test_run_failed_in_transcript(transcript: list[LLMMessage]) -> bool:
    """True when a ``test_run`` was attempted and none succeeded (未过)."""
    if not transcript:
        return False
    calls = _tool_call_args_map(transcript)
    saw_test_run = False
    for msg in transcript:
        if msg.role != "tool" or not msg.tool_call_id:
            continue
        name, _ = calls.get(msg.tool_call_id, ("", ""))
        if name != "test_run":
            continue
        saw_test_run = True
    if not saw_test_run:
        return False
    return not _test_run_succeeded_in_transcript(transcript)

def _verify_shaped_command_failed_in_transcript(transcript: list[LLMMessage]) -> bool:
    """True when verify-shaped ``code_execute`` / ``terminal`` ran and none exited 0.

    Mirrors the success predicates used by ``_run_verified_in_transcript``: only
    typecheck/test/build-shaped commands count. A failed verify attempt with no
    later success must depress delivery (可用性诚实性 · 丙).
    """
    if not transcript:
        return False
    calls = _tool_call_args_map(transcript)
    saw_verify_shaped = False
    for msg in transcript:
        if msg.role != "tool" or not msg.tool_call_id:
            continue
        name, args_json = calls.get(msg.tool_call_id, ("", ""))
        if name not in ("code_execute", "terminal"):
            continue
        if not _VERIFY_COMMAND_RE.search(args_json or ""):
            continue
        saw_verify_shaped = True
    if not saw_verify_shaped:
        return False
    if _code_execute_verify_succeeded_in_transcript(transcript):
        return False
    return not _terminal_verify_succeeded_in_transcript(transcript)

_VERIFY_FAILED_REASON = "verify_failed"
_VERIFY_BUDGET_REASON = "verify_budget"
_VERIFY_BUDGET_GAP_DESC = (
    "验证未完成（无响应或强制中止，进程已中止，非仍在跑）"
)

def _verify_failure_rows(transcript: list[LLMMessage]) -> list[dict[str, str]]:
    """Structured verify-failure gap rows (description + reason) for one transcript."""
    out: list[dict[str, str]] = []
    if _browser_navigate_failed_in_transcript(transcript):
        out.append(
            {
                "description": "浏览器验证失败（未成功打开目标页）",
                "reason": _VERIFY_FAILED_REASON,
            }
        )
    if _test_run_budget_exhausted_in_transcript(transcript):
        out.append(
            {
                "description": _VERIFY_BUDGET_GAP_DESC,
                "reason": _VERIFY_BUDGET_REASON,
            }
        )
    elif _test_run_failed_in_transcript(transcript):
        out.append(
            {
                "description": "测试未通过（test_run 未全部通过）",
                "reason": _VERIFY_FAILED_REASON,
            }
        )
    if _verify_shaped_command_failed_in_transcript(transcript):
        out.append(
            {
                "description": (
                    "验证命令未通过（verify 形 code_execute / terminal "
                    "非零退出或执行失败）"
                ),
                "reason": _VERIFY_FAILED_REASON,
            }
        )
    return out

def _verify_failure_descriptions(transcript: list[LLMMessage]) -> list[str]:
    """Human one-liners for verify-shaped tool failures present in ``transcript``."""
    return [row["description"] for row in _verify_failure_rows(transcript)]

def collect_verify_failure_gaps(
    plan: RunPlan,
    results: dict[str, RunState],
) -> list[tuple[str, list[dict[str, str]]]]:
    """Per-COMPLETED-worker verify-tool failure gaps (可用性诚实性 · 丙).

    Scans worker transcripts for browser navigate / ``test_run`` / verify-shaped
    ``code_execute``·``terminal`` failures. Each hit becomes a blocking gap row with
    ``reason=verify_failed`` (or ``verify_budget`` for idle/disaster timeout incomplete)
    so ``build_delivery_status`` cannot stay ``delivered``.
    """
    out: list[tuple[str, list[dict[str, str]]]] = []
    for node in plan.nodes:
        state = results.get(node.run_id)
        if state is None or state.phase is not RunPhase.COMPLETED:
            continue
        rows = _verify_failure_rows(state.transcript or [])
        if not rows:
            continue
        label = node.role or node.run_id
        out.append((label, rows))
    return out

def collect_worker_gaps(
    plan: RunPlan,
    results: dict[str, RunState],
) -> list[tuple[str, list[dict[str, str]]]]:
    """Per-worker structured gaps for CEO synthesis (warnings + degraded handoff).

    Returns ``[(role_label, gap_rows), ...]`` only for workers that still carry
    contract / handoff / cutoff shortfalls after soft-accept — so forced
    convergence finalize (write tools withheld) still surfaces what was never
    delivered. Each gap row is ``{description, reason?}`` where ``reason`` is a
    machine code when the signal is a known cutoff
    (``token_budget`` / ``worker_timeout`` / ``degraded_handoff``).

    刀1：worker 已有落盘时 ``degraded_handoff`` 带 ``severity=warning``（备注，非硬缺口）。
    """
    from agentcore.runtime.runs.cutoff import (
        DEGRADED_HANDOFF_WARNING,
        REASON_DEGRADED_HANDOFF,
        reason_for_warning,
    )

    out: list[tuple[str, list[dict[str, str]]]] = []
    for node in plan.nodes:
        state = results.get(node.run_id)
        if state is None or state.phase is not RunPhase.COMPLETED:
            continue
        files_landed = bool(state.files_touched) or any(
            isinstance(a, dict) and a.get("status") == "accepted"
            for a in (state.file_acceptance or [])
        )
        gaps: list[dict[str, str]] = []
        seen_desc: set[str] = set()
        # Prefer first-class delivery_gaps when present (single source).
        for row in getattr(state, "delivery_gaps", None) or []:
            if not isinstance(row, dict):
                continue
            text = str(row.get("description") or "").strip()
            if not text or text in seen_desc:
                continue
            seen_desc.add(text)
            item: dict[str, str] = {"description": text}
            reason = str(row.get("reason") or "").strip()
            if reason:
                item["reason"] = reason
            severity = str(row.get("severity") or "").strip()
            if severity:
                item["severity"] = severity
            elif reason == REASON_DEGRADED_HANDOFF and files_landed:
                item["severity"] = "warning"
            gaps.append(item)
        if state.warnings:
            for raw in state.warnings:
                text = str(raw).strip()
                if not text or text in seen_desc:
                    continue
                seen_desc.add(text)
                entry: dict[str, str] = {"description": text}
                code = reason_for_warning(text)
                if code:
                    entry["reason"] = code
                    if code == REASON_DEGRADED_HANDOFF and files_landed:
                        entry["severity"] = "warning"
                gaps.append(entry)
        debrief = state.debrief if isinstance(state.debrief, dict) else None
        if debrief and debrief.get("degraded"):
            text = DEGRADED_HANDOFF_WARNING
            if text not in seen_desc:
                seen_desc.add(text)
                row = {"description": text, "reason": REASON_DEGRADED_HANDOFF}
                if files_landed:
                    row["severity"] = "warning"
                gaps.append(row)
        if gaps:
            label = node.role or node.run_id
            out.append((label, gaps))
    return out

# Exact hard-gap closing copy — do not rephrase (soft-only path drops this block).
_HARD_GAPS_CLOSING = (
    "\n**【终稿诚实性·部分交付】**上方契约缺口非空：终稿必须使用「部分交付 / 尚未齐备」"
    "类措辞，点明未闭合缺口与建议下一步；"
    "【禁止】写「完整交付 / 全部完成 / 可运行无缺 / 无需审计 / 团队已交付完毕」等完成度断言。"
)
_SOFT_GAPS_CLOSING = (
    "\n**【终稿诚实性】**"
    "【禁止】写「完整交付 / 全部完成 / 可运行无缺 / 无需审计 / 团队已交付完毕」等完成度断言。"
)


def _gap_description_and_reason(gap: dict[str, str] | str) -> tuple[str, str]:
    if isinstance(gap, dict):
        return (
            str(gap.get("description") or "").strip(),
            str(gap.get("reason") or "").strip(),
        )
    return str(gap).strip(), ""


def _worker_gaps_have_hard(
    gaps_by_worker: list[tuple[str, list[dict[str, str]]]] | list[tuple[str, list[str]]],
) -> bool:
    """True when any listed gap is outside ``_SOFT_GAP_REASONS`` (missing reason = hard)."""
    from agentcore.runtime.delegate.delivery_status import _SOFT_GAP_REASONS

    for _, gaps in gaps_by_worker:
        for gap in gaps:
            desc, reason = _gap_description_and_reason(gap)
            if not desc:
                continue
            if reason not in _SOFT_GAP_REASONS:
                return True
    return False


def format_worker_gaps_block(
    gaps_by_worker: list[tuple[str, list[dict[str, str]]]] | list[tuple[str, list[str]]],
) -> str:
    """CEO-facing「契约缺口」section, or "" when nobody has residual gaps.

    Cutoff reasons (token_budget / worker_timeout / degraded_handoff) are listed
    for the CEO's replan / continue decisions. User-facing gap disclosure is owned
    by structured ``delivery_status.gaps`` + the presentation layer — the synopsis
    only gets a light anti-contradiction discipline (no completeness claims).

    Soft-only gaps (``_SOFT_GAP_REASONS``) ban completeness assertions but do **not**
    force「部分交付 / 尚未齐备」. Any hard gap keeps the partial-delivery closing
    copy unchanged.
    """
    from agentcore.runtime.runs.cutoff import CUTOFF_REASONS

    if not gaps_by_worker:
        return ""
    has_cutoff = False
    has_hard = _worker_gaps_have_hard(gaps_by_worker)
    lines = [
        "\n### ⚠️ 契约缺口（请据缺口同图点名补，勿整团重开）\n"
        "以下是各队员收尾后仍未对齐的声明交付物 / 交接缺口（含收敛强制收尾后无法再写文件"
        "留下的缺口，以及预算/超时掐断信号）。优先同一协作图 `replan(add)` +"
        "`replaces_run_id` / `continue_from_run_id` 按缺口点名补；禁止无缺口另开大派，"
        "别假装收工。\n"
    ]
    for label, gaps in gaps_by_worker:
        parts: list[str] = []
        for gap in gaps:
            desc, reason = _gap_description_and_reason(gap)
            if not desc:
                continue
            if reason:
                if reason in CUTOFF_REASONS:
                    has_cutoff = True
                parts.append(f"{desc}〔原因码 {reason}〕")
            else:
                parts.append(desc)
        if parts:
            lines.append(f"- **{label}**：{'；'.join(parts)}")
    lines.append(_HARD_GAPS_CLOSING if has_hard else _SOFT_GAPS_CLOSING)
    if has_cutoff:
        lines.append(
            "结构化交付缺口已由系统对账卡呈现，概览正文不必逐条复述掐断原因；"
            "可建议续派、绑定本机执行环境或 continue_from_run_id。"
        )
    return "\n".join(lines) + "\n"

