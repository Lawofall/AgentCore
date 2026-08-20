"""Unit tests for prefix-cache observability (审计议题 D4 · 只观测不改装配).

Covers the metric computation itself (breach classification, reusable / forfeited tokens,
hit ratio, provider silence) and the section ledger that names WHICH prompt section broke
the byte prefix — plus the guarantee that adding all of it changed no assembled prompt.
"""

from __future__ import annotations

import pytest

from agentcore.core.log_context import bind_log_context, clear_log_context
from agentcore.llm.provider.protocol import LLMMessage, TokenUsage
from agentcore.observability.prefix_cache import (
    BASIS_ESTIMATED,
    BASIS_MEASURED,
    BASIS_NONE,
    BREACH_COLD_CHAIN,
    BREACH_HEAD_REWRITE,
    BREACH_HISTORY_GROWTH,
    BREACH_HISTORY_REWRITE,
    BREACH_IDENTICAL,
    BREACH_SYSTEM_PROMPT,
    ChainState,
    SectionDelta,
    compute_probe,
    digest_text,
    flatten_sections,
    message_fingerprints,
    observe_prefix_cache,
    prompt_section_delta,
    record_prompt_sections,
    reset_prefix_cache_state,
)
from agentcore.runtime.context import ContextAssembler, SectionOrder


@pytest.fixture(autouse=True)
def _clean_probe_state():
    reset_prefix_cache_state()
    clear_log_context()
    yield
    reset_prefix_cache_state()
    clear_log_context()


def _m(role: str, content: str) -> LLMMessage:
    return LLMMessage(role=role, content=content)


def _chain(*messages: LLMMessage, input_tokens: int, calls: int = 1) -> ChainState:
    """The state a previous call on the same chain would have left behind."""
    digests, _ = message_fingerprints(messages)
    return ChainState(digests=digests, input_tokens=input_tokens, calls=calls)


def _probe(messages, previous, *, hit=0, miss=0, input_tokens=1000, delta=None):
    digests, sizes = message_fingerprints(messages)
    return compute_probe(
        digests=digests,
        sizes=sizes,
        first_role=messages[0].role,
        input_tokens=input_tokens,
        cache_hit_tokens=hit,
        cache_miss_tokens=miss,
        previous=previous,
        **({"section_delta": delta} if delta is not None else {}),
    )


# --- 击穿归因: message-chain classification -------------------------------------------


def test_first_call_on_a_chain_is_cold_not_a_miss():
    # Nothing was cached before, so a 0% hit here means nothing — must not read as a breach.
    probe = _probe([_m("system", "SYS")], previous=None)
    assert probe.breach == BREACH_COLD_CHAIN
    assert probe.reusable_basis == BASIS_NONE
    assert probe.forfeited_tokens == 0
    assert probe.chain_calls == 1


def test_pure_append_is_history_growth_and_reuses_the_measured_prompt():
    # The best case: system prompt untouched, one more user turn appended. The whole
    # previous request is a literal prefix, so its own measured input_tokens is reusable.
    previous = _chain(
        _m("system", "SYS"), _m("user", "q1"), _m("assistant", "a1"), input_tokens=900
    )
    messages = [
        _m("system", "SYS"),
        _m("user", "q1"),
        _m("assistant", "a1"),
        _m("user", "q2"),
    ]
    probe = _probe(messages, previous, hit=896, miss=104, input_tokens=1000)
    assert probe.breach == BREACH_HISTORY_GROWTH
    assert probe.reusable_tokens == 900
    assert probe.reusable_basis == BASIS_MEASURED
    assert probe.stable_prefix_messages == 3
    assert probe.forfeited_tokens == 4  # block-granularity shortfall, not a breach
    assert probe.hit_ratio == 0.896
    assert probe.chain_calls == 2


def test_resent_identical_request_is_not_growth():
    previous = _chain(_m("system", "SYS"), _m("user", "q1"), input_tokens=500)
    messages = [_m("system", "SYS"), _m("user", "q1")]
    probe = _probe(messages, previous, hit=500, miss=0, input_tokens=500)
    assert probe.breach == BREACH_IDENTICAL
    assert probe.forfeited_tokens == 0


