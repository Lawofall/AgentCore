"""Cloud early title mint: schedule after user save, conditional write, fallback."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import agentcore.conversation.common as common
from agentcore.conversation.common import (
    TITLE_MAX_CHARS,
    fallback_title,
    generate_title,
    schedule_title_generation,
)
from agentcore.memory.conversation_title import TitleResult
from agentcore.runtime.events import EventSink, EventType


class _BoomProvider:
    async def complete(self, _request):
        raise RuntimeError("llm down")


class _FakeSessionCM:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


def test_fallback_title_truncates_to_max():
    long = "题" * (TITLE_MAX_CHARS + 5)
    assert fallback_title(long) == "题" * TITLE_MAX_CHARS + "…"


# --- 降级标题的可读性 ---------------------------------------------------------
#
# 标题模型是尽力而为，429 / 超时 / 额度打光都落到 fallback_title，而它产出的字符串
# 就是用户在侧栏里长期看着的那个。从首条消息头上硬切 30 字，遇到粘贴进来的任务卡就
# 是一段从句子中间撕下来的正文——线上真实抱怨。


def test_fallback_title_takes_the_first_line_of_a_task_card():
    """任务卡：标题取首行主题，不要横穿换行把正文拖进侧栏。"""
    card = "帮我做一个季度销售复盘\n数据在 sales.csv，需要按大区拆分，输出结论和建议"
    assert fallback_title(card) == "帮我做一个季度销售复盘…"
    assert "\n" not in fallback_title(card)


def test_fallback_title_marks_a_short_first_line_as_a_fragment():
    """丢了后文就得留省略号——否则用户原话会被 auto_cloud_desk_name 当成目录名收下。

    这里钉的是跨模块约束：``auto_cloud_desk_name`` 只做形状判断（够短 + 无截断标记），
    没有任何办法追溯标题是不是模型生成的。首行短的任务卡若不留标记，「帮我查一下张伟
    的社保记录」就会变成云端磁盘上真实存在的目录段。
    """
    from agentcore.runtime.delegate.target_desktop_auto_cloud import auto_cloud_desk_name

    card = "帮我查一下张伟的社保记录\n身份证号是 3101xxxxxxxxxxxxxx，手机 138xxxxxxxx"
    label = fallback_title(card)
    assert label.endswith("…")
    assert auto_cloud_desk_name(conversation_title=label) == "云文件夹"


def test_fallback_title_cuts_at_a_clause_boundary():
    """长句在最后一个从句边界断开，而不是在第 30 个字符处拦腰截断。"""
    long_line = "帮我写一份要求男方支付抚养费的起诉状、金额为4000元每月，另附证据清单"
    out = fallback_title(long_line)
    assert out == "帮我写一份要求男方支付抚养费的起诉状、金额为4000元每月…"
    # 西文同理：断在词边界，不切进单词里。
    assert fallback_title(
        "Please review the quarterly sales numbers and tell me what changed"
    ) == "Please review the quarterly…"


def test_fallback_title_keeps_the_truncation_marker():
    """省略号是 ``auto_cloud_desk_name`` 判「这是半句话、不能当目录名」的依据。"""
    assert fallback_title("题" * (TITLE_MAX_CHARS + 5)).endswith("…")
    assert not fallback_title("短标题").endswith("…")


def test_fallback_title_strips_markdown_decoration():
    """剥掉装饰不算「丢内容」——「## 季度复盘」是一个完整主题，不是半个。"""
    assert fallback_title("## 1. **季度复盘**") == "季度复盘"
    assert fallback_title("- 帮我查一下这个季度的销售数据") == "帮我查一下这个季度的销售数据"
    assert fallback_title("1、写一份周报") == "写一份周报"
    assert fallback_title("## 1. **季度复盘**\n详情见附件") == "季度复盘…"


def test_fallback_title_keeps_a_leading_decimal():
    """剥列表序号不能顺手把「1.5 亿」的小数点吃掉。"""
    money = "1.5 亿的预算怎么分配到三个大区，请给出方案和依据"
    assert fallback_title(money).startswith("1.5 亿")


