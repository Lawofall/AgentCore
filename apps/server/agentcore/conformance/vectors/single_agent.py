"""Conformance vector builders — single-agent chat scenarios.

See ``vectors/__init__.py`` for the aggregated ``VECTORS`` registry.
"""

from __future__ import annotations

from collections.abc import Callable

from agentcore.core.errors import ErrorCode
from agentcore.runtime.events import (
    FinishReason,
    SSEEvent,
    checkpoint_required,
    citations_event,
    content_delta,
    content_reset,
    error_event,
    message_end,
    message_start,
    reasoning_delta,
    run_completed,
    run_context,
    run_started,
    title_generated,
    tool_use_end,
    tool_use_progress,
    tool_use_start,
    turn_saved,
    turn_warning,
)
from agentcore.runtime.events.attach_replay import replay_close_event, replay_open_event
from agentcore.workspace.limits import CHANNEL_DEAD_PREPARE_ABORT

from ._common import _CONV, _COST, _USAGE, _ctx_block


def _single_agent_text() -> list[SSEEvent]:
    return [
        message_start("m1", conversation_id=_CONV),
        reasoning_delta("先想一下。"),
        reasoning_delta("好的。"),
        content_delta("你好"),
        content_delta("，世界！"),
        message_end(FinishReason.END_TURN, input_tokens=1200, output_tokens=300, cost=_COST),
    ]


def _single_agent_queued_then_run() -> list[SSEEvent]:
    """发送即有流 · FIFO 排队→开跑→续流：``turn_queued`` → ``turn_queue_started`` → 单聊。

    此前此态从未被向量覆盖（HTTP 202 JSON 退役前客户端拿不到续流事件），是协议 bug
    逃过 CI 的根因。排队 EPHEMERAL / fold no-op，golden 与纯单聊同形。
    """
    from agentcore.runtime.events import turn_queue_started, turn_queued

    return [
        turn_queued(
            queue_id="q1",
            position=1,
            queue_depth=1,
            conversation_id=_CONV,
        ),
        turn_queue_started(
            queue_id="q1",
            conversation_id=_CONV,
            remaining_depth=0,
            content="next",
        ),
        *_single_agent_text(),
    ]


def _single_agent_queued_degraded_from_steer() -> list[SSEEvent]:
    """经典 in-flight + ``delivery=steer`` 回落：无 accepting 窗口时 ``turn_queued.degraded_from=steer``。

    EPHEMERAL / fold no-op；golden 与纯单聊同形。客户端据此 toast「已改为排队」。
    开跑仍带 ``turn_queue_started``（与强制 queue 同形闭环）。
    """
    from agentcore.runtime.events import turn_queue_started, turn_queued

    return [
        turn_queued(
            queue_id="q-degraded",
            position=1,
            queue_depth=1,
            conversation_id=_CONV,
            degraded_from="steer",
        ),
        turn_queue_started(
            queue_id="q-degraded",
            conversation_id=_CONV,
            remaining_depth=0,
            content="next",
        ),
        *_single_agent_text(),
    ]


def _single_agent_user_interjection_steer() -> list[SSEEvent]:
    """经典 in-flight + ``delivery=steer`` 全链：``user_interjection`` received→injected。

    DURABLE；同 id 保最新 → golden ``userInterjections`` status=injected（经典终态）。
    插话穿插在已开流的回合中间（``message_start`` 之后）——经典 steer 定义上只发生在
    回合流式输出期间，与协调插话向量同序；勿把它排到 ``message_start`` 之前。
    """
    from agentcore.runtime.events import user_interjection

    return [
        message_start("m1", conversation_id=_CONV),
        reasoning_delta("先想一下。"),
        content_delta("你好"),
        user_interjection(
            interjection_id="inj-steer-1",
            execution_id="exec-classic-1",
            content="改成用中文总结",
            status="received",
        ),
        user_interjection(
            interjection_id="inj-steer-1",
            execution_id="exec-classic-1",
            content="改成用中文总结",
            status="injected",
        ),
        content_delta("，世界！"),
        message_end(FinishReason.END_TURN, input_tokens=1200, output_tokens=300, cost=_COST),
    ]


