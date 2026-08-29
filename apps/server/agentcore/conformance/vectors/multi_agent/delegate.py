"""Multi-agent delegate / worker deliverable vectors."""

from __future__ import annotations

from agentcore.runtime.events import (
    FinishReason,
    SSEEvent,
    content_delta,
    message_end,
    message_start,
    run_completed,
    run_failed,
    run_output_delta,
    run_output_reset,
    run_plan,
    run_progress,
    run_reasoning_delta,
    run_started,
    run_tool_progress,
    tool_use_end,
    tool_use_start,
)

from .._common import _CONV, _COST, _USAGE


def _multi_agent_delegate() -> list[SSEEvent]:
    agents = [
        {
            "id": "w1",
            "role": "研究员",
            "thinking": True,
        },
        {
            "id": "w2",
            "role": "撰写员",
            "thinking": True,
        },
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "调研", "depends_on": []},
        {"id": "r2", "agent_id": "w2", "task": "撰写", "depends_on": ["r1"]},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排团队。"),
        # The CEO's `delegate` tool call: in production it emits a top-level
        # tool_use_start (before run_plan) and resolves after the team finishes — this
        # `delegate` step is where the client slots the inline team graph (统一团队时间线).
        # 专测阻塞 wire（runs 树 / 进度 / 总账）：显式 coordinate=false，保经典阻塞 golden。
        tool_use_start(
            "dc1",
            "delegate",
            {"tasks": [{"role": "研究员"}, {"role": "撰写员"}], "coordinate": False},
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="构建 X",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_output_delta("r1", "w1", "调研结论"),
        # 完工交接简报: r1 submitted a 交接简报 via the handoff tool — its summary(结论) is the display
        # summary, the structured brief rides run_completed (surfaced in the run-detail 摘要). The
        # output stays the pure deliverable — the brief is never mixed into the prose.
        run_completed(
            "r1",
            "w1",
            output_summary="完成调研",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
            debrief={
                "summary": "完成调研",
                "key_points": ["横评了主流方案 A/B/C", "方案 A 综合成本最低"],
                "assumptions": "按团队现有技术栈评估",
                "next_steps": "由撰写员据此产出定稿",
            },
        ),
        # 进度里程碑 (run_progress): the WaveScheduler ticks completed/total as each run lands.
        # Inert in ProjectedTurn (progress is derived from run states — the wire counter is a
        # timeline marker only); emitted here so the coverage gate proves all three folds no-op
        # it identically rather than diverging on a stray counter.
        run_progress(1, 2),
        run_started("r2", "w2"),
        run_output_delta("r2", "w2", "成稿"),
        # r2 never called handoff → debrief absent (the null-degrade branch): the run-detail
        # falls back to the full 输出, and output_summary stays a plain scan line.
        run_completed(
            "r2",
            "w2",
            output_summary="完成撰写",
            duration_ms=1200,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_progress(2, 2),
        # delegate resolves only after the whole team finishes (it blocks the CEO round).
        tool_use_end("dc1", "delegate", success=True, output="团队完成 2 项任务。"),
        content_delta(" 团队已完成。"),
        message_end(FinishReason.END_TURN, input_tokens=4000, output_tokens=800, cost=_COST),
    ]

def _multi_agent_worker_failed_debrief() -> list[SSEEvent]:
    """多 Agent：worker 未过契约（run_failed）但仍调 handoff 提交了交接简报——失败节点也 surface 交接简报。
    验 run_failed 携 debrief 折到 run.debrief（run 详情在错误旁展示作者结论 + 建议下一步），
    而不是让失败运行只剩一条错误。progress = 0/1（失败终态不计入 completed）。"""
    agents = [
        {
            "id": "w1",
            "role": "研究员",
            "thinking": True,
        },
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "调研", "depends_on": []},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排调研。"),
        tool_use_start("dc1", "delegate", {"tasks": [{"role": "研究员"}]}),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="调研 X",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_output_delta("r1", "w1", "初步调研，但缺少必需的引用来源"),
        # 契约未过但产出 + handoff 交接简报仍在：run_failed 携 debrief，run 详情在错误旁展示作者结论。
        run_failed(
            "r1",
            "w1",
            "未通过契约：缺少必需的引用来源",
            failure_kind="quality",
            debrief={
                "summary": "完成初步调研，但未满足引用契约",
                "key_points": ["覆盖了三个主流方案", "引用来源缺失，结论待核实"],
                "assumptions": "暂按公开资料整理",
                "next_steps": "补齐权威引用后再定稿",
            },
        ),
        tool_use_end("dc1", "delegate", success=True, output="团队完成（含 1 项未达标）。"),
        content_delta(" 调研初步完成，但引用需补齐。"),
        message_end(FinishReason.END_TURN, input_tokens=2000, output_tokens=400, cost=_COST),
    ]