def test_fallback_title_skips_lines_that_are_pure_decoration():
    """整行只有分隔符时继续往下找，别把标题降级成空字符串。"""
    assert fallback_title("---\n\n真正的内容在这里") == "真正的内容在这里"
    assert fallback_title("   ") == ""


async def test_generate_title_degrades_to_truncated_on_llm_failure():
    user = "帮我设计一个登录流程和权限模型"
    out = await generate_title(
        provider=_BoomProvider(),  # type: ignore[arg-type]
        conversation_id="c1",
        user_message=user,
        assistant_reply="",
    )
    assert out.title == fallback_title(user)
    # 降级必须自报家门：写库那一层要能分辨这是兜底标题还是模型产物。
    assert out.degraded_reason == "other"


async def test_generate_title_reraise_llm_auth_error():
    import pytest

    from agentcore.core.errors import LLMAuthError

    class _AuthBoom:
        async def complete(self, _request):
            raise LLMAuthError(provider_name="platform")

    with pytest.raises(LLMAuthError):
        await generate_title(
            provider=_AuthBoom(),  # type: ignore[arg-type]
            conversation_id="c1",
            user_message="帮我设计登录",
            assistant_reply="好的",
        )


async def test_generate_title_falls_back_on_truncated_json_body():
    """Empty/half JSON after length-truncation → user-message prefix, never `{\"title`."""
    from agentcore.llm import LLMResponse

    class _FragProvider:
        async def complete(self, _request):
            return LLMResponse(content='{"title')

    user = "帮我设计一个登录流程和权限模型"
    out = await generate_title(
        provider=_FragProvider(),  # type: ignore[arg-type]
        conversation_id="c1",
        user_message=user,
        assistant_reply="",
    )
    assert out.title == fallback_title(user)
    assert "{" not in out.title


async def test_schedule_title_does_not_block_caller(monkeypatch):
    """schedule returns immediately; the runner runs in parallel (does not await)."""
    gate = asyncio.Event()
    finished = asyncio.Event()
    order: list[str] = []

    async def _slow(**_kwargs):
        try:
            order.append("runner_started")
            await gate.wait()
            order.append("runner_done")
            finished.set()
        finally:
            common._title_inflight.discard("c-early")

    monkeypatch.setattr(common, "_mint_title_background", _slow)
    common._title_inflight.clear()

    schedule_title_generation(
        conversation_id="c-early",
        user_id="u1",
        user_message="你好",
        sink=EventSink(),
    )
    order.append("caller_returned")
    assert "runner_done" not in order
    assert "c-early" in common._title_inflight

    gate.set()
    await asyncio.wait_for(finished.wait(), 1)
    await asyncio.sleep(0)
    assert "caller_returned" in order
    assert "runner_done" in order
    assert "c-early" not in common._title_inflight


async def test_schedule_dedupes_while_inflight(monkeypatch):
    calls: list[str] = []

    async def _rec(**kwargs):
        calls.append(kwargs["conversation_id"])

    monkeypatch.setattr(common, "_mint_title_background", _rec)
    common._title_inflight.add("c1")
    try:
        schedule_title_generation(
            conversation_id="c1",
            user_id="u1",
            user_message="x",
            sink=EventSink(),
        )
        await asyncio.sleep(0.02)
        assert calls == []
    finally:
        common._title_inflight.discard("c1")