def _single_agent_user_interjection_steer_queued() -> list[SSEEvent]:
    """经典 steer 收口 leftover 降级：received→queued + ``turn_queued.degraded_from=steer``。

    双发对齐协调升队先例；queued 为经典终态之一（无 addressed）。
    收口升队发生在回合 finally，故排在正文之后、``message_end`` 之前。
    """
    from agentcore.runtime.events import turn_queued, user_interjection

    return [
        message_start("m1", conversation_id=_CONV),
        reasoning_delta("先想一下。"),
        content_delta("你好"),
        user_interjection(
            interjection_id="inj-steer-q",
            execution_id="exec-classic-1",
            content="晚到的纠偏",
            status="received",
        ),
        content_delta("，世界！"),
        user_interjection(
            interjection_id="inj-steer-q",
            execution_id="exec-classic-1",
            content="晚到的纠偏",
            status="queued",
            note="当前回合已收口，已自动转入下一回合",
        ),
        turn_queued(
            queue_id="q-steer-leftover",
            position=1,
            queue_depth=1,
            conversation_id=_CONV,
            degraded_from="steer",
        ),
        message_end(FinishReason.END_TURN, input_tokens=1200, output_tokens=300, cost=_COST),
    ]


def _single_agent_queue_cancelled() -> list[SSEEvent]:
    """排队项取消：``turn_queued`` 后 ``turn_queue_cancelled``（同 queue_id）；均为 EPHEMERAL。"""
    from agentcore.runtime.events import turn_queue_cancelled, turn_queued

    return [
        turn_queued(
            queue_id="q-cancel",
            position=1,
            queue_depth=1,
            conversation_id=_CONV,
        ),
        turn_queue_cancelled(queue_id="q-cancel", conversation_id=_CONV),
        *_single_agent_text(),
    ]


def _single_agent_tool() -> list[SSEEvent]:
    return [
        message_start("m1", conversation_id=_CONV),
        reasoning_delta("我先搜索。"),
        tool_use_start("tc1", "web_search", {"query": "AgentCore"}),
        tool_use_end("tc1", "web_search", success=True, output="找到 3 条结果。"),
        content_delta("根据搜索，"),
        content_delta("答案如下。"),
        message_end(FinishReason.END_TURN, input_tokens=1500, output_tokens=200, cost=_COST),
    ]

def _single_agent_consult_memory() -> list[SSEEvent]:
    """单聊：CEO 翻开一条记忆主题笔记 (记忆文件夹化 §六 · consult_memory 渐进披露 可视化)。系统
    提示词的「记忆主题目录」列主题名＋一行摘要；CEO 判断「部署流程」与当前任务相关 → 调
    ``consult_memory(name=部署流程)`` 把该主题笔记**全文**拉回（``tool_use_end`` 携 ``display.topic``
    + ``result`` 正文），据此作答。consult_memory 是 CEO 召回原语、**不在** ORCHESTRATION_TOOLS
    丢弃集（那只含 delegate/debate），故它照常落一个 ``tool`` 步——三端 process fold + oracle 据
    ``display.topic`` 渲染成「查阅记忆：<主题>」卡片 + 可展开全文（镜像 consult_skill 的查阅卡）。"""
    note = (
        "## 部署流程\n"
        "- 前端：pnpm dev 起桌面壳\n"
        "- 服务端：uv run python -m agentcore\n"
        "- 数据库：本地 Postgres，迁移 alembic upgrade head\n"
    )
    return [
        message_start("m1", conversation_id=_CONV),
        reasoning_delta("这事和部署有关，先翻一下记忆里的部署流程。"),
        tool_use_start("tc1", "consult_memory", {"name": "部署流程"}),
        tool_use_end(
            "tc1",
            "consult_memory",
            success=True,
            output=note,
            display={"topic": "部署流程"},
        ),
        content_delta("按你记录的部署流程，"),
        content_delta("先 pnpm dev 起壳，再 uv run 起服务端即可。"),
        message_end(FinishReason.END_TURN, input_tokens=1400, output_tokens=180, cost=_COST),
    ]

def _single_agent_error() -> list[SSEEvent]:
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("开始处理"),
        error_event("llm_error", "模型超时"),
    ]


def _single_agent_tool_failure() -> list[SSEEvent]:
    """单聊：工具执行失败（``tool_use_end`` ``success=False`` → wire ``status=error``）。

    生产 join 点把 ``error``+``output`` 的技术细节放进模型面 ``result``（可含主机/类名），
    用户面走可选 ``failure: {message, code}``。本向量钉住**生产真实形状**（技术 result +
    产品 failure），避免再用产品句冒充 result 让泄漏在全绿 CI 下漏网。
    """
    return [
        message_start("m1", conversation_id=_CONV),
        reasoning_delta("先搜一下。"),
        tool_use_start("tc1", "web_search", {"query": "AgentCore"}),
        tool_use_end(
            "tc1",
            "web_search",
            success=False,
            output=(
                "搜索失败：ConnectError: [Errno 111] Connection refused "
                "to searxng.internal:8080"
            ),
            failure={
                "message": "本地搜索服务不可用，请稍后重试",
                "code": "searxng_unreachable",
            },
        ),
        content_delta("检索失败了，"),
        content_delta("我先按已有知识回答。"),
        message_end(FinishReason.END_TURN, input_tokens=1100, output_tokens=160, cost=_COST),
    ]