def _multi_agent_worker_failed_format() -> list[SSEEvent]:
    """多 Agent：worker 结构/格式闸失败 → run_failed.failure_kind=format（协作图「格式未过」）。
    与 quality「未达标」分脸；棘轮 fold 投影 ``run.failureKind=format``。"""
    agents = [
        {
            "id": "w1",
            "role": "工程师",
            "thinking": True,
        },
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "写审计报告", "depends_on": []},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排审计报告。"),
        tool_use_start("dc1", "delegate", {"tasks": [{"role": "工程师"}]}),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="写审计报告",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_output_delta("r1", "w1", '{"findings":[{"severity":"INVALID"}]}'),
        run_failed(
            "r1",
            "w1",
            "结构闸：findings[0] severity 无效",
            failure_kind="format",
        ),
        tool_use_end("dc1", "delegate", success=True, output="团队完成（含 1 项格式未过）。"),
        content_delta(" 结构字段需按 schema 补齐。"),
        message_end(FinishReason.END_TURN, input_tokens=2000, output_tokens=400, cost=_COST),
    ]


def _multi_agent_worker_tool() -> list[SSEEvent]:
    """多 Agent：worker 工具调用。worker 的 ``tool_use_start/end`` 与 CEO 的同形地走顶层流，
    但**携 ``run_id``**——三端 process fold 据此把它**排除出 CEO 气泡时间线**（统一团队时间线
    只放 CEO 自己的步骤）；归属落到该 run 的 ``ProjectedRun.process``（与 live / 重开一致）。
    本向量验「worker 工具不串进 CEO ``process``」且「worker process 含 tool→content」：
    ``process`` 只剩 CEO 正文「我来分工。」；r1.process = [tool, content]。
    ``run_tool_progress`` 是唯一持久可观测（→ ``agent.toolProgress``）。末尾不发 ``message_end``：
    w2 停在「正在生成」快照，故其 ``toolProgress`` 可见。"""
    agents = [
        {
            "id": "w1",
            "role": "工程师",
            "thinking": True,
        },
        {
            "id": "w2",
            "role": "测试员",
            "thinking": True,
        },
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "写代码", "depends_on": []},
        {"id": "r2", "agent_id": "w2", "task": "跑测试", "depends_on": ["r1"]},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来分工。"),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="实现 + 测试",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_tool_progress("r1", "w1", "file_write", 1200),
        tool_use_start("tc1", "file_write", {"path": "a.py", "content": "print(1)"}, run_id="r1"),
        tool_use_end("tc1", "file_write", success=True, output="已写入", run_id="r1"),
        run_output_delta("r1", "w1", "代码就绪"),
        run_completed(
            "r1",
            "w1",
            output_summary="实现完成",
            duration_ms=1500,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started("r2", "w2"),
        run_tool_progress("r2", "w2", "code_execute", 64),
    ]


def _multi_agent_worker_process_timeline() -> list[SSEEvent]:
    """多 Agent：worker per-run process 时间线 live 交错（思考→工具→正文→工具→正文）。

    钉住 ``ProjectedRun.process`` 与 EventSink / 三端 fold 同序——重开对话回放不得退化成
    ``message_final`` 粗合成（先全文思考再全文输出）。CEO ``process`` 仅 ``team`` + 收尾正文。
    """
    agents = [
        {
            "id": "w1",
            "role": "调研员",
            "thinking": True,
        },
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "调研竞品", "depends_on": []},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("交给调研员。"),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="调研竞品",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_reasoning_delta("r1", "w1", "先搜一圈。"),
        tool_use_start("tc1", "web_search", {"query": "竞品定价"}, run_id="r1"),
        tool_use_end("tc1", "web_search", success=True, output="命中 3 条", run_id="r1"),
        run_output_delta("r1", "w1", "初步结论：价格带偏高。"),
        run_reasoning_delta("r1", "w1", "再读一篇。"),
        tool_use_start("tc2", "read_url", {"url": "https://example.com"}, run_id="r1"),
        tool_use_end("tc2", "read_url", success=True, output="正文…", run_id="r1"),
        run_output_delta("r1", "w1", " 最终建议：跟价。"),
        run_completed(
            "r1",
            "w1",
            output_summary="完成调研",
            duration_ms=2000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        content_delta(" 调研已完成。"),
        message_end(FinishReason.END_TURN, input_tokens=800, output_tokens=200, cost=_COST),
    ]

