"""Multi-agent delivery-status vectors（交付状态结构化：已交付 / 缺口 / 待用户操作）."""

from __future__ import annotations

from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import LLMRateLimitError, mark_llm_leaf_exhausted
from agentcore.llm.errors import error_context_from
from agentcore.runtime.delegate.delivery_status import build_delivery_status
from agentcore.runtime.events import (
    FinishReason,
    SSEEvent,
    content_delta,
    content_reset,
    delivery_status,
    error_event,
    message_end,
    message_start,
    run_completed,
    run_failed,
    run_plan,
    run_started,
    tool_use_end,
    tool_use_start,
)
from agentcore.runtime.runs.error_signal import run_error_signal
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState
from agentcore.tools.builtin.file_ops.integrity import format_artifact_manifest
from agentcore.tools.file_products import product_kind_for_path

from .._common import _CONV, _COST, _USAGE

# 生产实测（trace 933d81fea6cf4b278ee6ce1e0d607e86）：叶层用尽后的限流是
# ``retry_after=None`` / ``cooldown_source=local_backoff``——本地退避猜的秒数
# 不得进用户可见文案，也不得冒充 ErrorContext.retry_after（上游 Retry-After）。
_RATE_LIMIT_RETRY_AFTER = 4.0

# Worker 落盘的三份 CSV（路径即交付账认列；正文只为 file_write 回执逐字对齐生产 manifest）。
# CEO 汇总撞 429 后，把 delegate 交代渲染成回复（降级出口），不再丢成空泡。
_RATE_LIMIT_CEO_DEBRIEF = (
    "已落盘 订单.csv、明细.csv、汇总.csv。数据分析队员因上游限流失败，未完成其余交付。"
)
_RATE_LIMIT_CSV_FILES: tuple[tuple[str, str], ...] = (
    ("订单.csv", "id,amount\n1,100\n"),
    ("明细.csv", "id,sku,qty\n1,A,2\n"),
    ("汇总.csv", "sku,qty\nA,2\n"),
)


def _exhausted_rate_limit_signal():
    """叶层用尽后的限流信号：``exc.retryable`` 已翻 False，但 ``llm_failure_class`` 仍瞬时。

    引擎仍带着本地退避秒数（``self.retry_after``）；来源默认 unknown，文案与
    线上 ``retry_after`` 都不报这个数。
    """
    exc = LLMRateLimitError(retry_after=_RATE_LIMIT_RETRY_AFTER)
    mark_llm_leaf_exhausted(exc)
    return run_error_signal(exc)


def _csv_file_write_events(run_id: str) -> list[SSEEvent]:
    """三连 ``file_write`` —— 回执走生产 ``format_artifact_manifest``，禁手写占位 output。"""
    events: list[SSEEvent] = []
    for i, (path, content) in enumerate(_RATE_LIMIT_CSV_FILES, start=1):
        kind = product_kind_for_path(path)
        events.append(
            tool_use_start(
                f"tc{i}",
                "file_write",
                {"path": path, "content": content},
                run_id=run_id,
            )
        )
        events.append(
            tool_use_end(
                f"tc{i}",
                "file_write",
                success=True,
                output=format_artifact_manifest(
                    path=path,
                    content=content,
                    chars_written=len(content),
                    kind=kind,
                ),
                run_id=run_id,
            )
        )
    return events


def _landed_rate_limit_delivery_payload(error: str) -> dict:
    """FAILED + ``files_touched`` 无验收戳 → 生产 ``build_delivery_status`` 的 partial 账。

    历史 bug：落盘账认 3 个文件、交付账却 blocked / artifacts_count=0。此处不手写 payload，
    直接调产出该事件的后端，回退那条修复时本向量的 delivery_status 会跟着变脏。
    """
    paths = [path for path, _ in _RATE_LIMIT_CSV_FILES]
    plan = RunPlan(nodes=[RunSpec(run_id="r1", task="导出三份 CSV", role="数据分析")])
    results = {
        "r1": RunState(
            phase=RunPhase.FAILED,
            error=error,
            files_touched=paths,
            file_acceptance=[],
        )
    }
    payload = build_delivery_status(plan, results, execution_id="exec_rl")
    if payload is None:
        raise RuntimeError("rate-limit landed worker must emit delivery_status")
    return payload