def test_system_prompt_edit_forfeits_the_whole_history_behind_it():
    # The audit's core claim: the provider matches ONE token prefix, so editing the system
    # message (even at its tail) throws away every history token that follows it.
    previous = _chain(
        _m("system", "SYS-v1"), _m("user", "q1"), _m("assistant", "a1"), input_tokens=900
    )
    messages = [_m("system", "SYS-v2"), _m("user", "q1"), _m("assistant", "a1")]
    probe = _probe(messages, previous, hit=0, miss=1000, input_tokens=1000)
    assert probe.breach == BREACH_SYSTEM_PROMPT
    assert probe.stable_prefix_messages == 0
    assert probe.stable_prefix_chars == 0
    assert probe.reusable_tokens == 0
    assert probe.reusable_basis == BASIS_NONE
    assert probe.hit_ratio == 0.0


def test_mid_history_rewrite_keeps_only_the_leading_messages():
    previous = _chain(
        _m("system", "SYS"),
        _m("user", "q1"),
        _m("assistant", "a1"),
        _m("user", "q2"),
        input_tokens=1000,
    )
    messages = [
        _m("system", "SYS"),
        _m("user", "q1"),
        _m("assistant", "a1-COMPACTED"),
        _m("user", "q2"),
    ]
    probe = _probe(messages, previous, hit=200, miss=800, input_tokens=1000)
    assert probe.breach == BREACH_HISTORY_REWRITE
    assert probe.stable_prefix_messages == 2
    # No measured token count for a partial prefix — prorated by chars and flagged as such.
    assert probe.reusable_basis == BASIS_ESTIMATED
    assert 0 < probe.reusable_tokens < 1000
    assert probe.forfeited_tokens == max(probe.reusable_tokens - 200, 0)


def test_first_message_change_without_a_system_message_is_not_blamed_on_the_prompt():
    previous = _chain(_m("user", "q1"), _m("assistant", "a1"), input_tokens=100)
    messages = [_m("user", "q1-edited"), _m("assistant", "a1")]
    probe = _probe(messages, previous, hit=0, miss=100, input_tokens=100)
    assert probe.breach == BREACH_HEAD_REWRITE


def test_silent_provider_is_not_reported_as_a_zero_percent_hit():
    # No cache fields on the wire → we know nothing. Reporting forfeited tokens here would
    # invent a finding out of a provider that simply does not speak about caching.
    previous = _chain(_m("system", "SYS"), _m("user", "q1"), input_tokens=900)
    messages = [_m("system", "SYS"), _m("user", "q1"), _m("user", "q2")]
    probe = _probe(messages, previous, hit=0, miss=0, input_tokens=1000)
    assert probe.cache_reported is False
    assert probe.forfeited_tokens == 0
    assert probe.reusable_tokens == 900  # structurally reusable; billing unknown


def test_cache_reported_true_when_only_a_miss_split_is_returned():
    probe = _probe(
        [_m("system", "SYS")],
        _chain(_m("system", "SYS"), input_tokens=10),
        hit=0,
        miss=10,
        input_tokens=10,
    )
    assert probe.cache_reported is True


def test_tool_call_arguments_are_part_of_a_message_identity():
    # A ReAct round appends assistant(tool_calls) + tool result; both must be diffable or
    # every round would look "identical" and the growth attribution would be wrong.
    from agentcore.llm.provider.protocol import ToolCall, ToolCallFunction

    call_a = ToolCall(id="c1", function=ToolCallFunction(name="file_read", arguments='{"p":"a"}'))
    call_b = ToolCall(id="c1", function=ToolCallFunction(name="file_read", arguments='{"p":"b"}'))
    a, _ = message_fingerprints([LLMMessage(role="assistant", tool_calls=[call_a])])
    b, _ = message_fingerprints([LLMMessage(role="assistant", tool_calls=[call_b])])
    assert a != b