def _multi_agent_worker_output_reset() -> list[SSEEvent]:
    """多 Agent·交付前核验回炉 (finish_guard) 统一底线：worker done 轮结构缺陷
    （声明 json 却空体围栏）→ ``run_output_reset`` 清卡片已流式草稿 → 重写修正版。
    三端 fold + oracle 必须一致：清 agent output/outputChunks，reasoning 保留；
    无 ``content_reset``（CEO 气泡不受影响）。"""
    agents = [
        {
            "id": "w1",
            "role": "工程师",
            "thinking": True,
        },
    ]
    plan_runs = [{"id": "r1", "agent_id": "w1", "task": "起草结构化产出", "depends_on": []}]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排队员起草。"),
        tool_use_start("dc1", "delegate", {"tasks": [{"role": "工程师"}]}),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="起草结构化产出",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_reasoning_delta("r1", "w1", "先起草 JSON 结构。"),
        run_output_delta("r1", "w1", "草稿：\n```json\n```"),
        run_output_reset("r1", "w1", "finish_guard"),
        run_output_delta("r1", "w1", "修正后的产出：{\"status\":\"ok\"}"),
        run_completed(
            "r1",
            "w1",
            output_summary="修正后产出完成",
            duration_ms=1100,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end("dc1", "delegate", success=True, output="队员已修正产出。"),
        content_delta("队员已修正产出。"),
        message_end(FinishReason.END_TURN, input_tokens=2800, output_tokens=420, cost=_COST),
    ]


def _multi_agent_worker_deliverable_reset() -> list[SSEEvent]:
    """多 Agent·交付正文只留最终交付、旁白入 journal (Fork-B, 全队对称)：worker 在调【非终止】工具
    前写了一段旁白（"我先看下现状。"），引擎判定这是过程旁白而非交付 → 回退交付正文并发一次
    ``run_output_reset`` 清掉卡片已流式的旁白 → 工具后重累积【最终交付】。

    与 ``worker_output_reset``（finish_guard 结构缺陷回炉）同用 ``run_output_reset`` 机制，但
    reason=``narration``（非 finish_guard）：三端 fold + oracle 必须一致地只清卡片草稿、
    不折过程痕迹。清 agent output 标量（重累积最终
    交付），reasoning 是真实过程、保留；旁白只活在 journal 的 llm_call fact 里，不进
    message_final / 卡片重载 / CEO 综述输入。故 r1 的 ``output`` 末态只剩「结论：应采用方案 A。」。"""
    agents = [
        {
            "id": "w1",
            "role": "工程师",
            "thinking": True,
        },
    ]
    plan_runs = [{"id": "r1", "agent_id": "w1", "task": "调研现状并给结论", "depends_on": []}]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排队员调研。"),
        tool_use_start("dc1", "delegate", {"tasks": [{"role": "工程师"}]}),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="调研并给结论",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_reasoning_delta("r1", "w1", "先看现状再下结论。"),  # 思考：真实过程，保留
        run_output_delta("r1", "w1", "我先看下现状。"),  # 调工具前的旁白（将被 reset 清掉）
        tool_use_start("tc1", "grep", {"pattern": "x"}, run_id="r1"),
        tool_use_end("tc1", "grep", success=True, output="命中 3 处", run_id="r1"),
        # 引擎回退交付正文、发 run_output_reset 清卡片旁白（直播==重载==最终交付）。
        # reason=narration：正常旁白归档，fold 只清草稿、不折过程痕迹。
        run_output_reset("r1", "w1", "narration"),
        run_output_delta("r1", "w1", "结论：应采用方案 A。"),  # 最终交付
        run_completed(
            "r1",
            "w1",
            output_summary="完成调研",
            duration_ms=1300,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end("dc1", "delegate", success=True, output="队员已给出结论。"),
        content_delta(" 队员已给出结论。"),
        message_end(FinishReason.END_TURN, input_tokens=3000, output_tokens=500, cost=_COST),
    ]