def _multi_agent_delivery_status_partial() -> list[SSEEvent]:
    """交付对账·部分交付：脚本落盘但可播放 pptx 未生成（云端无执行环境）。

    delivery_status 同 execution_id 保最新——先发 blocked（验收未满足即时对账）、
    后发 partial（补写脚本后的最终对账），fold 只留最后一条；gaps + actions
    （bind_local_folder）随卡重建。
    """
    agents = [
        {
            "id": "w1",
            "role": "课件工程师",
            "thinking": True,
        },
        {
            "id": "w2",
            "role": "讲稿撰写",
            "thinking": True,
        },
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "用 python-pptx 生成课件", "depends_on": []},
        {"id": "r2", "agent_id": "w2", "task": "撰写逐页讲稿", "depends_on": []},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排团队制作课件。"),
        tool_use_start(
            "dc1",
            "delegate",
            {"tasks": [{"role": "课件工程师"}, {"role": "讲稿撰写"}]},
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="生成课件 + 讲稿",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_started("r2", "w2"),
        delivery_status(
            execution_id="exec1",
            state="blocked",
            summary="未能交付：1 项缺口",
            delivered_files=[],
            gaps=[{"role": "验收", "description": "尚无 worker 成功运行 code_execute / test_run 验证代码"}],
            actions=[
                {
                    "kind": "bind_local_folder",
                    "description": (
                        "本回合为云端会话、未装配执行环境：绑定本机执行环境"
                        "（本会话 scratch，≠打开本地项目）后可在本机运行生成。"
                    ),
                }
            ],
        ),
        run_completed(
            "r1",
            "w1",
            output_summary="生成脚本已落盘（未运行验证）",
            duration_ms=1200,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
            output_files=["build_pptx.py"],
        ),
        run_completed(
            "r2",
            "w2",
            output_summary="逐页讲稿已落盘",
            duration_ms=900,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
            output_files=["讲稿.md"],
        ),
        delivery_status(
            execution_id="exec1",
            state="partial",
            summary="已交付 2 个文件；1 项缺口",
            delivered_files=["build_pptx.py", "讲稿.md"],
            gaps=[
                {
                    "role": "课件工程师",
                    "description": "course.pptx 未生成（云端无执行环境，脚本未运行）",
                    "reason": "token_budget",
                }
            ],
            actions=[
                {
                    "kind": "bind_local_folder",
                    "description": (
                        "本回合为云端会话、未装配执行环境：绑定本机执行环境"
                        "（本会话 scratch，≠打开本地项目）后可在本机运行生成。"
                    ),
                }
            ],
        ),
        tool_use_end("dc1", "delegate", success=True, output="团队已完成。"),
        content_delta("脚本与讲稿已就绪；pptx 需绑定本机执行环境后在本机生成。"),
        message_end(FinishReason.END_TURN, input_tokens=2000, output_tokens=400, cost=_COST),
    ]


def _multi_agent_export_docx_artifacts() -> list[SSEEvent]:
    """交付台账·导出件：写 md 再导出 docx，两件都进 ``artifacts``（首条非空产物向量）。

    真实事故形状：worker ``file_write`` 起诉状 md → ``md_to_docx`` 导出真实 .docx；客户端
    只认 ``delivery_status.artifacts``，而两个工具的**入参都只有那份 md**——docx 只存在于
    工具自报的产物里。故本向量钉死 wire 侧的两件事：导出件自成一行（计数不再是 1），且
    它带 ``derived_from`` 指向源 md（客户端据此把源折成中间稿；``kind`` 同为自报）。
    """
    md = "抚养费起诉状-昝雯.md"
    docx = "抚养费起诉状-昝雯.docx"
    agents = [{"id": "w1", "role": "文书撰写", "thinking": True}]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "起草抚养费起诉状并导出 Word", "depends_on": []},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排起草起诉状并导出 Word。"),
        tool_use_start("dc1", "delegate", {"tasks": [{"role": "文书撰写"}]}),
        run_plan(
            execution_id="exec_docx",
            plan_type="multi_agent",
            task_summary="抚养费起诉状（Word）",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        tool_use_start(
            "tc1",
            "file_write",
            {"path": md, "content": "# 民事起诉状\n\n原告：昝雯……"},
            run_id="r1",
        ),
        tool_use_end("tc1", "file_write", success=True, output="已写入", run_id="r1"),
        # 导出工具的入参也只有源 md——.docx 这个路径只从工具自报的产物来。
        tool_use_start("tc2", "md_to_docx", {"path": md}, run_id="r1"),
        tool_use_end(
            "tc2",
            "md_to_docx",
            success=True,
            output=(
                f"已导出 Word：{docx}（38964 字节）\n"
                "【artifact manifest】\n"
                f"path: {docx}\n"
                "kind: docx\n"
                "bytes: 38964\n"
                f"source: {md}\n"
                "warnings: （无）\n"
                "【验真】请以本 manifest 确认落盘；可用工作区下载打开 .docx。"
            ),
            run_id="r1",
        ),
        run_completed(
            "r1",
            "w1",
            output_summary="起诉状已成稿并导出 Word",
            duration_ms=2400,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
            output_files=[md, docx],
        ),
        delivery_status(
            execution_id="exec_docx",
            state="delivered",
            summary="已交付 2 个文件",
            delivered_files=[md, docx],
            gaps=[],
            actions=[],
            artifacts=[
                {"path": md, "status": "accepted", "kind": "md"},
                {
                    "path": docx,
                    "status": "accepted",
                    "kind": "docx",
                    "derived_from": md,
                },
            ],
        ),
        tool_use_end("dc1", "delegate", success=True, output="团队完成 1 项任务。"),
        content_delta(f" Word 版起诉状已生成：`{docx}`。"),
        message_end(FinishReason.END_TURN, input_tokens=2600, output_tokens=460, cost=_COST),
    ]