# --- 击穿归因: which prompt section moved ----------------------------------------------


def _record_ceo_turn(*, conversation_id: str, turn_id: str, folder_catalog: str, tail: str):
    """Mimic the three nested CEO layers: shared base → ceo_chat → per-turn tail."""
    shared = [("base", "BASE"), ("runtime_context", "DATE"), ("memory_rules", "RULES")]
    shared_render = "\n".join(text for _, text in shared)
    record_prompt_sections(
        scope="shared_base",
        sections=shared,
        conversation_id=conversation_id,
        turn_id=turn_id,
    )
    ceo = [("ceo_base", shared_render), ("ceo_core", "CORE"), ("folder_catalog", folder_catalog)]
    ceo_render = "\n".join(text for _, text in ceo)
    record_prompt_sections(
        scope="ceo_chat", sections=ceo, conversation_id=conversation_id, turn_id=turn_id
    )
    record_prompt_sections(
        scope="ceo_turn",
        sections=[("ceo_prompt", ceo_render), ("workspace_context", tail)],
        conversation_id=conversation_id,
        turn_id=turn_id,
    )


def test_flatten_splices_nested_layers_into_leaves_in_render_order():
    _record_ceo_turn(conversation_id="c1", turn_id="t1", folder_catalog="CAT", tail="FILES")
    from agentcore.observability.prefix_cache import _conversation_sections

    leaves = flatten_sections(_conversation_sections["c1"].scopes)
    assert [leaf.key for leaf in leaves] == [
        "base",
        "runtime_context",
        "memory_rules",
        "ceo_core",
        "folder_catalog",
        "workspace_context",
    ]


def test_unrecorded_layer_degrades_to_the_container_section():
    # A layer nobody tracked cannot be spliced; attribution gets coarser, never wrong.
    record_prompt_sections(
        scope="ceo_turn",
        sections=[("ceo_prompt", "WHOLE-PROMPT"), ("workspace_context", "FILES")],
        conversation_id="c1",
        turn_id="t1",
    )
    from agentcore.observability.prefix_cache import _conversation_sections

    leaves = flatten_sections(_conversation_sections["c1"].scopes)
    assert [leaf.key for leaf in leaves] == ["ceo_prompt", "workspace_context"]


def test_a_second_shared_base_in_the_same_turn_does_not_break_the_chain():
    # Workers re-assemble the shared base mid-turn; letting that overwrite the CEO's copy
    # would leave ``ceo_base`` pointing at a digest nothing resolves to.
    _record_ceo_turn(conversation_id="c1", turn_id="t1", folder_catalog="CAT", tail="FILES")
    record_prompt_sections(
        scope="shared_base",
        sections=[("base", "WORKER-BASE")],
        conversation_id="c1",
        turn_id="t1",
    )
    from agentcore.observability.prefix_cache import _conversation_sections

    leaves = flatten_sections(_conversation_sections["c1"].scopes)
    assert leaves[0].key == "base"
    assert "folder_catalog" in [leaf.key for leaf in leaves]


def test_delta_is_not_comparable_until_a_second_turn():
    _record_ceo_turn(conversation_id="c1", turn_id="t1", folder_catalog="CAT", tail="FILES")
    assert prompt_section_delta("c1").comparable is False
    assert prompt_section_delta("never-seen").comparable is False


def test_delta_names_the_reordered_project_catalog_not_the_container():
    # 项目清单按最近活跃排序、却坐在稳定前缀中段 —— 这正是要能被单独指认的嫌疑段。
    _record_ceo_turn(conversation_id="c1", turn_id="t1", folder_catalog="A,B", tail="FILES")
    _record_ceo_turn(conversation_id="c1", turn_id="t2", folder_catalog="B,A", tail="FILES")
    delta = prompt_section_delta("c1")
    assert delta.comparable is True
    assert delta.first_changed == "folder_catalog"
    assert delta.changed == ("folder_catalog",)