async def test_mint_skips_write_when_title_already_set(monkeypatch):
    """User rename race: already-titled conversations must not be overwritten."""
    writes: list[tuple[str, str]] = []
    emits: list[str] = []

    class _ConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title="用户手改的标题")

        async def update_title_if_empty(self, conversation_id, title):
            writes.append((conversation_id, title))
            return SimpleNamespace(title=title)

    monkeypatch.setattr(common, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(common, "ConversationRepository", _ConvRepo)
    common._title_inflight.clear()

    sink = EventSink()
    original_emit = sink.emit

    def _spy_emit(event):
        emits.append(event.type.value if hasattr(event.type, "value") else str(event.type))
        return original_emit(event)

    sink.emit = _spy_emit  # type: ignore[method-assign]

    await common._mint_title_background(
        conversation_id="c1",
        user_id="u1",
        user_message="不该覆盖",
        sink=sink,
    )
    assert writes == []
    assert EventType.TITLE_GENERATED.value not in emits
    assert "c1" not in common._title_inflight


async def test_mint_skips_write_and_does_not_emit_when_llm_fails(monkeypatch):
    """LLM failure → no DB write, no SSE title_generated (column stays empty)."""
    writes: list[tuple[str, str]] = []
    user = "这是一条足够长的首条用户消息用来验证截断降级行为是否正确"

    class _ConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title=None)

        async def update_title_if_empty(self, conversation_id, title):
            writes.append((conversation_id, title))
            return SimpleNamespace(title=title)

    monkeypatch.setattr(common, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(common, "ConversationRepository", _ConvRepo)

    async def _run_bg(user_id, *, purpose="title", runner):
        from agentcore.billing.gate import BackgroundLlmResult
        from agentcore.llm.credentials import LLMCredentials

        creds = LLMCredentials(
            api_key="sk", base_url="https://x", default_model="flash", source="platform"
        )
        value = await runner(creds)
        return BackgroundLlmResult(value=value, credentials=creds)

    monkeypatch.setattr(common, "run_background_llm", _run_bg)
    monkeypatch.setattr(common, "resolve_turn_model", lambda _c: "flash")
    monkeypatch.setattr(
        common,
        "build_provider",
        lambda *_a, **_k: SimpleNamespace(close=AsyncMock()),
    )
    monkeypatch.setattr(
        common,
        "generate_title",
        AsyncMock(
            return_value=TitleResult(
                title=fallback_title(user), degraded_reason="other"
            )
        ),
    )
    common._title_inflight.add("c-fail")  # simulate schedule having armed it

    sink = EventSink()
    events: list = []
    original_emit = sink.emit

    def _spy_emit(event):
        events.append(event)
        return original_emit(event)

    sink.emit = _spy_emit  # type: ignore[method-assign]

    await common._mint_title_background(
        conversation_id="c-fail",
        user_id="u1",
        user_message=user,
        sink=sink,
    )

    assert writes == []
    title_events = [e for e in events if e.type == EventType.TITLE_GENERATED]
    assert title_events == []
    assert "c-fail" not in common._title_inflight


# --- 降级要落日志、不得写库 ---------------------------------------------------
#
# 失败若把 fallback 写入 title，字段变非空，所有铸题入口都不再触发——限流恢复后
# 永远不会再铸。chat.title_degraded.persisted 区分「曾落库」与「未写、可再铸」。


def _stub_mint_deps(monkeypatch, *, writes: list[tuple[str, str]]) -> None:
    """Wire ``_mint_title_core`` onto fakes: no DB, no provider, no real LLM."""

    class _ConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title=None)

        async def update_title_if_empty(self, conversation_id, title):
            writes.append((conversation_id, title))
            return SimpleNamespace(title=title)

    monkeypatch.setattr(common, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(common, "ConversationRepository", _ConvRepo)
    monkeypatch.setattr(common, "resolve_turn_model", lambda _c: "flash")
    monkeypatch.setattr(
        common, "build_provider", lambda *_a, **_k: SimpleNamespace(close=AsyncMock())
    )


async def test_mint_logs_the_degrade_when_the_title_model_fails(monkeypatch):
    """模型侧失败（429 等）打 chat.title_degraded 且不写库。"""
    from tests.conftest import LogSpy

    writes: list[tuple[str, str]] = []
    _stub_mint_deps(monkeypatch, writes=writes)

    async def _run_bg(user_id, *, purpose="title", runner):
        from agentcore.billing.gate import BackgroundLlmResult
        from agentcore.llm.credentials import LLMCredentials

        creds = LLMCredentials(
            api_key="sk", base_url="https://x", default_model="flash", source="platform"
        )
        return BackgroundLlmResult(
            value=TitleResult(title="兜底短标题", degraded_reason="rate_limit"),
            credentials=creds,
        )

    monkeypatch.setattr(common, "run_background_llm", _run_bg)
    spy = LogSpy()
    monkeypatch.setattr(common, "logger", spy)

    out = await common.mint_title_if_empty(
        conversation_id="c-degraded", user_id="u1", user_message="随便什么首条消息"
    )

    assert out is None
    assert writes == []
    line = spy.get("chat.title_degraded")
    assert line["conversation_id"] == "c-degraded"
    assert line["reason"] == "rate_limit"
    assert line["title_chars"] == len("兜底短标题")
    assert line["persisted"] is False


async def test_mint_logs_a_gate_refusal_as_its_own_reason(monkeypatch):
    """闸拒（额度打光 / 没凭据）也是降级，且要与「模型失败」区分得开。"""
    from agentcore.billing.gate import BackgroundLlmSkip, BackgroundSkipReason
    from tests.conftest import LogSpy

    writes: list[tuple[str, str]] = []
    _stub_mint_deps(monkeypatch, writes=writes)

    async def _run_bg(user_id, *, purpose="title", runner):
        return BackgroundLlmSkip(reason=BackgroundSkipReason.NO_CREDENTIALS)

    monkeypatch.setattr(common, "run_background_llm", _run_bg)
    spy = LogSpy()
    monkeypatch.setattr(common, "logger", spy)

    user = "帮我做一个季度销售复盘\n数据在 sales.csv，需要按大区拆分"
    await common.mint_title_if_empty(
        conversation_id="c-gated", user_id="u1", user_message=user
    )

    assert writes == []
    line = spy.get("chat.title_degraded")
    assert line["reason"] == "gate_no_credentials"
    assert line["persisted"] is False


async def test_mint_stays_quiet_when_the_model_answers(monkeypatch):
    """模型正常产出时不得打降级日志——否则这条指标什么也测不出来。"""
    from tests.conftest import LogSpy

    writes: list[tuple[str, str]] = []
    _stub_mint_deps(monkeypatch, writes=writes)

    async def _run_bg(user_id, *, purpose="title", runner):
        from agentcore.billing.gate import BackgroundLlmResult
        from agentcore.llm.credentials import LLMCredentials

        creds = LLMCredentials(
            api_key="sk", base_url="https://x", default_model="flash", source="platform"
        )
        return BackgroundLlmResult(value=TitleResult(title="季度销售复盘"), credentials=creds)

    monkeypatch.setattr(common, "run_background_llm", _run_bg)
    spy = LogSpy()
    monkeypatch.setattr(common, "logger", spy)

    await common.mint_title_if_empty(
        conversation_id="c-ok", user_id="u1", user_message="帮我做一个季度销售复盘"
    )

    assert writes == [("c-ok", "季度销售复盘")]
    assert [name for name, _ in spy.events if name == "chat.title_degraded"] == []


async def test_mint_emits_best_effort_when_sink_closed(monkeypatch):
    """Closed sink must not raise; DB write still lands."""
    writes: list[str] = []

    class _ConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title=None)

        async def update_title_if_empty(self, conversation_id, title):
            writes.append(title)
            return SimpleNamespace(title=title)

    monkeypatch.setattr(common, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(common, "ConversationRepository", _ConvRepo)

    async def _run_bg(user_id, *, purpose="title", runner):
        from agentcore.billing.gate import BackgroundLlmResult
        from agentcore.llm.credentials import LLMCredentials

        creds = LLMCredentials(
            api_key="sk", base_url="https://x", default_model="flash", source="platform"
        )
        value = await runner(creds)
        return BackgroundLlmResult(value=value, credentials=creds)

    monkeypatch.setattr(common, "run_background_llm", _run_bg)
    monkeypatch.setattr(common, "resolve_turn_model", lambda _c: "flash")
    monkeypatch.setattr(
        common,
        "build_provider",
        lambda *_a, **_k: SimpleNamespace(close=AsyncMock()),
    )
    monkeypatch.setattr(
        common,
        "generate_title",
        AsyncMock(return_value=TitleResult(title="早到标题")),
    )
    common._title_inflight.clear()

    sink = EventSink()
    sink.close()
    await common._mint_title_background(
        conversation_id="c-closed",
        user_id="u1",
        user_message="hi",
        sink=sink,
    )
    assert writes == ["早到标题"]


async def test_stream_chat_schedules_title_before_turn(monkeypatch):
    """After user message persist, title is scheduled before run_and_persist runs."""
    from agentcore.conversation import turns as turns_mod

    order: list[str] = []
    scheduled: list[dict] = []

    class _ConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title=None, folder_id=None)

    class _MsgRepo:
        def __init__(self, _session):
            pass

        async def create(self, **_kwargs):
            order.append("user_saved")
            return SimpleNamespace(id="um1")

    class _BoardRepo:
        def __init__(self, _session):
            pass

        async def get_by_conversation_id(self, *_a, **_k):
            return None

    monkeypatch.setattr(turns_mod, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(turns_mod, "ConversationRepository", _ConvRepo)
    monkeypatch.setattr(turns_mod, "MessageRepository", _MsgRepo)
    monkeypatch.setattr(turns_mod, "BoardRepository", _BoardRepo)
    monkeypatch.setattr(turns_mod, "resolve_local_binding", AsyncMock(return_value=None))
    monkeypatch.setattr(turns_mod, "resolve_profile_set", AsyncMock(return_value=None))

    from agentcore.core.types import AutonomyPolicy, recipe_to_axes

    monkeypatch.setattr(
        turns_mod,
        "resolve_permission_axes",
        AsyncMock(return_value=recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT)),
    )
    monkeypatch.setattr(
        turns_mod,
        "build_turn_backend",
        AsyncMock(return_value=SimpleNamespace(location="server")),
    )
    monkeypatch.setattr(turns_mod, "persist_attachments", AsyncMock(return_value=[]))
    monkeypatch.setattr(turns_mod, "to_stored_metadata", lambda _a: None)
    monkeypatch.setattr(
        turns_mod,
        "load_chat_context",
        AsyncMock(return_value=[{"role": "user", "content": "hi"}]),
    )

    def _schedule(**kwargs):
        order.append("title_scheduled")
        scheduled.append(kwargs)

    async def _run(**_kwargs):
        order.append("turn_started")

    monkeypatch.setattr(turns_mod, "schedule_title_generation", _schedule)
    monkeypatch.setattr(turns_mod, "run_and_persist", _run)

    sink = EventSink()
    await turns_mod.stream_chat(
        conversation_id="c1",
        user_message="帮我写周报",
        user_id="u1",
        sink=sink,
    )

    assert order == ["user_saved", "title_scheduled", "turn_started"]
    assert scheduled[0]["conversation_id"] == "c1"
    assert scheduled[0]["user_message"] == "帮我写周报"