def _single_agent_cancelled() -> list[SSEEvent]:
    """单聊：用户取消（``message_end(FinishReason.CANCELLED)``）。

    生产 ``/stop`` / turn_persistence 对挂着的 SSE 先发 live ``message_end(cancelled)``；
    与 ``reload_interrupted_partial``（lease salvage → interrupted）对偶——本向量钉住
    **用户主动停止** 的 finish_reason=cancelled → status=cancelled，半截正文保留。
    """
    return [
        message_start("m1", conversation_id=_CONV),
        reasoning_delta("先梳理要点。"),
        content_delta("根据目前信息，"),
        content_delta("建议分三步："),
        message_end(
            FinishReason.CANCELLED,
            input_tokens=600,
            output_tokens=40,
            cost=_COST,
        ),
    ]


def _single_agent_tool_progress() -> list[SSEEvent]:
    """单聊：工具执行阶段进度（``tool_use_progress``，非 ``tool_progress``）。

    生产 ``tool_exec`` 在 ``tool_use_start``…``tool_use_end`` 之间经 ``on_phase`` 发
    transport-only ``tool_use_progress``（web_search → querying/queued/fallback）；
    ``tool_progress`` 是参数流式心跳，worker 侧对偶是 ``run_tool_progress``。本事件
    不进 journal / ProjectedTurn process（EPHEMERAL），但向量须带真字段，保证三端 fold
    对未知 phase 不崩、序列与生产一致。
    """
    return [
        message_start("m1", conversation_id=_CONV),
        reasoning_delta("我先搜索。"),
        tool_use_start("tc1", "web_search", {"query": "AgentCore"}),
        tool_use_progress("tc1", "web_search", "querying"),
        tool_use_progress("tc1", "web_search", "queued"),
        tool_use_end("tc1", "web_search", success=True, output="找到 3 条结果。"),
        content_delta("根据搜索，"),
        content_delta("答案如下。"),
        message_end(FinishReason.END_TURN, input_tokens=1500, output_tokens=200, cost=_COST),
    ]


def _single_agent_title_and_turn_saved() -> list[SSEEvent]:
    """单聊：回合 chrome——``turn_saved``（用户消息落库）+ ``title_generated``（早标题）。

    生产：``turn_saved`` 在 ``message_start`` 之前；``title_generated`` 与回合并行、常在
    ``message_end`` 前后到达。二者均不进 ProjectedTurn（DERIVED / chrome），fold no-op；
    向量钉住事件名与 payload 键，避免客户端误把 chrome 当判定态。
    """
    return [
        turn_saved(user_message_id="u1"),
        message_start("m1", conversation_id=_CONV),
        content_delta("你好，"),
        content_delta("已收到。"),
        message_end(FinishReason.END_TURN, input_tokens=400, output_tokens=60, cost=_COST),
        title_generated("问候与确认", conversation_id=_CONV),
    ]

def _single_agent_citations() -> list[SSEEvent]:
    return [
        message_start("m1", conversation_id=_CONV),
        reasoning_delta("先查资料。"),
        tool_use_start("tc1", "web_search", {"query": "AgentCore 架构"}),
        tool_use_end("tc1", "web_search", success=True, output="找到来源。"),
        content_delta("综合来看，"),
        content_delta("结论是 X。"),
        citations_event(
            [
                {
                    "url": "https://a.example/x",
                    "title": "来源 A",
                    "snippet": "片段 A",
                    "site": "a.example",
                    "id": "#r1",
                    "tier": "unknown",
                },
                {
                    "url": "https://www.bjnews.com.cn/detail/1.html",
                    "title": "来源 B · 新京报",
                    "snippet": "片段 B",
                    "site": "bjnews.com.cn",
                    "id": "#r2",
                    "tier": "media",
                },
            ]
        ),
        message_end(FinishReason.END_TURN, input_tokens=1800, output_tokens=260, cost=_COST),
    ]