def test_delta_reports_the_volatile_tail_when_only_the_file_index_moved():
    _record_ceo_turn(conversation_id="c1", turn_id="t1", folder_catalog="A", tail="FILES-1")
    _record_ceo_turn(conversation_id="c1", turn_id="t2", folder_catalog="A", tail="FILES-2")
    delta = prompt_section_delta("c1")
    assert delta.first_changed == "workspace_context"
    assert delta.changed == ("workspace_context",)


def test_delta_reports_every_changed_leaf_but_blames_the_earliest():
    _record_ceo_turn(conversation_id="c1", turn_id="t1", folder_catalog="A", tail="FILES-1")
    _record_ceo_turn(conversation_id="c1", turn_id="t2", folder_catalog="B", tail="FILES-2")
    delta = prompt_section_delta("c1")
    assert delta.first_changed == "folder_catalog"
    assert set(delta.changed) == {"folder_catalog", "workspace_context"}


def test_section_attribution_rides_only_a_system_prompt_breach():
    delta = SectionDelta(
        comparable=True, first_changed="folder_catalog", changed=("folder_catalog",)
    )
    previous = _chain(_m("system", "SYS-v1"), _m("user", "q1"), input_tokens=100)
    breached = _probe(
        [_m("system", "SYS-v2"), _m("user", "q1")],
        previous,
        input_tokens=100,
        delta=delta,
    )
    assert breached.breach_section == "folder_catalog"
    # A pure append did not break the prompt, so last turn's section churn is not the story.
    grown = _probe(
        [_m("system", "SYS-v1"), _m("user", "q1"), _m("user", "q2")],
        previous,
        input_tokens=120,
        delta=delta,
    )
    assert grown.breach == BREACH_HISTORY_GROWTH
    assert grown.breach_section == ""
    assert grown.changed_sections == ()


# --- emit seam --------------------------------------------------------------------------


def test_observe_emits_one_line_and_advances_the_chain(monkeypatch):
    captured: list[dict] = []

    class _Spy:
        def debug(self, event: str, **kwargs: object) -> None:
            captured.append({"event": event, "level": "debug", **kwargs})

        def info(self, event: str, **kwargs: object) -> None:
            captured.append({"event": event, "level": "info", **kwargs})

    monkeypatch.setattr("agentcore.observability.prefix_cache.logger", _Spy())
    bind_log_context(conversation_id="conv-1", trace_id="t1", agent_id="ceo")
    first = [LLMMessage(role="system", content="SYS"), LLMMessage(role="user", content="q1")]
    usage = TokenUsage(input_tokens=800, cache_hit_tokens=0, cache_miss_tokens=800)
    observe_prefix_cache(
        scenario="chat",
        model="deepseek-chat",
        messages=first,
        input_tokens=usage.input_tokens,
        cache_hit_tokens=usage.cache_hit_tokens,
        cache_miss_tokens=usage.cache_miss_tokens,
    )
    second = [
        *first,
        LLMMessage(role="assistant", content="a1"),
        LLMMessage(role="user", content="q2"),
    ]
    probe = observe_prefix_cache(
        scenario="chat",
        model="deepseek-chat",
        messages=second,
        input_tokens=1000,
        cache_hit_tokens=768,
        cache_miss_tokens=232,
    )
    assert [row["event"] for row in captured] == ["cost.prefix_cache", "cost.prefix_cache"]
    assert all(row["level"] == "debug" for row in captured)
    assert captured[0]["breach"] == BREACH_COLD_CHAIN
    assert captured[1]["breach"] == BREACH_HISTORY_GROWTH
    assert captured[1]["reusable_tokens"] == 800
    assert captured[1]["forfeited_tokens"] == 32
    assert probe is not None and probe.chain_calls == 2


