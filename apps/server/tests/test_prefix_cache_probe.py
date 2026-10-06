"""Unit tests for prefix-cache observability (审计议题 D4 · 只观测不改装配).

Covers the metric computation itself (breach classification, reusable / forfeited tokens,
hit ratio, provider silence) and the section ledger that names WHICH prompt section broke
the byte prefix — plus the guarantee that adding all of it changed no assembled prompt.
"""

from __future__ import annotations

import pytest

from agentcore.core.log_context import bind_log_context, log_context
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
    BREACH_TOOLS,
    OPENING_COLD,
    OPENING_SAME,
    OPENING_SYSTEM,
    OPENING_TOOLS,
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
    tools_fingerprint,
)
from agentcore.runtime.context import ContextAssembler, SectionOrder

_CHAIN_LOG_KEYS = (
    "conversation_id",
    "trace_id",
    "agent_id",
    "run_id",
    "cost_role",
    "message_id",
    "user_id",
)


def _drop_chain_log_ids() -> None:
    """Unbind chain ids without wiping the root ``traffic=test`` fixture."""
    from structlog.contextvars import get_contextvars, unbind_contextvars

    present = [k for k in _CHAIN_LOG_KEYS if k in get_contextvars()]
    if present:
        unbind_contextvars(*present)


@pytest.fixture(autouse=True)
def _clean_probe_state():
    # Prefix-cache ledger + chain ids. Do not clear_log_context — the root
    # ``_mark_test_traffic`` fixture holds ``bound_contextvars`` for the test.
    reset_prefix_cache_state()
    _drop_chain_log_ids()
    yield
    reset_prefix_cache_state()
    _drop_chain_log_ids()


def _m(role: str, content: str) -> LLMMessage:
    return LLMMessage(role=role, content=content)


def _chain(
    *messages: LLMMessage, input_tokens: int, calls: int = 1, tools: list | None = None
) -> ChainState:
    """The state a previous call on the same chain would have left behind."""
    digests, _ = message_fingerprints(messages)
    tools_digest, tools_count = tools_fingerprint(tools)
    return ChainState(
        digests=digests,
        input_tokens=input_tokens,
        calls=calls,
        tools_digest=tools_digest,
        tools_count=tools_count,
    )