def _single_agent_web_read() -> list[SSEEvent]:
    """单聊·联网检索与深读的富渲染 (工具结果富渲染 · read_url display + 工具组合并)：
    web_search 出来源卡片列表；单条 read_url 出「来源头 + 正文」卡片（display 携
    url/title/site/snippet/content）；≥2 条连续 read_url 折叠成来源集合（favicon pill +
    「读取网页 · N 个来源」/ 展开来源列表，无内联正文）。钉住三端 process fold 对 read_url
    display 的渲染分流与工具组合并阈值（≥2 全 read_url → tool-group → 来源集合）。"""

    def _hit(title: str, url: str, snippet: str, site: str) -> dict:
        return {"title": title, "url": url, "snippet": snippet, "site": site}

    def _rd(url: str, title: str, site: str, snippet: str, content: str) -> dict:
        return {
            "url": url,
            "title": title,
            "site": site,
            "snippet": snippet,
            "content": content,
        }

    return [
        message_start("m1", conversation_id=_CONV),
        reasoning_delta("先检索这个案子的背景。"),
        tool_use_start("tc1", "web_search", {"query": "LV 茉莉奶白 商标 诉讼"}),
        tool_use_end(
            "tc1",
            "web_search",
            success=True,
            output="找到 3 条结果。",
            display={
                "query": "LV 茉莉奶白 商标 诉讼",
                "results": [
                    _hit(
                        "驴疯了？LV 起诉国家知识产权局！",
                        "https://www.sohu.com/a/1050596771_121124370",
                        "路易威登与「茉莉奶白」的商标纠纷再起波澜。",
                        "sohu.com",
                    ),
                    _hit(
                        "LV 起诉国家知识产权局，7 月开庭",
                        "https://www.sohu.com/a/1050271277_349248",
                        "相关商标行政纠纷案将于 7 月 16 日开庭审理。",
                        "sohu.com",
                    ),
                    _hit(
                        "又涉及茉莉奶白？本案属行政诉讼",
                        "https://www.sohu.com/a/1050304127_121811866",
                        "本案属于行政诉讼范畴，被告为国家知识产权局。",
                        "sohu.com",
                    ),
                ],
            },
        ),
        reasoning_delta("摘要不够，深读第一篇看细节。"),
        tool_use_start(
            "tc2", "read_url", {"url": "https://www.sohu.com/a/1050596771_121124370"}
        ),
        tool_use_end(
            "tc2",
            "read_url",
            success=True,
            output='{"url": "…", "title": "驴疯了？LV 起诉国家知识产权局！", "content": "…"}',
            display=_rd(
                "https://www.sohu.com/a/1050596771_121124370",
                "驴疯了？LV 起诉国家知识产权局！",
                "sohu.com",
                "路易威登针对「茉莉奶白」商标争议将国家知识产权局诉至法院。",
                "路易威登（LV）近日就「茉莉奶白」商标争议，将国家知识产权局诉至法院。"
                "该案源于双方在商标近似认定上的分歧，一审将于近期开庭。",
            ),
        ),
        reasoning_delta("再多读几篇核对细节。"),
        tool_use_start(
            "tc3", "read_url", {"url": "https://www.sohu.com/a/1050271277_349248"}
        ),
        tool_use_end(
            "tc3",
            "read_url",
            success=True,
            output="正文……",
            display=_rd(
                "https://www.sohu.com/a/1050271277_349248",
                "LV 起诉国家知识产权局，7 月开庭",
                "sohu.com",
                "相关商标行政纠纷案将于 7 月 16 日开庭审理。",
                "相关商标行政纠纷案将于 7 月 16 日在北京知识产权法院开庭审理。",
            ),
        ),
        tool_use_start(
            "tc4", "read_url", {"url": "https://www.sohu.com/a/1050304127_121811866"}
        ),
        tool_use_end(
            "tc4",
            "read_url",
            success=True,
            output="正文……",
            display=_rd(
                "https://www.sohu.com/a/1050304127_121811866",
                "又涉及茉莉奶白？本案属行政诉讼",
                "sohu.com",
                "本案属于行政诉讼范畴，被告为国家知识产权局。",
                "本案属于行政诉讼范畴，被告为国家知识产权局，原告为路易威登。",
            ),
        ),
        tool_use_start(
            "tc5", "read_url", {"url": "https://zhuanlan.zhihu.com/p/700123456"}
        ),
        tool_use_end(
            "tc5",
            "read_url",
            success=True,
            output="正文……",
            display=_rd(
                "https://zhuanlan.zhihu.com/p/700123456",
                "如何看待 LV 起诉国家知识产权局",
                "zhihu.com",
                "多角度分析该案的法律看点与商标近似认定标准。",
                "本文从商标近似认定与行政诉讼程序两方面分析该案的看点。",
            ),
        ),
        content_delta("综合多篇报道，"),
        content_delta("该案为 LV 就「茉莉奶白」商标提起的行政诉讼，将于 7 月开庭。"),
        citations_event(
            [
                _hit(
                    "驴疯了？LV 起诉国家知识产权局！",
                    "https://www.sohu.com/a/1050596771_121124370",
                    "路易威登与「茉莉奶白」的商标纠纷。",
                    "sohu.com",
                ),
                _hit(
                    "LV 起诉国家知识产权局，7 月开庭",
                    "https://www.sohu.com/a/1050271277_349248",
                    "将于 7 月 16 日开庭审理。",
                    "sohu.com",
                ),
            ]
        ),
        message_end(FinishReason.END_TURN, input_tokens=2200, output_tokens=320, cost=_COST),
    ]