def test_observe_skips_calls_with_no_chain_identity_or_no_tokens(monkeypatch):
    captured: list[dict] = []

    class _Spy:
        def debug(self, event: str, **kwargs: object) -> None:
            captured.append({"event": event, **kwargs})

        def info(self, event: str, **kwargs: object) -> None:
            captured.append({"event": event, **kwargs})

    monkeypatch.setattr("agentcore.observability.prefix_cache.logger", _Spy())
    messages = [LLMMessage(role="system", content="SYS")]
    # No conversation bound (catalog probe / eval): nothing to compare across turns.
    assert (
        observe_prefix_cache(
            scenario="chat",
            model="m",
            messages=messages,
            input_tokens=10,
            cache_hit_tokens=0,
            cache_miss_tokens=0,
        )
        is None
    )
    bind_log_context(conversation_id="conv-1", trace_id="t1")
    # A stubbed / usage-less call carries no measurable prompt.
    assert (
        observe_prefix_cache(
            scenario="chat",
            model="m",
            messages=messages,
            input_tokens=0,
            cache_hit_tokens=0,
            cache_miss_tokens=0,
        )
        is None
    )
    assert captured == []


def test_separate_runs_in_one_conversation_do_not_diff_against_each_other(monkeypatch):
    monkeypatch.setattr(
        "agentcore.observability.prefix_cache.logger",
        type("_Spy", (), {
            "info": lambda self, event, **kw: None,
            "debug": lambda self, event, **kw: None,
        })(),
    )
    messages = [LLMMessage(role="system", content="SYS"), LLMMessage(role="user", content="q")]
    bind_log_context(conversation_id="conv-1", trace_id="t1", agent_id="ceo")
    observe_prefix_cache(
        scenario="chat",
        model="m",
        messages=messages,
        input_tokens=100,
        cache_hit_tokens=0,
        cache_miss_tokens=100,
    )
    bind_log_context(run_id="run-9", agent_id="researcher")
    worker = observe_prefix_cache(
        scenario="agent",
        model="m",
        messages=messages,
        input_tokens=100,
        cache_hit_tokens=0,
        cache_miss_tokens=100,
    )
    assert worker is not None and worker.breach == BREACH_COLD_CHAIN


# --- 跨回合链: the CEO's chain is the conversation, not the per-turn captain run ---------


def _mute_probe_log(monkeypatch) -> None:
    monkeypatch.setattr(
        "agentcore.observability.prefix_cache.logger",
        type(
            "_Spy",
            (),
            {
                "info": lambda self, event, **kw: None,
                "debug": lambda self, event, **kw: None,
            },
        )(),
    )


def _ceo_turn(turn: str, run: str) -> None:
    """Enter the log scope of one CEO turn — a FRESH captain run each time.

    ``pipeline/run.py`` mints ``captain_run_id`` per user turn and uses it for both
    ``run_id`` and ``agent_id``; ``cost_role`` stays ``captain`` (executor + turn entry).
    """
    bind_log_context(trace_id=turn, run_id=run, agent_id=run, cost_role="captain")


def test_a_new_captain_run_each_turn_no_longer_restarts_the_ceo_chain(monkeypatch):
    # 这是本次修的 bug：链 key 拼了 agent_id + run_id，而 CEO 每个用户回合都新铸一个 captain
    # run，所以每回合首调结构上必然 cold_chain / reusable_tokens=0 —— 本模块存在的意义
    # （跨回合比对）一次都没发生过。
    _mute_probe_log(monkeypatch)
    bind_log_context(conversation_id="conv-ceo")
    _ceo_turn("t1", "captain-run-1")
    turn_one = [_m("system", "SYS"), _m("user", "q1")]
    observe_prefix_cache(
        scenario="chat",
        model="m",
        messages=turn_one,
        input_tokens=800,
        cache_hit_tokens=0,
        cache_miss_tokens=800,
    )
    _ceo_turn("t2", "captain-run-2")
    probe = observe_prefix_cache(
        scenario="chat",
        model="m",
        messages=[*turn_one, _m("assistant", "a1"), _m("user", "q2")],
        input_tokens=1000,
        cache_hit_tokens=760,
        cache_miss_tokens=240,
    )
    assert probe is not None
    assert probe.breach == BREACH_HISTORY_GROWTH
    assert probe.chain_calls == 2
    assert probe.reusable_tokens == 800  # turn 1's own measured prompt
    assert probe.reusable_basis == BASIS_MEASURED
    assert probe.forfeited_tokens == 40