def _multi_agent_pptx_promised_md_only() -> list[SSEEvent]:
    """选 pptx 却只落 md/脚本：部分交付卡可见；假「PPT 已可打开」经 finish_guard 回炉。

    前置假定用户已在开工卡选定 format_id=f0（PowerPoint）；本向量钉交付诚实性——
    delivery_status=partial（无 .pptx）+ 违规终稿被 content_reset(finish_guard) 丢掉，
    修正为承认缺口。对照 ``multi_agent_delivery_status_partial``（诚实终稿、无回炉）。
    """
    agents = [
        {"id": "w1", "role": "课件工程师", "thinking": True},
        {"id": "w2", "role": "讲稿撰写", "thinking": True},
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "生成 PowerPoint（.pptx）课件", "depends_on": []},
        {"id": "r2", "agent_id": "w2", "task": "撰写逐页讲稿", "depends_on": []},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("已按你选的 PowerPoint（.pptx）安排团队。"),
        tool_use_start(
            "dc1",
            "delegate",
            {"tasks": [{"role": "课件工程师"}, {"role": "讲稿撰写"}]},
        ),
        run_plan(
            execution_id="exec_pptx",
            plan_type="multi_agent",
            task_summary="生成 pptx 课件 + 讲稿",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_started("r2", "w2"),
        run_completed(
            "r1",
            "w1",
            output_summary="仅落盘生成脚本（未产出 .pptx）",
            duration_ms=1100,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
            output_files=["build_pptx.py"],
        ),
        run_completed(
            "r2",
            "w2",
            output_summary="逐页讲稿已落盘",
            duration_ms=800,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
            output_files=["讲稿.md"],
        ),
        delivery_status(
            execution_id="exec_pptx",
            state="partial",
            summary="已交付 2 个文件；1 项缺口",
            delivered_files=["build_pptx.py", "讲稿.md"],
            gaps=[
                {
                    "role": "课件工程师",
                    "description": "用户选定 PowerPoint（.pptx），但 course.pptx 未落盘（仅有脚本与讲稿）",
                    "reason": "files_not_landed",
                }
            ],
            actions=[
                {
                    "kind": "bind_local_folder",
                    "description": "绑定本机执行环境后可在本机运行 build_pptx.py 生成 .pptx。",
                }
            ],
        ),
        tool_use_end("dc1", "delegate", success=True, output="团队产出已汇总。"),
        content_delta("课件 PPT 已落盘，可直接打开使用。"),
        content_reset("finish_guard"),
        content_delta("讲稿与生成脚本已就绪；pptx 尚未生成，请绑定本机执行环境后运行脚本。"),
        message_end(FinishReason.END_TURN, input_tokens=2100, output_tokens=420, cost=_COST),
    ]