def _single_agent_content_reset() -> list[SSEEvent]:
    """单聊·交付前核验回炉 (finish_guard)：CEO 直答先产出带越界角标的违规版正文（仅 1 条来源
    却引了 [2]，复刻真实事故「24 源却写 [25]」），done 轮轻层核验拦下 → content_reset 丢弃这
    一版 → 重写为只引真实来源 [1] 的修正版。三端 fold + oracle 必须一致处理 content_reset：清
    正文标量 + 弹掉 process 尾部连续 content 步，故最终 content/process 只含修正版（违规版不
    残留），尾部 tool 步保留。"""
    return [
        message_start("m1", conversation_id=_CONV),
        reasoning_delta("先查资料再作答。"),
        tool_use_start("tc1", "web_search", {"query": "建设工程价款优先权"}),
        tool_use_end("tc1", "web_search", success=True, output="找到 1 条来源。"),
        content_delta("依据 [1] 与 "),
        content_delta("[2] 可知……"),
        content_reset("finish_guard"),
        content_delta("依据 [1] "),
        content_delta("可知……"),
        citations_event(
            [
                {
                    "url": "https://a.example/x",
                    "title": "来源 A",
                    "snippet": "片段 A",
                    "site": "a.example",
                },
            ]
        ),
        message_end(FinishReason.END_TURN, input_tokens=1900, output_tokens=210, cost=_COST),
    ]

def _single_agent_retry_reset() -> list[SSEEvent]:
    """单聊·LLM 流式透明重试 (reason=retry)：上游故障丢弃已流出的临时正文、重试重写。与
    finish_guard 回炉同用 ``content_reset`` 机制，但三端 fold + oracle 必须一致地【不】折
    rework 步——基础设施重试不是「按交付规范重写」，不该留痕（误报根治的棘轮向量）。清正文
    标量 + 弹掉尾部 content 步照旧，故最终 content/process 只含重写版、无 rework chip。"""
    return [
        message_start("m1", conversation_id=_CONV),
        reasoning_delta("直接作答。"),
        content_delta("答案是……"),
        content_reset("retry"),
        content_delta("答案：42。"),
        message_end(FinishReason.END_TURN, input_tokens=900, output_tokens=80, cost=_COST),
    ]