async def test_stream_chat_skips_title_when_already_named(monkeypatch):
    from agentcore.conversation import turns as turns_mod

    scheduled: list[dict] = []

    class _ConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title="已命名", folder_id=None)

    class _MsgRepo:
        def __init__(self, _session):
            pass

        async def create(self, **_kwargs):
            return SimpleNamespace(id="um1")

    class _BoardRepo:
        def __init__(self, _session):
            pass

        async def get_by_conversation_id(self, *_a, **_k):
            return None

    monkeypatch.setattr(turns_mod, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(turns_mod, "ConversationRepository", _ConvRepo)
    monkeypatch.setattr(turns_mod, "MessageRepository", _MsgRepo)
    monkeypatch.setattr(turns_mod, "BoardRepository", _BoardRepo)
    monkeypatch.setattr(turns_mod, "resolve_local_binding", AsyncMock(return_value=None))
    monkeypatch.setattr(turns_mod, "resolve_profile_set", AsyncMock(return_value=None))

    from agentcore.core.types import AutonomyPolicy, recipe_to_axes

    monkeypatch.setattr(
        turns_mod,
        "resolve_permission_axes",
        AsyncMock(return_value=recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT)),
    )
    monkeypatch.setattr(
        turns_mod,
        "build_turn_backend",
        AsyncMock(return_value=SimpleNamespace(location="server")),
    )
    monkeypatch.setattr(turns_mod, "persist_attachments", AsyncMock(return_value=[]))
    monkeypatch.setattr(turns_mod, "to_stored_metadata", lambda _a: None)
    monkeypatch.setattr(
        turns_mod,
        "load_chat_context",
        AsyncMock(return_value=[{"role": "user", "content": "hi"}]),
    )
    monkeypatch.setattr(
        turns_mod,
        "schedule_title_generation",
        lambda **kwargs: scheduled.append(kwargs),
    )
    monkeypatch.setattr(turns_mod, "run_and_persist", AsyncMock())

    await turns_mod.stream_chat(
        conversation_id="c1",
        user_message="hi",
        user_id="u1",
        sink=EventSink(),
    )
    assert scheduled == []