def test_a_delegated_run_never_lands_on_the_ceo_chain(monkeypatch):
    # 合链只对 CEO 开；worker / 辩手 各自 run 的 ReAct 链必须保持隔离，否则它们会互相
    # 「击穿」对方，把真实的击穿归因淹掉。
    _mute_probe_log(monkeypatch)
    bind_log_context(conversation_id="conv-team")
    _ceo_turn("t1", "captain-run-1")
    ceo_messages = [_m("system", "CEO-SYS"), _m("user", "q1")]
    observe_prefix_cache(
        scenario="chat",
        model="m",
        messages=ceo_messages,
        input_tokens=800,
        cache_hit_tokens=0,
        cache_miss_tokens=800,
    )
    for role, run in (("member", "worker-1"), ("member", "worker-2"), ("arena", "debater-1")):
        bind_log_context(run_id=run, agent_id=run, cost_role=role)
        probe = observe_prefix_cache(
            scenario="agent",
            model="m",
            messages=[_m("system", "WORKER-SYS"), _m("user", "task")],
            input_tokens=300,
            cache_hit_tokens=0,
            cache_miss_tokens=300,
        )
        assert probe is not None and probe.breach == BREACH_COLD_CHAIN
    # …and the CEO's own chain survived the workers running under the same conversation.
    _ceo_turn("t2", "captain-run-2")
    resumed = observe_prefix_cache(
        scenario="chat",
        model="m",
        messages=[*ceo_messages, _m("assistant", "a1"), _m("user", "q2")],
        input_tokens=1000,
        cache_hit_tokens=800,
        cache_miss_tokens=200,
    )
    assert resumed is not None and resumed.breach == BREACH_HISTORY_GROWTH


def test_a_title_call_on_the_same_conversation_is_its_own_chain(monkeypatch):
    # Background chrome (title / compaction / memory) rides the same conversation but is a
    # different prompt shape — comparing it against the chat transcript would report a
    # breach on every line, so ``scenario`` stays in the key.
    _mute_probe_log(monkeypatch)
    bind_log_context(conversation_id="conv-title")
    _ceo_turn("t1", "captain-run-1")
    observe_prefix_cache(
        scenario="chat",
        model="m",
        messages=[_m("system", "SYS"), _m("user", "q1")],
        input_tokens=800,
        cache_hit_tokens=0,
        cache_miss_tokens=800,
    )
    title = observe_prefix_cache(
        scenario="title",
        model="m",
        messages=[_m("system", "TITLE-SYS"), _m("user", "q1")],
        input_tokens=200,
        cache_hit_tokens=0,
        cache_miss_tokens=200,
    )
    assert title is not None and title.breach == BREACH_COLD_CHAIN


def test_the_second_ceo_turn_names_the_section_that_broke_the_prefix(monkeypatch):
    # 段级归因只在 breach=system_prompt 时才填，而 CEO 主路径以前永远停在 cold_chain ——
    # 所以 breach_section 在生产里从未点亮过。合链后它才第一次可读。
    _mute_probe_log(monkeypatch)
    bind_log_context(conversation_id="conv-attr")
    _ceo_turn("t1", "captain-run-1")
    _record_ceo_turn(conversation_id="conv-attr", turn_id="t1", folder_catalog="A", tail="FILES-1")
    observe_prefix_cache(
        scenario="chat",
        model="m",
        messages=[_m("system", "SYS+FILES-1"), _m("user", "q1")],
        input_tokens=800,
        cache_hit_tokens=0,
        cache_miss_tokens=800,
    )
    _ceo_turn("t2", "captain-run-2")
    _record_ceo_turn(conversation_id="conv-attr", turn_id="t2", folder_catalog="A", tail="FILES-2")
    probe = observe_prefix_cache(
        scenario="chat",
        model="m",
        messages=[
            _m("system", "SYS+FILES-2"),
            _m("user", "q1"),
            _m("assistant", "a1"),
            _m("user", "q2"),
        ],
        input_tokens=1000,
        cache_hit_tokens=0,
        cache_miss_tokens=1000,
    )
    assert probe is not None
    assert probe.breach == BREACH_SYSTEM_PROMPT
    assert probe.breach_section == "workspace_context"
    assert probe.changed_sections == ("workspace_context",)
    assert probe.chain_calls == 2