def _single_agent_captain_context() -> list[SSEEvent]:
    """单聊：CEO 收到的上下文 (上下文传递可视化, CEO 侧 通道①)。纯聊天回合无 run_plan，但 captain
    仍 emit ``run_started(kind=captain)`` + ``run_context``（system/history/request 三通道）。三端
    fold + oracle 必须把它路由到 TURN 级 ``captainContext``（CEO 是图上方的气泡，不是节点）——故
    ``runs`` 恒空、``process`` 照常累积，``captainContext`` 承载这三块。这正是方案 3 的关键：最高频的
    纯聊天回合也能看见 CEO 吃进了什么（决策②: system 默认隐藏是前端门控，不影响投影）。"""
    return [
        message_start("m1", conversation_id=_CONV),
        run_started("c1", "c1", kind="captain"),
        run_context(
            "c1",
            "c1",
            [
                _ctx_block(
                    "system",
                    "CEO 系统提示（本回合实际遵循的系统指令）",
                    "你是 CEO，统筹团队完成用户目标。",
                ),
                _ctx_block(
                    "history",
                    "对话历史（本回合之前的往来）",
                    "用户：你好\n\nCEO：你好，有什么可以帮你？",
                ),
                _ctx_block("request", "原始用户请求", "帮我把这段话润色一下。"),
            ],
        ),
        reasoning_delta("先理解用户的润色诉求。"),
        content_delta("润色后的版本如下：……"),
        run_completed(
            "c1",
            "c1",
            output_summary="完成润色",
            duration_ms=800,
            role="captain",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        message_end(FinishReason.END_TURN, input_tokens=1200, output_tokens=300, cost=_COST),
    ]


def _reload_turn_warning() -> list[SSEEvent]:
    """刷新重建（P2）：预检 ``turn_warning`` 已 DURABLE——向量模拟「流中/收口后刷新」重放
    journal 可见事件，golden 钉住 ``turnWarning`` 横幅文案（三端 fold 同形）。"""
    return [
        message_start("m1", conversation_id=_CONV),
        turn_warning("当前模型可能不支持工具调用，复杂任务效果可能受限。"),
        content_delta("好的，我先用纯文本回答。"),
        message_end(FinishReason.END_TURN, input_tokens=800, output_tokens=120, cost=_COST),
    ]


def _reload_interrupted_partial() -> list[SSEEvent]:
    """中断回合 + 部分内容（P4）：lease sweeper salvage 写 ``finish_reason=interrupted``，
    半截思考/正文保留；golden 钉住 finishReason + cancelled-class status + 部分文本。"""
    return [
        message_start("m1", conversation_id=_CONV),
        reasoning_delta("先想一下方案。"),
        content_delta("根据现有信息，"),
        content_delta("建议先做这一步"),
        message_end(
            FinishReason.INTERRUPTED,
            input_tokens=400,
            output_tokens=80,
            cost=_COST,
        ),
    ]


def _reload_cursor_structure() -> list[SSEEvent]:
    """游标重连结构完整（P3/P4）：全量 journal 回放——游标前的工具行必须在场，正文为单块
    （segment 合成同构），无叠字。钉住 process 工具步 + 正文。

    段首取自服务端回放合成器本体（:func:`replay_open_event`，带 ``full_replay``）：清空指令
    由段首帧下达，客户端据此重置本回合流式态后重折，不再拿 id 与屏上气泡比对自己猜。
    """
    return [
        replay_open_event(turn_id="m1", conversation_id=_CONV),
        reasoning_delta("我先搜索。"),
        tool_use_start("tc1", "web_search", {"query": "AgentCore"}),
        tool_use_end("tc1", "web_search", success=True, output="找到 3 条结果。"),
        # Single-block content (stream_state / coalesced replay shape).
        content_delta("根据搜索，答案如下。"),
        message_end(FinishReason.END_TURN, input_tokens=1500, output_tokens=200, cost=_COST),
    ]


def _reload_cursor_paused_ask() -> list[SSEEvent]:
    """游标重连 · 耐久卡绑定（SSE-A1）：attach 回放段本身就得能画出待答卡。

    ``message_start`` 是 EPHEMERAL、不落 journal，却是前端拿到服务端 ``message_id`` 的
    唯一盖章点——耐久卡的「继续」正按这个 id 提交（``POST …/messages/{id}/resume``）。故回放
    段由 :func:`replay_open_event` 开场：盖章在耐久卡之前，卡才绑得到本回合气泡；同一帧的
    ``full_replay`` 即「本段是全量重放，先重置本回合本地态」的显式指令。

    开场帧与收口帧都取自服务端回放合成器本体（不手抄），回放段形状一变、导出的 golden 就变，
    契约漂移门禁即红。
    """
    return [
        replay_open_event(turn_id="m1", conversation_id=_CONV),
        # journal process_content → 单块正文（游标回放同构，无叠字）。
        content_delta("我先按 A 方案推进。"),
        checkpoint_required(
            checkpoint_id="cp1",
            conversation_id=_CONV,
            question="继续按 A 方案，还是换 B？\n两者成本相近，B 多花一天但更稳。",
            intent="decision",
        ),
        # 收口事实回放：detached 回合的 message_end 发进了空气，attach 段按 turn_end 合成
        # （仅 finish_reason；usage/cost 由 Message 列 rehydrate）。
        replay_close_event(FinishReason.PAUSED),
    ]


def _reload_cursor_incremental() -> list[SSEEvent]:
    """游标重连 · 增量段（P3 真增量）：段首**不带** ``full_replay`` → 客户端不清空、往后接。

    前半场是客户端已经折过的 live 流：思考 → 工具 → 半截正文。它的 ``Last-Event-ID`` 停在
    ``tool_use_end`` 那条耐久事件上——正文 delta 不带 ``id:``，所以游标天然落在文本块中间。

    重连后服务端只补游标之后的事实。那一步正文在 journal 里是**整步全文**（process 步闭合
    才落盘），客户端手里却只有它的前半截，于是该帧带 ``replace``：整块换掉、不是往后追加。
    金标钉死两件事——不清空（思考/工具行仍在），且正文是「根据搜索，答案如下。」而不是把
    「根据搜索，」叠两遍。
    """
    return [
        message_start("m1", conversation_id=_CONV),
        reasoning_delta("我先搜索。"),
        tool_use_start("tc1", "web_search", {"query": "AgentCore"}),
        tool_use_end("tc1", "web_search", success=True, output="找到 3 条结果。"),
        # 客户端只收到这半截就断了（游标仍停在 tool_use_end）。
        content_delta("根据搜索，"),
        # —— 重连：增量段开场，段首无 full_replay ——
        replay_open_event(turn_id="m1", conversation_id=_CONV, full_replay=False),
        content_delta("根据搜索，答案如下。", replace=True),
        message_end(FinishReason.END_TURN, input_tokens=1500, output_tokens=200, cost=_COST),
    ]


def _mid_run_refresh_ceo_narration() -> list[SSEEvent]:
    """运行中刷新（process 渐进持久化）：CEO 旁白→工具→旁白→交付，保序交织。

    Attach 全量重放须还原同一 process 序；``messages.content`` 在交付轮
    才累加终稿（向量里末段 content 即交付，前段旁白也在 content_delta 里——与 live 同构，
    deliverable_only 裁剪是服务端 finalize 契约，不在本 fold 向量里模拟）。
    """
    return [
        message_start("m1", conversation_id=_CONV),
        reasoning_delta("先摸清案情。"),
        content_delta("## 案情简介\nLV 诉茉莉奶白。"),
        tool_use_start("tc1", "web_search", {"query": "LV 茉莉奶白"}),
        tool_use_end("tc1", "web_search", success=True, output="找到关键报道。"),
        content_delta("检索完毕，下面给出结论。"),
        content_delta("\n\n## 结论\n建议启动辩论。"),
        message_end(FinishReason.END_TURN, input_tokens=1800, output_tokens=400, cost=_COST),
    ]


def _empty_face_shell(
    *,
    code: str,
    message: str,
    finish: FinishReason,
) -> list[SSEEvent]:
    """Empty assistant content + structured error + terminal finish (空泡脸向量骨架)."""
    return [
        message_start("m1", conversation_id=_CONV),
        error_event(code, message),
        message_end(finish, input_tokens=0, output_tokens=0, cost=_COST),
    ]


def _empty_face_degraded() -> list[SSEEvent]:
    return _empty_face_shell(
        code="LLM_EMPTY_RESPONSE",
        message="模型多次空响应 · 模型返回空内容",
        finish=FinishReason.DEGRADED,
    )


def _empty_face_paused() -> list[SSEEvent]:
    """paused without interaction card — must still surface a face when error present."""
    return _empty_face_shell(
        code="PIPELINE_ERROR",
        message="本轮未能完成，请重试。",
        finish=FinishReason.PAUSED,
    )


def _empty_face_channel_dead() -> list[SSEEvent]:
    # 生产真值：prepare 阶段 WorkspaceIOError(CHANNEL_DEAD_PREPARE_ABORT)
    # 经 error_fields_for 映射为 STREAM_ERROR + 原文（见 core/errors.py）。
    # 引常量而非抄字面量，避免文案改动后向量再次失真。
    return _empty_face_shell(
        code=ErrorCode.STREAM_ERROR,
        message=CHANNEL_DEAD_PREPARE_ABORT,
        finish=FinishReason.ERROR,
    )


def _empty_face_insufficient_balance() -> list[SSEEvent]:
    return _empty_face_shell(
        code="LLM_INSUFFICIENT_BALANCE",
        message="上游账户余额不足，请充值或更换 Key。",
        finish=FinishReason.ERROR,
    )


def _empty_face_model_acl() -> list[SSEEvent]:
    return _empty_face_shell(
        code="LLM_ERROR",
        message="This token has no access to model kimi-k3",
        finish=FinishReason.ERROR,
    )


def _empty_face_invalid_temperature() -> list[SSEEvent]:
    return _empty_face_shell(
        code="LLM_ERROR",
        message="invalid temperature: only 1 is allowed for this model",
        finish=FinishReason.ERROR,
    )


def _empty_face_timeout() -> list[SSEEvent]:
    return _empty_face_shell(
        code="LLM_TIMEOUT",
        message="连接超时，请检查网络后重试。",
        finish=FinishReason.ERROR,
    )


def _empty_face_empty_response() -> list[SSEEvent]:
    return _empty_face_shell(
        code="LLM_EMPTY_RESPONSE",
        message="模型空响应 · 输出长度截断 · 返回空内容",
        finish=FinishReason.DEGRADED,
    )


VECTORS: dict[str, tuple[str, Callable[[], list[SSEEvent]]]] = {
    "single_agent_text": ("单聊：思考+正文+总账，end_turn 完成", _single_agent_text),
    "single_agent_queued_then_run": (
        "发送即有流：turn_queued → turn_queue_started → message_start…（FIFO 闭环）",
        _single_agent_queued_then_run,
    ),
    "single_agent_queued_degraded_from_steer": (
        "经典+steer 回落：turn_queued.degraded_from=steer → turn_queue_started → 续流",
        _single_agent_queued_degraded_from_steer,
    ),
    "single_agent_user_interjection_steer": (
        "经典+steer 全链：user_interjection received→injected（DURABLE；经典终态）→ 续流单聊",
        _single_agent_user_interjection_steer,
    ),
    "single_agent_user_interjection_steer_queued": (
        "经典+steer 收口降级：user_interjection received→queued + turn_queued.degraded_from=steer",
        _single_agent_user_interjection_steer_queued,
    ),
    "single_agent_queue_cancelled": (
        "排队项取消：turn_queued → turn_queue_cancelled（EPHEMERAL；多端清 UI）",
        _single_agent_queue_cancelled,
    ),
    "single_agent_tool": ("单聊：思考→工具→正文（process 时间线）", _single_agent_tool),
    "single_agent_consult_memory": ("单聊：CEO 翻开记忆主题笔记（consult_memory → 查阅记忆卡片 + 全文）", _single_agent_consult_memory),
    "single_agent_error": ("单聊：正文中途 error 事件 → failed", _single_agent_error),
    "single_agent_tool_failure": (
        "单聊：工具失败（tool_use_end success=False → process status=error）后继续作答",
        _single_agent_tool_failure,
    ),
    "single_agent_cancelled": (
        "单聊：用户取消（message_end finish_reason=cancelled → status=cancelled，半截正文保留）",
        _single_agent_cancelled,
    ),
    "single_agent_tool_progress": (
        "单聊：工具执行阶段进度（tool_use_progress querying/queued，EPHEMERAL；终态同成功工具）",
        _single_agent_tool_progress,
    ),
    "single_agent_title_and_turn_saved": (
        "单聊：chrome turn_saved + title_generated（不进 ProjectedTurn；fold no-op）",
        _single_agent_title_and_turn_saved,
    ),
    "single_agent_citations": ("单聊：思考→工具→正文 + citations 来源卡", _single_agent_citations),
    "single_agent_web_read": (
        "单聊：联网检索+深读富渲染（web_search 卡 · 单条 read_url 来源头+正文 · ≥2 read_url 来源集合）",
        _single_agent_web_read,
    ),
    "single_agent_content_reset": ("单聊：交付前核验回炉 (finish_guard) content_reset 丢弃违规版正文、重写修正版", _single_agent_content_reset),
    "single_agent_retry_reset": ("单聊：LLM 流式透明重试 (reason=retry) content_reset 丢弃临时正文、不留 rework 痕迹", _single_agent_retry_reset),
    "single_agent_captain_context": ("单聊：CEO 收到的上下文（run_context kind=captain → 回合级 captainContext，system/history/request）", _single_agent_captain_context),
    "reload_turn_warning": (
        "刷新重建（P2）：turn_warning DURABLE → ProjectedTurn.turnWarning 横幅",
        _reload_turn_warning,
    ),
    "reload_interrupted_partial": (
        "中断回合+部分内容（P4）：finish_reason=interrupted → 半截正文/思考 + cancelled status",
        _reload_interrupted_partial,
    ),
    "reload_cursor_structure": (
        "游标重连结构完整（P3）：全量 journal 回放 → 工具行+正文同在、无叠字",
        _reload_cursor_structure,
    ),
    "reload_cursor_paused_ask": (
        "游标重连耐久卡（SSE-A1）：回放段以 message_start 盖章开场 → 待答 ask_user 卡绑本回合",
        _reload_cursor_paused_ask,
    ),
    "reload_cursor_incremental": (
        "游标重连增量段（P3）：段首无 full_replay → 不清空；跨游标那步正文带 replace 整块换、不叠字",
        _reload_cursor_incremental,
    ),
    "mid_run_refresh_ceo_narration": (
        "运行中刷新：CEO 旁白→工具→旁白→交付 process 保序（process 渐进持久化）",
        _mid_run_refresh_ceo_narration,
    ),
    # 空泡族根因重设计：空 content + 结构化错误 → fold 后非空脸（hasProjectedFailureFace）
    "empty_face_degraded": (
        "空脸：degraded 收尾 + LLM_EMPTY_RESPONSE → 非空脸",
        _empty_face_degraded,
    ),
    "empty_face_paused": (
        "空脸：paused 且无暂停卡 + 结构化错误 → 非空脸",
        _empty_face_paused,
    ),
    "empty_face_channel_dead": (
        "空脸：channel_dead / STREAM_ERROR → 非空脸",
        _empty_face_channel_dead,
    ),
    "empty_face_insufficient_balance": (
        "空脸：欠费 LLM_INSUFFICIENT_BALANCE → 非空脸",
        _empty_face_insufficient_balance,
    ),
    "empty_face_model_acl": (
        "空脸：模型 ACL 无权限 → 非空脸",
        _empty_face_model_acl,
    ),
    "empty_face_invalid_temperature": (
        "空脸：invalid temperature → 非空脸",
        _empty_face_invalid_temperature,
    ),
    "empty_face_timeout": (
        "空脸：连接超时 LLM_TIMEOUT → 非空脸",
        _empty_face_timeout,
    ),
    "empty_face_empty_response": (
        "空脸：llm.empty_response（LLM_EMPTY_RESPONSE + degraded）→ 非空脸",
        _empty_face_empty_response,
    ),
}