def _probe(
    messages,
    previous,
    *,
    hit=0,
    miss=0,
    input_tokens=1000,
    delta=None,
    tools=None,
):
    digests, sizes = message_fingerprints(messages)
    tools_digest, tools_count = tools_fingerprint(tools)
    return compute_probe(
        digests=digests,
        sizes=sizes,
        first_role=messages[0].role,
        input_tokens=input_tokens,
        cache_hit_tokens=hit,
        cache_miss_tokens=miss,
        previous=previous,
        tools_digest=tools_digest,
        tools_count=tools_count,
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


_TOOL_A = [{"type": "function", "function": {"name": "read", "parameters": {}}}]
_TOOL_B = [
    {"type": "function", "function": {"name": "read", "parameters": {}}},
    {"type": "function", "function": {"name": "consult", "parameters": {}}},
]


def test_tools_table_change_is_tools_breach_not_history_growth():
    previous = _chain(
        _m("system", "SYS"),
        _m("user", "q1"),
        _m("assistant", "a1"),
        input_tokens=900,
        tools=_TOOL_A,
    )
    messages = [
        _m("system", "SYS"),
        _m("user", "q1"),
        _m("assistant", "a1"),
        _m("user", "q2"),
    ]
    probe = _probe(messages, previous, hit=0, miss=1100, input_tokens=1100, tools=_TOOL_B)
    assert probe.breach == BREACH_TOOLS
    assert probe.tools_changed is True
    assert probe.tools_count == 2
    assert probe.reusable_tokens == 0
    assert probe.reusable_basis == BASIS_NONE
    assert probe.forfeited_tokens == 0
    assert probe.breach_section == ""
    assert probe.hit_ratio == 0.0


def test_same_tools_plus_append_stays_history_growth():
    previous = _chain(
        _m("system", "SYS"),
        _m("user", "q1"),
        _m("assistant", "a1"),
        input_tokens=900,
        tools=_TOOL_A,
    )
    messages = [
        _m("system", "SYS"),
        _m("user", "q1"),
        _m("assistant", "a1"),
        _m("user", "q2"),
    ]
    probe = _probe(messages, previous, hit=896, miss=104, input_tokens=1000, tools=_TOOL_A)
    assert probe.breach == BREACH_HISTORY_GROWTH
    assert probe.tools_changed is False
    assert probe.tools_count == 1
    assert probe.reusable_tokens == 900


def test_tools_change_beats_system_prompt_in_hierarchy():
    previous = _chain(
        _m("system", "SYS-v1"), _m("user", "q1"), input_tokens=500, tools=_TOOL_A
    )
    messages = [_m("system", "SYS-v2"), _m("user", "q1")]
    probe = _probe(messages, previous, hit=0, miss=500, input_tokens=500, tools=_TOOL_B)
    assert probe.breach == BREACH_TOOLS
    assert probe.tools_changed is True
    assert probe.breach_section == ""


def test_tools_fingerprint_ignores_key_order_not_list_order():
    a = [{"type": "function", "function": {"name": "a", "z": 1, "m": 2}}]
    b = [{"type": "function", "function": {"m": 2, "name": "a", "z": 1}}]
    assert tools_fingerprint(a) == tools_fingerprint(b)
    swapped = [
        {"type": "function", "function": {"name": "b"}},
        {"type": "function", "function": {"name": "a"}},
    ]
    ordered = [
        {"type": "function", "function": {"name": "a"}},
        {"type": "function", "function": {"name": "b"}},
    ]
    assert tools_fingerprint(swapped) != tools_fingerprint(ordered)
    assert tools_fingerprint(None) == tools_fingerprint([]) == ("", 0)


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


def test_dropped_ceo_envelope_across_turns_is_history_rewrite_not_growth():
    """Anti-pattern: T2 rebuilds ``system → history → new envelope → new user``.

    The previous turn's envelope is not in chat history, so it vanishes from the
    prefix. That is a mid-list rewrite (DeepSeek Example 2), not a pure append.
    """
    from agentcore.runtime.resolve.prompt.envelope import (
        TURN_ENVELOPE_FENCE,
        opening_ceo_messages,
    )

    env1 = f"{TURN_ENVELOPE_FENCE}\n<运行时/>"
    env2 = f"{TURN_ENVELOPE_FENCE}\n<运行时/>\n<表格/>"
    turn1 = opening_ceo_messages(
        system_prompt="SYS",
        history=None,
        turn_envelope=env1,
        user_content="q1",
    )
    previous = _chain(*turn1, input_tokens=900)
    turn2 = opening_ceo_messages(
        system_prompt="SYS",
        history=[_m("user", "q1"), _m("assistant", "a1")],
        turn_envelope=env2,
        user_content="q2",
    )
    probe = _probe(turn2, previous, hit=200, miss=1000, input_tokens=1200)
    assert probe.breach == BREACH_HISTORY_REWRITE
    assert probe.stable_prefix_messages == 1  # frozen system only
    assert probe.reusable_basis == BASIS_ESTIMATED


def test_product_cross_turn_replays_stored_envelope_as_history_growth():
    """Product T2: prior envelope stays in the window; this turn only appends."""
    from agentcore.runtime.resolve.prompt.envelope import (
        TURN_ENVELOPE_FENCE,
        opening_ceo_messages,
    )

    env1 = f"{TURN_ENVELOPE_FENCE}\n<运行时/>"
    env2 = f"{TURN_ENVELOPE_FENCE}\n<表格/>"
    turn1 = opening_ceo_messages(
        system_prompt="SYS",
        history=None,
        turn_envelope=env1,
        user_content="q1",
    )
    previous = _chain(*turn1, input_tokens=900)
    turn2 = opening_ceo_messages(
        system_prompt="SYS",
        history=[
            _m("user", env1),
            _m("user", "q1"),
            _m("assistant", "a1"),
        ],
        turn_envelope=env2,
        user_content="q2",
    )
    probe = _probe(turn2, previous, hit=896, miss=200, input_tokens=1100)
    assert probe.breach == BREACH_HISTORY_GROWTH
    assert probe.reusable_tokens == 900
    assert probe.reusable_basis == BASIS_MEASURED


def test_keeping_prior_ceo_envelope_is_history_growth():
    """Industry shape: once an envelope is sent, it stays; the next turn only appends."""
    from agentcore.runtime.resolve.prompt.envelope import (
        TURN_ENVELOPE_FENCE,
        opening_ceo_messages,
    )

    env1 = f"{TURN_ENVELOPE_FENCE}\n<运行时/>"
    env2 = f"{TURN_ENVELOPE_FENCE}\n<表格/>"
    turn1 = opening_ceo_messages(
        system_prompt="SYS",
        history=None,
        turn_envelope=env1,
        user_content="q1",
    )
    previous = _chain(*turn1, input_tokens=900)
    kept = [
        *turn1,
        _m("assistant", "a1"),
        _m("user", env2),
        _m("user", "q2"),
    ]
    probe = _probe(kept, previous, hit=896, miss=200, input_tokens=1100)
    assert probe.breach == BREACH_HISTORY_GROWTH
    assert probe.reusable_tokens == 900
    assert probe.reusable_basis == BASIS_MEASURED


def test_identical_ceo_envelope_omitted_is_history_growth():
    """Unchanged environment: do not re-append the snapshot; user text still grows."""
    from agentcore.runtime.resolve.prompt.envelope import (
        TURN_ENVELOPE_FENCE,
        opening_ceo_messages,
    )

    env = f"{TURN_ENVELOPE_FENCE}\n<运行时/>"
    turn1 = opening_ceo_messages(
        system_prompt="SYS",
        history=None,
        turn_envelope=env,
        user_content="q1",
    )
    previous = _chain(*turn1, input_tokens=900)
    turn2 = opening_ceo_messages(
        system_prompt="SYS",
        history=[
            _m("user", env),
            _m("user", "q1"),
            _m("assistant", "a1"),
        ],
        turn_envelope=env,
        user_content="q2",
    )
    probe = _probe(turn2, previous, hit=896, miss=80, input_tokens=980)
    assert probe.breach == BREACH_HISTORY_GROWTH
    assert probe.reusable_tokens == 900
    assert [m.content for m in turn2].count(env) == 1


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

    call_a = ToolCall(id="c1", function=ToolCallFunction(name="read", arguments='{"p":"a"}'))
    call_b = ToolCall(id="c1", function=ToolCallFunction(name="read", arguments='{"p":"b"}'))
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


def test_flatten_splices_when_outer_layer_is_exactly_the_inner_render():
    # No volatile tail: ceo_turn is one section whose text equals the ceo_chat join.
    # Last-write on render_digest would keep the container and report ``ceo_prompt``.
    record_prompt_sections(
        scope="shared_base",
        sections=[("base", "BASE")],
        conversation_id="c1",
        turn_id="t1",
    )
    inner_render = "BASE\nFACTS"
    record_prompt_sections(
        scope="ceo_chat",
        sections=[("ceo_base", "BASE"), ("workspace_facts", "FACTS")],
        conversation_id="c1",
        turn_id="t1",
    )
    record_prompt_sections(
        scope="ceo_turn",
        sections=[("ceo_prompt", inner_render)],
        conversation_id="c1",
        turn_id="t1",
    )
    from agentcore.observability.prefix_cache import _conversation_sections

    leaves = flatten_sections(_conversation_sections["c1"].scopes)
    assert [leaf.key for leaf in leaves] == ["base", "workspace_facts"]


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


def test_observe_tools_promotion_is_tools_not_history_growth(monkeypatch):
    monkeypatch.setattr(
        "agentcore.observability.prefix_cache.logger",
        type("_Spy", (), {"debug": lambda self, event, **kw: None, "info": lambda self, event, **kw: None})(),
    )
    bind_log_context(conversation_id="conv-tools", trace_id="t1", agent_id="ceo")
    first = [LLMMessage(role="system", content="SYS"), LLMMessage(role="user", content="q1")]
    observe_prefix_cache(
        scenario="chat",
        model="m",
        messages=first,
        input_tokens=800,
        cache_hit_tokens=0,
        cache_miss_tokens=800,
        tools=_TOOL_A,
    )
    second = [
        *first,
        LLMMessage(role="assistant", content="a1"),
        LLMMessage(role="user", content="q2"),
    ]
    probe = observe_prefix_cache(
        scenario="chat",
        model="m",
        messages=second,
        input_tokens=1000,
        cache_hit_tokens=0,
        cache_miss_tokens=1000,
        tools=_TOOL_B,
    )
    assert probe is not None
    assert probe.breach == BREACH_TOOLS
    assert probe.tools_changed is True
    assert probe.tools_count == 2


def test_silent_cache_split_logs_wire_usage_keys():
    from structlog.testing import capture_logs

    from agentcore.llm.observability import log_llm_call

    bind_log_context(conversation_id="conv-keys", trace_id="t-keys", user_id="user-1")
    usage = TokenUsage.from_openai_wire(
        {"prompt_tokens": 100, "completion_tokens": 4, "total_tokens": 104}
    )
    with capture_logs() as caps:
        log_llm_call(
            scenario="chat",
            model="deepseek-flash",
            usage=usage,
            finish_reason="stop",
            latency_ms=10,
            stream=True,
            messages=[_m("system", "SYS"), _m("user", "q")],
        )
    call = next(row for row in caps if row.get("event") == "llm.call")
    assert call["cache_hit_tokens"] == 0
    assert call["cache_miss_tokens"] == 0
    assert call["usage_keys"] == "completion_tokens,prompt_tokens,total_tokens"


def test_opening_vs_prev_spans_conversations_for_the_same_user(monkeypatch):
    """A new chat is cold_chain, but the opening fingerprint still compares to the last chat."""
    monkeypatch.setattr(
        "agentcore.observability.prefix_cache.logger",
        type(
            "_Spy",
            (),
            {
                "debug": lambda self, event, **kw: None,
                "info": lambda self, event, **kw: None,
            },
        )(),
    )
    bind_log_context(user_id="user-1", conversation_id="conv-a", trace_id="t1")
    first = [LLMMessage(role="system", content="SYS"), LLMMessage(role="user", content="你好")]
    cold = observe_prefix_cache(
        scenario="chat",
        model="deepseek-flash",
        messages=first,
        input_tokens=4496,
        cache_hit_tokens=0,
        cache_miss_tokens=4496,
        tools=_TOOL_A,
    )
    assert cold is not None
    assert cold.breach == BREACH_COLD_CHAIN
    assert cold.opening_vs_prev == OPENING_COLD
    assert cold.tools_fp
    assert cold.system_fp

    bind_log_context(conversation_id="conv-b", trace_id="t2")
    same_opening = [
        LLMMessage(role="system", content="SYS"),
        LLMMessage(role="user", content="你好呀"),
    ]
    matched = observe_prefix_cache(
        scenario="chat",
        model="deepseek-flash",
        messages=same_opening,
        input_tokens=4497,
        cache_hit_tokens=0,
        cache_miss_tokens=4497,
        tools=_TOOL_A,
    )
    assert matched is not None
    assert matched.breach == BREACH_COLD_CHAIN
    assert matched.opening_vs_prev == OPENING_SAME
    assert matched.tools_fp == cold.tools_fp
    assert matched.system_fp == cold.system_fp

    tools_moved = observe_prefix_cache(
        scenario="chat",
        model="deepseek-flash",
        messages=same_opening,
        input_tokens=4497,
        cache_hit_tokens=0,
        cache_miss_tokens=4497,
        tools=_TOOL_B,
    )
    assert tools_moved is not None and tools_moved.opening_vs_prev == OPENING_TOOLS

    system_moved = observe_prefix_cache(
        scenario="chat",
        model="deepseek-flash",
        messages=[LLMMessage(role="system", content="SYS-2"), LLMMessage(role="user", content="你好")],
        input_tokens=4500,
        cache_hit_tokens=0,
        cache_miss_tokens=4500,
        tools=_TOOL_B,
    )
    assert system_moved is not None and system_moved.opening_vs_prev == OPENING_SYSTEM


def test_opening_vs_prev_stays_cold_without_a_user_id(monkeypatch):
    monkeypatch.setattr(
        "agentcore.observability.prefix_cache.logger",
        type(
            "_Spy",
            (),
            {
                "debug": lambda self, event, **kw: None,
                "info": lambda self, event, **kw: None,
            },
        )(),
    )
    bind_log_context(conversation_id="conv-anon", trace_id="t1")
    messages = [LLMMessage(role="system", content="SYS"), LLMMessage(role="user", content="q")]
    first = observe_prefix_cache(
        scenario="chat",
        model="m",
        messages=messages,
        input_tokens=10,
        cache_hit_tokens=0,
        cache_miss_tokens=10,
        tools=_TOOL_A,
    )
    second = observe_prefix_cache(
        scenario="chat",
        model="m",
        messages=messages,
        input_tokens=10,
        cache_hit_tokens=0,
        cache_miss_tokens=10,
        tools=_TOOL_A,
    )
    assert first is not None and first.opening_vs_prev == OPENING_COLD
    assert second is not None and second.opening_vs_prev == OPENING_COLD


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


def test_summary_buckets_tools_changed():
    from agentcore.observability.query.stats import prefix_cache_summary

    summary = prefix_cache_summary(
        [
            _row(tools_changed=False, cache_hit_tokens=900),
            _row(
                breach=BREACH_TOOLS,
                tools_changed=True,
                cache_hit_tokens=0,
                input_tokens=2000,
            ),
        ]
    )
    assert summary["by_tools"]["unchanged"]["hit_ratio"] == 0.9
    assert summary["by_tools"]["changed"]["hit_ratio"] == 0.0
    assert summary["by_tools"]["changed"]["calls"] == 1
    assert summary["by_breach"][BREACH_TOOLS]["calls"] == 1


def test_summary_samples_unusual_breaches_not_pure_append():
    from agentcore.observability.query.stats import PREFIX_SAMPLE_CAP, prefix_cache_summary

    rows = [
        _row(trace_id="t-grow", conversation_id="c-grow"),
        _row(
            breach=BREACH_HISTORY_REWRITE,
            trace_id="t-rw1",
            conversation_id="c-rw",
            scenario="chat",
            cost_role="captain",
        ),
        _row(
            breach=BREACH_HISTORY_REWRITE,
            trace_id="t-rw2",
            conversation_id="c-rw",
        ),
        _row(
            breach=BREACH_COLD_CHAIN,
            trace_id="t-cold",
            conversation_id="c-cold",
            scenario="agent",
        ),
    ]
    for i in range(PREFIX_SAMPLE_CAP + 2):
        rows.append(
            _row(
                breach=BREACH_COLD_CHAIN,
                trace_id=f"t-cold-{i}",
                conversation_id=f"c-cold-{i}",
            )
        )
    summary = prefix_cache_summary(rows)
    samples = summary["samples_by_breach"]
    assert "history_growth" not in samples
    assert [s["trace_id"] for s in samples["history_rewrite"]] == ["t-rw1"]
    assert samples["history_rewrite"][0]["conversation_id"] == "c-rw"
    assert samples["history_rewrite"][0]["input_tokens"] == "1000"
    assert len(samples["cold_chain"]) == PREFIX_SAMPLE_CAP


def test_summary_reads_llm_call_compact_fields():
    from agentcore.observability.query.stats import (
        prefix_cache_summary,
        prefix_rows_for_stats,
    )

    compact = {
        "prefix_breach": BREACH_HISTORY_GROWTH,
        "tools_changed": False,
        "tools_count": 3,
        "input_tokens": 1000,
        "cache_hit_tokens": 800,
        "cache_miss_tokens": 200,
        "cost_role": "captain",
    }
    summary = prefix_cache_summary([compact])
    assert summary["hit_ratio"] == 0.8
    assert summary["by_breach"][BREACH_HISTORY_GROWTH]["calls"] == 1
    assert summary["by_role"]["captain"]["hit_ratio"] == 0.8
    assert summary["has_forfeited"] is False
    rows, source = prefix_rows_for_stats([compact], [])
    assert source == "llm.call"
    assert rows == [compact]


def test_log_llm_call_attaches_compact_prefix_fields():
    from structlog.testing import capture_logs

    from agentcore.llm.observability import log_llm_call
    from agentcore.llm.profiles import DEEPSEEK_V4_FLASH

    bind_log_context(conversation_id="conv-compact", trace_id="t1", cost_role="captain")
    first = [_m("system", "SYS"), _m("user", "q1")]
    usage0 = TokenUsage(input_tokens=800, cache_hit_tokens=0, cache_miss_tokens=800)
    usage1 = TokenUsage(input_tokens=1000, cache_hit_tokens=0, cache_miss_tokens=1000)
    with capture_logs() as caps:
        log_llm_call(
            scenario="chat",
            model=DEEPSEEK_V4_FLASH,
            usage=usage0,
            finish_reason="stop",
            latency_ms=10,
            stream=False,
            messages=first,
            tools=_TOOL_A,
            credential_source="platform",
        )
        log_llm_call(
            scenario="chat",
            model=DEEPSEEK_V4_FLASH,
            usage=usage1,
            finish_reason="stop",
            latency_ms=12,
            stream=False,
            messages=[
                *first,
                _m("assistant", "a1"),
                _m("user", "q2"),
            ],
            tools=_TOOL_B,
            credential_source="platform",
        )
    calls = [c for c in caps if c.get("event") == "llm.call"]
    assert len(calls) == 2
    assert calls[0]["prefix_breach"] == BREACH_COLD_CHAIN
    assert calls[0]["opening_vs_prev"] == OPENING_COLD
    assert calls[0]["tools_changed"] is False
    assert calls[0]["tools_count"] == 1
    assert calls[0]["tools_fp"]
    assert calls[0]["system_fp"]
    assert calls[1]["prefix_breach"] == BREACH_TOOLS
    assert calls[1]["tools_changed"] is True
    assert calls[1]["tools_count"] == 2
    assert "forfeited_tokens" not in calls[1]
    assert "reusable_tokens" not in calls[1]


def test_log_llm_call_dropped_envelope_is_history_rewrite():
    """Writing llm.call (no live model) still attributes T2 dropped-envelope as rewrite."""
    from structlog.testing import capture_logs

    from agentcore.llm.observability import log_llm_call
    from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
    from agentcore.runtime.resolve.prompt.envelope import (
        TURN_ENVELOPE_FENCE,
        opening_ceo_messages,
    )

    env1 = f"{TURN_ENVELOPE_FENCE}\n<运行时/>"
    env2 = f"{TURN_ENVELOPE_FENCE}\n<运行时/>\n<表格/>"
    turn1 = opening_ceo_messages(
        system_prompt="SYS",
        history=None,
        turn_envelope=env1,
        user_content="q1",
    )
    turn2 = opening_ceo_messages(
        system_prompt="SYS",
        history=[_m("user", "q1"), _m("assistant", "a1")],
        turn_envelope=env2,
        user_content="q2",
    )
    bind_log_context(conversation_id="conv-rw-log", trace_id="t-rw-log", cost_role="captain")
    usage0 = TokenUsage(input_tokens=800, cache_hit_tokens=0, cache_miss_tokens=800)
    usage1 = TokenUsage(input_tokens=1200, cache_hit_tokens=200, cache_miss_tokens=1000)
    with capture_logs() as caps:
        log_llm_call(
            scenario="chat",
            model=DEEPSEEK_V4_FLASH,
            usage=usage0,
            finish_reason="stop",
            latency_ms=10,
            stream=False,
            messages=turn1,
            tools=_TOOL_A,
            credential_source="platform",
        )
        log_llm_call(
            scenario="chat",
            model=DEEPSEEK_V4_FLASH,
            usage=usage1,
            finish_reason="stop",
            latency_ms=12,
            stream=False,
            messages=turn2,
            tools=_TOOL_A,
            credential_source="platform",
        )
    calls = [c for c in caps if c.get("event") == "llm.call"]
    assert len(calls) == 2
    assert calls[0]["prefix_breach"] == BREACH_COLD_CHAIN
    assert calls[1]["prefix_breach"] == BREACH_HISTORY_REWRITE
    assert calls[1]["tools_changed"] is False


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


def test_empty_turn_id_then_labelled_keeps_nested_scopes():
    """A layer recorded before trace_id is bound must not look like a new turn."""
    record_prompt_sections(
        scope="shared_base",
        sections=[("base", "BASE")],
        conversation_id="c-adopt",
        turn_id="",
    )
    record_prompt_sections(
        scope="ceo_chat",
        sections=[("ceo_base", "BASE"), ("ceo_core", "CORE")],
        conversation_id="c-adopt",
        turn_id="t1",
    )
    from agentcore.observability.prefix_cache import _conversation_sections

    scopes = _conversation_sections["c-adopt"].scopes
    assert set(scopes) == {"shared_base", "ceo_chat"}
    assert _conversation_sections["c-adopt"].turn_id == "t1"


def test_the_real_ceo_layers_splice_into_leaf_sections():
    # End-to-end over the production composers: the outer layer must NOT stay stuck on its
    # ``ceo_prompt`` container. Guards the digest-splicing invariant against a future change
    # to how a layer joins its sections.
    from agentcore.runtime.resolve.prompt.compose import (
        assemble_system_prompt,
        compose_ceo_chat_prompt,
    )

    cid = "conv-real-splice"
    with log_context(conversation_id=cid, trace_id="t-splice"):
        shared_base = assemble_system_prompt(
            rules_markdown="记住：用户偏好简洁",
        )
        ceo_prompt = compose_ceo_chat_prompt(
            shared_base,
            ceo_tool_names=set(),
        )
        (
            ContextAssembler()
            .add("ceo_prompt", ceo_prompt, SectionOrder.BASE)
            .observe(scope="ceo_turn")
        )
    from agentcore.observability.prefix_cache import _conversation_sections

    keys = [leaf.key for leaf in flatten_sections(_conversation_sections[cid].scopes)]
    assert "ceo_prompt" not in keys and "ceo_base" not in keys  # containers were spliced
    # Empty base and CEO core are skipped (Assembler omits falsy fragments).
    assert "base" not in keys
    assert "ceo_core" not in keys
    assert keys[0] == "memory_rules"
    assert "runtime_context" not in keys
    assert "workspace_facts" not in keys
    assert "</工作区>" not in ceo_prompt


def test_a_growing_table_section_is_attributable_to_its_own_section():
    from agentcore.runtime.resolve.prompt import render_ceo_turn_envelope

    def _turn(table: str) -> None:
        render_ceo_turn_envelope(
            attachment_context="",
            table_context=table,
            include_runtime=False,
        )

    bind_log_context(conversation_id="conv-ledger", trace_id="t1")
    _turn("<表格>\n行 1\n</表格>")
    bind_log_context(trace_id="t2")
    _turn("<表格>\n行 1\n行 2\n</表格>")

    delta = prompt_section_delta("conv-ledger")
    assert delta.comparable is True
    assert delta.first_changed == "table_context"
    assert delta.changed == ("table_context",)


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
    asm.observe(scope="unit")
    assert captured[0]["section_digests"] == {
        "base": digest_text("BASE"),
        "tail": digest_text("TAIL"),
    }
    # observe also feeds the probe, so the next turn can be attributed.
    assert prompt_section_delta("conv-1").comparable is False
    bind_log_context(trace_id="t2")
    ContextAssembler().add("base", "BASE", SectionOrder.BASE).add(
        "tail", "TAIL-2", SectionOrder.ATTACHMENT
    ).observe(scope="unit")
    assert prompt_section_delta("conv-1").first_changed == "tail"