# --- 日志聚合: the three questions, answered from the emitted rows -----------------------


def _row(**over):
    row = {
        "breach": BREACH_HISTORY_GROWTH,
        "breach_section": "",
        "cache_reported": True,
        "input_tokens": 1000,
        "cache_hit_tokens": 900,
        "forfeited_tokens": 0,
    }
    row.update(over)
    return row


def test_summary_excludes_silent_providers_from_every_ratio():
    from agentcore.observability.query.stats import prefix_cache_summary

    summary = prefix_cache_summary(
        [
            _row(),
            _row(cache_reported=False, cache_hit_tokens=0),
        ]
    )
    assert summary["calls"] == 2
    assert summary["cache_reported_calls"] == 1
    assert summary["cache_silent_calls"] == 1
    assert summary["hit_ratio"] == 0.9  # not 0.45 — the silent call is not a 0% hit


def test_summary_attributes_cost_to_breach_and_section():
    from agentcore.observability.query.stats import prefix_cache_summary

    summary = prefix_cache_summary(
        [
            _row(),
            _row(
                breach=BREACH_SYSTEM_PROMPT,
                breach_section="workspace_context",
                cache_hit_tokens=0,
                forfeited_tokens=900,
            ),
            _row(
                breach=BREACH_SYSTEM_PROMPT,
                breach_section="folder_catalog",
                cache_hit_tokens=0,
                forfeited_tokens=800,
            ),
        ]
    )
    assert summary["by_breach"][BREACH_SYSTEM_PROMPT]["calls"] == 2
    assert summary["by_breach"][BREACH_SYSTEM_PROMPT]["forfeited_tokens"] == 1700
    assert summary["by_breach"][BREACH_HISTORY_GROWTH]["hit_ratio"] == 0.9
    assert summary["by_section"] == {"workspace_context": 1, "folder_catalog": 1}


def test_summary_buckets_by_prompt_size():
    from agentcore.observability.query.stats import prefix_cache_summary

    summary = prefix_cache_summary(
        [
            _row(input_tokens=1_000, cache_hit_tokens=0),
            _row(input_tokens=30_000, cache_hit_tokens=24_000),
            _row(input_tokens=90_000, cache_hit_tokens=81_000),
        ]
    )
    assert set(summary["by_length"]) == {"<4k", "16k-64k", "≥64k"}
    assert summary["by_length"]["<4k"]["hit_ratio"] == 0.0
    assert summary["by_length"]["16k-64k"]["hit_ratio"] == 0.8
    assert summary["by_length"]["≥64k"]["hit_ratio"] == 0.9


# --- 装配行为一行未改 ---------------------------------------------------------------------


def test_tracking_changes_nothing_about_the_assembled_prompt():
    bind_log_context(conversation_id="conv-1", trace_id="t1")
    plain = (
        ContextAssembler()
        .add("base", "BASE", SectionOrder.BASE)
        .add("tail", "TAIL", SectionOrder.ATTACHMENT)
    )
    tracked = (
        ContextAssembler()
        .add("base", "BASE", SectionOrder.BASE)
        .add("tail", "TAIL", SectionOrder.ATTACHMENT)
    )
    assert tracked.track_sections(scope="unit") is tracked  # chainable
    assert tracked.render() == plain.render() == "BASE\nTAIL"
    assert [c.key for c in tracked.contributors()] == ["base", "tail"]