async def test_mint_title_if_empty_returns_existing_without_llm(monkeypatch):
    """Await path short-circuits when the conversation already has a title."""
    core_calls: list[str] = []

    async def _boom(**_kwargs):
        core_calls.append("called")
        return "should-not-run"

    monkeypatch.setattr(common, "_mint_title_core", _boom)
    monkeypatch.setattr(
        common,
        "_read_conversation_title",
        AsyncMock(return_value="已有标题"),
    )
    common._title_inflight.clear()

    out = await common.mint_title_if_empty(
        conversation_id="c1",
        user_id="u1",
        user_message="不该铸",
        sink=None,
    )
    assert out == "已有标题"
    assert core_calls == []


async def test_mint_title_if_empty_runs_core_when_untitled(monkeypatch):
    writes: list[tuple[str, str]] = []

    class _ConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title=None)

        async def update_title_if_empty(self, conversation_id, title):
            writes.append((conversation_id, title))
            return SimpleNamespace(title=title)

    monkeypatch.setattr(common, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(common, "ConversationRepository", _ConvRepo)

    async def _run_bg(user_id, *, purpose="title", runner):
        from agentcore.billing.gate import BackgroundLlmResult
        from agentcore.llm.credentials import LLMCredentials

        creds = LLMCredentials(
            api_key="sk", base_url="https://x", default_model="flash", source="platform"
        )
        value = await runner(creds)
        return BackgroundLlmResult(value=value, credentials=creds)

    monkeypatch.setattr(common, "run_background_llm", _run_bg)
    monkeypatch.setattr(common, "resolve_turn_model", lambda _c: "flash")
    monkeypatch.setattr(
        common,
        "build_provider",
        lambda *_a, **_k: SimpleNamespace(close=AsyncMock()),
    )
    monkeypatch.setattr(
        common,
        "generate_title",
        AsyncMock(return_value=TitleResult(title="并行铸题")),
    )
    common._title_inflight.clear()

    out = await common.mint_title_if_empty(
        conversation_id="c-await",
        user_id="u1",
        user_message="帮我写周报",
        sink=None,
    )
    assert out == "并行铸题"
    assert writes == [("c-await", "并行铸题")]
    assert "c-await" not in common._title_inflight


def test_auto_title_response_allows_empty_title():
    """Mint miss returns HTTP 200 with empty title — not a validation error."""
    from agentcore.api.schemas.conversations import AutoTitleResponse

    assert AutoTitleResponse(title="").title == ""
    assert AutoTitleResponse(title="周报").title == "周报"


async def test_mint_title_if_empty_waits_on_inflight(monkeypatch):
    """When another mint is in flight, await path waits then returns DB title."""
    reads: list[int] = []

    async def _read(_cid):
        reads.append(1)
        if len(reads) == 1:
            return None  # first check: empty
        return "他途已铸"

    core_calls: list[str] = []

    async def _boom(**_kwargs):
        core_calls.append("called")
        return "should-not"

    monkeypatch.setattr(common, "_read_conversation_title", _read)
    monkeypatch.setattr(common, "_mint_title_core", _boom)
    common._title_inflight.add("c-wait")

    async def _clear_later():
        await asyncio.sleep(0.08)
        common._title_inflight.discard("c-wait")

    clearer = asyncio.create_task(_clear_later())
    try:
        out = await common.mint_title_if_empty(
            conversation_id="c-wait",
            user_id="u1",
            user_message="x",
            sink=None,
        )
        assert out == "他途已铸"
        assert core_calls == []
        assert len(reads) >= 2
    finally:
        await clearer
        common._title_inflight.discard("c-wait")