def _multi_agent_worker_rate_limit_partial() -> list[SSEEvent]:
    """委派回合：一个 worker 落盘 3 个 CSV 后撞上游 429；CEO 汇总再撞 429。

    生产实测 trace ``933d81fea6cf4b278ee6ce1e0d607e86``。钉四处刚落地的修复：

    1. 该 worker 全链路只有一帧终态（历史：同 ``run_id`` 两帧 ``run_failed``，fold
       last-write-wins → 直播 2 帧、重载 1 帧）。
    2. ``run_failed`` 带 ``error_code=LLM_RATE_LIMIT`` / ``retryable=true``
       （叶层用尽后限流仍是瞬时；判据 ``llm_failure_class``，不是
       ``exc.retryable``）。未 attested 的退避秒数不进 ``retry_after``。
    3. ``delivery_status`` 为 partial 且认到 3 个产物（历史：blocked / artifacts_count=0）。
    4. CEO 汇总 429 后把 delegate 交代渲染成回复（``outcome=partial``，正文非空）；
       仍保留 ``error`` SSE。引擎 salvage 后 ``finish_reason=degraded``。
    """
    signal = _exhausted_rate_limit_signal()
    error = str(signal.exc)
    ds = _landed_rate_limit_delivery_payload(error)
    return [
        message_start("m1", conversation_id=_CONV),
        # CEO 首轮直接 delegate，汇总轮再撞 429 → 气泡正文为空（失败脸是唯一用户面）。
        tool_use_start("dc1", "delegate", {"tasks": [{"role": "数据分析"}]}),
        run_plan(
            execution_id="exec_rl",
            plan_type="multi_agent",
            task_summary="导出三份 CSV",
            agents=[{"id": "w1", "role": "数据分析", "thinking": True}],
            runs=[{"id": "r1", "agent_id": "w1", "task": "导出三份 CSV", "depends_on": []}],
        ),
        run_started("r1", "w1"),
        *_csv_file_write_events("r1"),
        run_failed(
            "r1",
            "w1",
            error,
            failure_kind="call",
            execution_id="exec_rl",
            product_landed=True,
            error_code=signal.error_code or ErrorCode.LLM_RATE_LIMIT,
            retryable=signal.retryable,
            retry_after=signal.retry_after,
        ),
        delivery_status(
            execution_id=ds["execution_id"],
            state=ds["state"],
            summary=ds["summary"],
            delivered_files=list(ds["delivered_files"]),
            gaps=list(ds["gaps"]),
            actions=list(ds["actions"]),
            artifacts=list(ds["artifacts"]),
        ),
        tool_use_end(
            "dc1",
            "delegate",
            success=True,
            output=_RATE_LIMIT_CEO_DEBRIEF,
            partial_failure=True,
        ),
        error_event(
            ErrorCode.LLM_RATE_LIMIT,
            error,
            context=error_context_from(signal.exc),
        ),
        content_delta(_RATE_LIMIT_CEO_DEBRIEF),
        message_end(
            FinishReason.DEGRADED,
            input_tokens=2000,
            output_tokens=80,
            cost=_COST,
            outcome="partial",
        ),
    ]


def _multi_agent_ceo_rate_limit_paused() -> list[SSEEvent]:
    """委派回合：worker 落盘后撞 429；delegate 已闭合；CEO 汇总再撞 429 → 暂停可续。

    与 ``multi_agent_worker_rate_limit_partial`` 对照：那条是 CEO 收口成功的
    partial；本条是无 attested 短冷却 / 冷却过长后的 CEO 限流，回合权威
    ``outcome=paused``，无 ``*_required``、无系统收口用户行。
    """
    signal = _exhausted_rate_limit_signal()
    error = str(signal.exc)
    ds = _landed_rate_limit_delivery_payload(error)
    return [
        message_start("m1", conversation_id=_CONV),
        tool_use_start("dc1", "delegate", {"tasks": [{"role": "数据分析"}]}),
        run_plan(
            execution_id="exec_rl",
            plan_type="multi_agent",
            task_summary="导出三份 CSV",
            agents=[{"id": "w1", "role": "数据分析", "thinking": True}],
            runs=[{"id": "r1", "agent_id": "w1", "task": "导出三份 CSV", "depends_on": []}],
        ),
        run_started("r1", "w1"),
        *_csv_file_write_events("r1"),
        run_failed(
            "r1",
            "w1",
            error,
            failure_kind="call",
            execution_id="exec_rl",
            product_landed=True,
            error_code=signal.error_code or ErrorCode.LLM_RATE_LIMIT,
            retryable=signal.retryable,
            retry_after=signal.retry_after,
        ),
        delivery_status(
            execution_id=ds["execution_id"],
            state=ds["state"],
            summary=ds["summary"],
            delivered_files=list(ds["delivered_files"]),
            gaps=list(ds["gaps"]),
            actions=list(ds["actions"]),
            artifacts=list(ds["artifacts"]),
        ),
        tool_use_end(
            "dc1",
            "delegate",
            success=True,
            output=_RATE_LIMIT_CEO_DEBRIEF,
            partial_failure=True,
        ),
        error_event(
            ErrorCode.LLM_RATE_LIMIT,
            error,
            context=error_context_from(signal.exc),
        ),
        content_delta(_RATE_LIMIT_CEO_DEBRIEF),
        message_end(
            FinishReason.PAUSED,
            input_tokens=2000,
            output_tokens=80,
            cost=_COST,
            outcome="paused",
        ),
    ]