def test_the_real_ceo_layers_splice_into_leaf_sections():
    # End-to-end over the production composers: the outer layer must NOT stay stuck on its
    # ``ceo_prompt`` container. Guards the digest-splicing invariant against a future change
    # to how a layer joins its sections.
    from agentcore.runtime.resolve.prompt.compose import (
        assemble_system_prompt,
        compose_ceo_chat_prompt,
    )

    bind_log_context(conversation_id="conv-real", trace_id="t1")
    shared_base = assemble_system_prompt(
        rules_markdown="记住：用户偏好简洁",
    )
    ceo_prompt = compose_ceo_chat_prompt(
        shared_base,
        ceo_tool_names=set(),
        workspace_context="<workspace_context>本地桌面</workspace_context>",
    )
    (
        ContextAssembler()
        .add("ceo_prompt", ceo_prompt, SectionOrder.BASE)
        .add("workspace_context", "<workspace_file_index/>", SectionOrder.WORKSPACE_OVERVIEW)
        .observe(scope="ceo_turn", soft_cap=None)
    )
    from agentcore.observability.prefix_cache import _conversation_sections

    keys = [leaf.key for leaf in flatten_sections(_conversation_sections["conv-real"].scopes)]
    assert "ceo_prompt" not in keys and "ceo_base" not in keys  # containers were spliced
    assert keys[0] == "base"
    assert {"runtime_context", "workspace_facts", "memory_rules", "ceo_core"} <= set(keys)
    assert keys.index("ceo_core") < keys.index("workspace_facts")
    assert keys.index("workspace_facts") < keys.index("workspace_context")
    assert keys[-1] == "workspace_context"  # the volatile tail stays last


def test_a_growing_source_ledger_is_attributable_to_its_own_section():
    # CTX-A3: the 来源台账 hydrates from the whole conversation, so it is the tail section
    # most likely to break the prefix. While it was appended outside the assembler the
    # probe never saw it — a turn whose ONLY change was the ledger looked identical.
    from agentcore.runtime.pipeline.assemble import build_chat_system_prompt

    def _turn(sources: str) -> None:
        build_chat_system_prompt(
            ceo_prompt="CEO",
            workspace_overview="<workspace_file_index/>",
            recent_team_graph="",
            prior_delivery_gaps="",
            prior_delegate_retry="",
            prior_futile_retries="",
            attachment_context="",
            registered_sources=sources,
            soft_cap=None,
        )

    bind_log_context(conversation_id="conv-ledger", trace_id="t1")
    _turn("<registered_sources>\n- #r1\n</registered_sources>")
    bind_log_context(trace_id="t2")
    _turn("<registered_sources>\n- #r1\n- #r2\n</registered_sources>")

    delta = prompt_section_delta("conv-ledger")
    assert delta.comparable is True
    assert delta.first_changed == "registered_sources"
    assert delta.changed == ("registered_sources",)


def test_observe_carries_per_section_digests_for_offline_diffing(monkeypatch):
    captured: list[dict] = []

    class _Spy:
        def info(self, event: str, **kwargs: object) -> None:
            captured.append({"event": event, **kwargs})

    monkeypatch.setattr("agentcore.runtime.context.assembler.logger", _Spy())
    bind_log_context(conversation_id="conv-1", trace_id="t1")
    asm = (
        ContextAssembler()
        .add("base", "BASE", SectionOrder.BASE)
        .add("tail", "TAIL", SectionOrder.ATTACHMENT)
    )
    asm.observe(scope="unit", soft_cap=None)
    assert captured[0]["section_digests"] == {
        "base": digest_text("BASE"),
        "tail": digest_text("TAIL"),
    }
    # observe also feeds the probe, so the next turn can be attributed.
    assert prompt_section_delta("conv-1").comparable is False
    bind_log_context(trace_id="t2")
    ContextAssembler().add("base", "BASE", SectionOrder.BASE).add(
        "tail", "TAIL-2", SectionOrder.ATTACHMENT
    ).observe(scope="unit", soft_cap=None)
    assert prompt_section_delta("conv-1").first_changed == "tail"
