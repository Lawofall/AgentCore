"""Tests for convergence governance (runtime.loop_controller).

Pure logic — no LLM, no I/O. Covers the fingerprint, failure-repeat / A-B-A-B
stuck patterns, detection priority, and the two-strike NUDGE→FINALIZE ladder
(including the window clear that prevents a stale pattern from finalizing
prematurely). Identical investigation success (re-read / paging) is not stuck;
identical non-investigation success is ``REPEATED_CALL``.
"""

import pytest

from agentcore.runtime.loop_controller import (
    Intervention,
    LoopController,
    StuckReason,
    ToolAttempt,
    fingerprint_tool_call,
)


def _ok(fp: str, name: str = "t") -> ToolAttempt:
    return ToolAttempt(fingerprint=fp, tool_name=name, success=True)


def _fail(fp: str, name: str = "t") -> ToolAttempt:
    return ToolAttempt(fingerprint=fp, tool_name=name, success=False)


# --- fingerprint ---


def test_fingerprint_same_name_and_args_match():
    assert fingerprint_tool_call("web_search", '{"q": "x"}') == fingerprint_tool_call(
        "web_search", '{"q": "x"}'
    )


def test_fingerprint_ignores_key_order():
    assert fingerprint_tool_call("t", '{"a": 1, "b": 2}') == fingerprint_tool_call(
        "t", '{"b": 2, "a": 1}'
    )


def test_fingerprint_differs_on_args_and_name():
    assert fingerprint_tool_call("t", '{"q": "x"}') != fingerprint_tool_call("t", '{"q": "y"}')
    assert fingerprint_tool_call("a", "{}") != fingerprint_tool_call("b", "{}")


def test_fingerprint_malformed_json_falls_back_to_raw():
    # Identical malformed strings still collide (verbatim repeat caught);
    # different malformed strings do not.
    assert fingerprint_tool_call("t", "not json") == fingerprint_tool_call("t", "not json")
    assert fingerprint_tool_call("t", "not json") != fingerprint_tool_call("t", "other junk")


def test_fingerprint_empty_args_stable():
    assert fingerprint_tool_call("t", "") == fingerprint_tool_call("t", "")


def test_fingerprint_empty_old_string_collapses_across_paths():
    """不同 path/new_string 的空 old_string → 同一 fingerprint（畸形收敛）。"""
    a = fingerprint_tool_call(
        "str_replace",
        '{"path": "a.md", "old_string": "", "new_string": "AAA"}',
    )
    b = fingerprint_tool_call(
        "str_replace",
        '{"path": "b.md", "old_string": "   ", "new_string": "BBB"}',
    )
    assert a == b
    # Non-empty old_string must not collapse into the same bucket.
    ok = fingerprint_tool_call(
        "str_replace",
        '{"path": "a.md", "old_string": "x", "new_string": "y"}',
    )
    assert ok != a


def test_fingerprint_empty_write_path_collapses():
    assert fingerprint_tool_call(
        "file_write", '{"path": "", "content": "x"}'
    ) == fingerprint_tool_call("file_write", '{"path": "  ", "content": "other"}')
    assert fingerprint_tool_call(
        "file_append", '{"path": "", "content": "x"}'
    ) == fingerprint_tool_call("file_append", '{"path": "  ", "content": "y"}')


# --- detect: nothing below threshold ---


def test_detect_below_threshold_returns_none():
    c = LoopController()
    c.record([_ok("a"), _ok("a")])  # 2 < threshold 3
    assert c.detect() is None


def test_detect_distinct_calls_returns_none():
    c = LoopController()
    c.record([_ok("a"), _ok("b"), _ok("c"), _ok("d")])
    assert c.detect() is None


# --- detect: investigation paging is not stuck; execution repeats are ---


def test_detect_repeated_success_is_not_stuck():
    c = LoopController(investigation_tools=frozenset({"file_read"}))
    c.record([_ok("a", "file_read"), _ok("a", "file_read"), _ok("a", "file_read")])
    assert c.detect() is None


def test_detect_repeated_success_across_rounds_is_not_stuck():
    c = LoopController(investigation_tools=frozenset({"file_read"}))
    c.record([_ok("a", "file_read")])
    c.record([_ok("a", "file_read")])
    assert c.detect() is None
    c.record([_ok("a", "file_read")])
    assert c.detect() is None


def test_detect_three_identical_parallel_success_is_not_stuck():
    c = LoopController(investigation_tools=frozenset({"file_read"}))
    c.record([_ok("a", "file_read"), _ok("a", "file_read"), _ok("a", "file_read")])
    assert c.detect() is None


def test_detect_repeated_execution_success_is_stuck():
    c = LoopController(investigation_tools=frozenset({"file_read"}))
    c.record([_ok("a", "compute"), _ok("a", "compute"), _ok("a", "compute")])
    signal = c.detect()
    assert signal is not None
    assert signal.reason is StuckReason.REPEATED_CALL
    assert signal.tool_name == "compute"


# --- detect: repeated failure takes priority ---


def test_detect_repeated_failure_priority_over_repeated_call():
    c = LoopController()
    c.record([_fail("a"), _fail("a"), _fail("a")])
    signal = c.detect()
    assert signal.reason is StuckReason.REPEATED_FAILURE
    assert signal.count == 3


def test_detect_mixed_success_is_not_stuck():
    c = LoopController()
    c.record([_ok("a"), _fail("a"), _ok("a")])  # only 2 failures < threshold
    assert c.detect() is None


# --- detect: A-B-A-B alternation ---


def test_detect_alternating():
    c = LoopController()
    c.record([_ok("a"), _ok("b"), _ok("a"), _ok("b")])
    signal = c.detect()
    assert signal is not None
    assert signal.reason is StuckReason.ALTERNATING


def test_detect_identical_success_is_not_alternating():
    c = LoopController(investigation_tools=frozenset({"file_read"}))
    # a,a,a,a → not stuck (investigation paging) and not alternation
    c.record(
        [
            _ok("a", "file_read"),
            _ok("a", "file_read"),
            _ok("a", "file_read"),
            _ok("a", "file_read"),
        ]
    )
    assert c.detect() is None


# --- decide: two-strike ladder ---


def test_decide_continue_when_no_signal():
    c = LoopController()
    assert c.decide(None) is Intervention.CONTINUE


def test_decide_first_signal_nudges_then_finalizes():
    c = LoopController()
    c.record([_fail("a"), _fail("a"), _fail("a")])
    first = c.detect()
    assert c.decide(first) is Intervention.NUDGE
    # window was cleared by the nudge: a fresh repeat is needed to escalate
    assert c.detect() is None
    c.record([_fail("a"), _fail("a"), _fail("a")])
    second = c.detect()
    assert c.decide(second) is Intervention.FINALIZE


def test_nudge_clears_window_so_one_stale_repeat_does_not_finalize():
    c = LoopController()
    c.record([_fail("a"), _fail("a"), _fail("a")])
    c.decide(c.detect())  # NUDGE + clear
    # Model recovers: a single different call must NOT immediately finalize.
    c.record([_ok("b")])
    assert c.detect() is None


# --- B2: empty-response degraded ladder ---


def test_empty_response_continues_then_finalizes():
    # The default ladder: 1st empty retries on the same model (CONTINUE), and a
    # 2nd consecutive empty → FINALIZE (the turn ends degraded, not blank).
    c = LoopController(empty_threshold=2)
    c.note_empty_round(True)
    assert c.empty_response_action() is Intervention.CONTINUE
    c.note_empty_round(True)
    assert c.empty_response_action() is Intervention.FINALIZE


def test_length_empty_finalizes_without_continue():
    """finish_reason=length + empty skips the default one-shot Continue."""
    c = LoopController(empty_threshold=2)
    c.note_empty_round(True)  # streak=1 would normally CONTINUE
    assert c.empty_response_action(finish_reason="length") is Intervention.FINALIZE
    # Ordinary silent empty still Continues on the first hit.
    assert c.empty_response_action(finish_reason="stop") is Intervention.CONTINUE


def test_empty_streak_resets_on_nonempty_round():
    # A real answer / tool call between empties breaks the streak, so only
    # *consecutive* empties escalate.
    c = LoopController(empty_threshold=2)
    c.note_empty_round(True)
    c.note_empty_round(False)  # recovered
    c.note_empty_round(True)  # streak restarts at 1
    assert c.empty_response_action() is Intervention.CONTINUE


# --- B2: tool failure circuit breaker ---


def test_circuit_breaker_warns_at_warn_threshold():
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record([_fail("a", "t")])  # 1 failure (distinct args, same tool)
    assert not c.tool_circuit_breaker()  # below warn
    c.record([_fail("b", "t")])  # 2 failures
    cb = c.tool_circuit_breaker()
    assert cb.warned == ("t",)
    assert cb.disabled == ()


def test_circuit_breaker_disables_at_disable_threshold_and_is_idempotent():
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record([_fail("a", "t"), _fail("b", "t")])
    assert c.tool_circuit_breaker().warned == ("t",)
    c.record([_fail("c", "t")])  # 3rd failure
    cb = c.tool_circuit_breaker()
    assert cb.disabled == ("t",)
    assert cb.warned == ()  # not re-warned
    # already disabled → no further transitions fire for this tool
    c.record([_fail("d", "t")])
    assert not c.tool_circuit_breaker()


def test_circuit_breaker_leaps_straight_to_disable_without_redundant_warn():
    # 3 failures arrive before any check → the tool is disabled outright (no warn).
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record([_fail("a", "t"), _fail("b", "t"), _fail("c", "t")])
    cb = c.tool_circuit_breaker()
    assert cb.disabled == ("t",)
    assert cb.warned == ()


def test_circuit_breaker_read_url_warn_and_disable_use_stop_read_steer():
    """read_url steers must not say「换不同的输入」(that fuels URL thrashing).

    Warn/disable must also close the «继续 web_search» default exit.
    """
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record([_fail("a", "read_url"), _fail("b", "read_url")])
    warn = c.tool_circuit_breaker()
    assert warn.warned == ("read_url",)
    msg = warn.message() or ""
    assert "换不同的输入" not in msg
    assert "read_url" in msg
    assert "不要把继续 web_search 当默认出路" in msg

    c.record([_fail("c", "read_url")])
    disable = c.tool_circuit_breaker()
    assert disable.disabled == ("read_url",)
    dmsg = disable.message() or ""
    assert "换不同的输入" not in dmsg
    assert "停用" in dmsg
    assert "收束继续 web_search" in dmsg or "不要把继续检索当默认出路" in dmsg
    assert "基于已有材料" in dmsg


def test_circuit_breaker_counts_failures_per_tool_and_ignores_success():
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record([_ok("a", "t"), _ok("b", "t")])  # successes never count
    c.record([_fail("c", "u")])  # a different tool's single failure
    assert not c.tool_circuit_breaker()


def test_circuit_breaker_ignores_policy_failures():
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    policy = ToolAttempt("a", "read_url", success=False, policy_failure=True)
    c.record([policy, policy, policy])
    assert not c.tool_circuit_breaker()


def test_circuit_breaker_ignores_approval_denial_policy_failures():
    # Approval denials are stamped policy_failure=True in tool_exec — same posture
    # as SSRF/egress blocks: honest for the model, invisible to the breaker.
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    denial = ToolAttempt("a", "file_write", success=False, policy_failure=True)
    c.record([denial, denial, denial])
    assert not c.tool_circuit_breaker()
    assert c.tool_failure_count("file_write") == 0


def test_circuit_breaker_still_counts_real_execution_failures():
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    real = ToolAttempt("a", "file_write", success=False, policy_failure=False)
    c.record([real, real, real])
    cb = c.tool_circuit_breaker()
    # Write tools stay enabled — force segmented instead of circuit-disable.
    assert cb.disabled == ()
    assert "file_write" in cb.force_segmented
    assert c.tool_failure_count("file_write") == 3
    msg = cb.message() or ""
    assert "短骨架" in msg or "分段" in msg
    assert "停用" not in msg


def test_circuit_breaker_parse_only_write_tools_force_segmented_not_disable():
    """Parse-only file_write failures must not retire the pen."""
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    parse = ToolAttempt("a", "file_write", success=False, parse_failure=True)
    c.record([parse])
    assert not c.tool_circuit_breaker()
    c.record([ToolAttempt("b", "file_write", success=False, parse_failure=True)])
    warn = c.tool_circuit_breaker()
    assert warn.warned == ("file_write",)
    assert "file_write" in warn.parse_only
    warn_msg = warn.message() or ""
    assert "分段" in warn_msg or "短骨架" in warn_msg
    assert "原样重发全部参数" not in warn_msg
    c.record([ToolAttempt("c", "file_write", success=False, parse_failure=True)])
    disable = c.tool_circuit_breaker()
    assert disable.disabled == ()
    assert "file_write" in disable.force_segmented
    assert "停用" not in (disable.message() or "")
    assert "原样重发" not in (disable.message() or "")


def test_retire_tools_hard_disables_family_on_first_failure():
    """Permanent capability fail (browser egress): one shot retires the live name."""
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    family = ("browser",)
    steer = "browser 因沙箱出网能力不可用已停用"
    c.record(
        [
            ToolAttempt(
                "a",
                "browser",
                success=False,
                error_summary="egress_unavailable",
                meta={
                    "code": "egress_unavailable",
                    "retire_tools": list(family),
                    "retire_message": steer,
                },
            )
        ]
    )
    cb = c.tool_circuit_breaker()
    assert set(cb.disabled) == set(family)
    assert cb.warned == ()
    assert cb.retire_message == steer
    msg = cb.message()
    assert msg is not None and steer in msg
    assert "已多次失败" not in msg
    # Idempotent: further failures do not re-fire.
    c.record(
        [
            ToolAttempt(
                "b",
                "browser",
                success=False,
                meta={"retire_tools": list(family), "retire_message": steer},
            )
        ]
    )
    assert not c.tool_circuit_breaker()


def test_retire_tools_still_honors_legacy_browser_family_names():
    """Historical journal retire_tools lists still disable those names."""
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    family = (
        "browser_navigate",
        "browser_click",
        "browser_screenshot",
    )
    c.record(
        [
            ToolAttempt(
                "a",
                "browser_navigate",
                success=False,
                meta={
                    "retire_tools": list(family),
                    "retire_message": "legacy family retire",
                },
            )
        ]
    )
    cb = c.tool_circuit_breaker()
    assert set(cb.disabled) == set(family)


def test_workspace_channel_dead_disables_landing_tools():
    """Sticky channel-dead must disable pens (not force_segmented) + retire family steer."""
    from agentcore.workspace.limits import (
        WORKSPACE_CHANNEL_DEAD_RETIRE_STEER,
        WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS,
    )

    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record(
        [
            ToolAttempt(
                "dead",
                "file_list",
                success=False,
                error_summary="活性挂起",
                meta={
                    "liveness_timeout": True,
                    "timeout_layer": "channel",
                    "error_class": "permanent",
                    "workspace_channel_dead": True,
                    "retire_tools": list(WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS),
                    "retire_message": WORKSPACE_CHANNEL_DEAD_RETIRE_STEER,
                },
            )
        ]
    )
    cb = c.tool_circuit_breaker()
    assert "file_write" in cb.disabled
    assert "str_replace" in cb.disabled
    assert "file_list" in cb.disabled
    assert "index_files" in cb.disabled
    assert not cb.force_segmented
    assert WORKSPACE_CHANNEL_DEAD_RETIRE_STEER in (cb.message() or "")
    assert "派需要读写本地文件的队员" in (cb.message() or "")


def test_single_op_channel_timeout_does_not_sticky_or_notice():
    """Single-op settle timeout (channel_op) must not latch sticky-dead or user notice."""
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )
    from agentcore.runtime.events import EventSink, EventType

    clear_active_coordination()
    sink = EventSink()
    session = CoordinationSession(
        execution_id="exec-op-timeout",
        total_workers=1,
        conversation_id="conv-op-timeout",
    )
    session.event_sink = sink
    set_active_coordination(session)
    try:
        c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
        c.record(
            [
                ToolAttempt(
                    "op-to",
                    "file_read",
                    success=False,
                    error_summary="活性挂起",
                    meta={
                        "liveness_timeout": True,
                        "timeout_layer": "channel_op",
                    },
                )
            ]
        )
        assert c._workspace_channel_dead is False  # noqa: SLF001
        assert session.workspace_channel_dead is False
        assert session.channel_dead_user_notice_emitted is False
        deltas = [e for e in sink._history if e.type is EventType.CONTENT_DELTA]
        assert deltas == []
        # Per-tool permanent retire still applies; family pens stay available.
        cb = c.tool_circuit_breaker()
        assert "file_read" in cb.disabled
        assert "file_write" not in cb.disabled
    finally:
        clear_active_coordination()


def test_workspace_channel_dead_emits_user_notice_once():
    """A2: sticky-dead stamps session + emits short content_delta on host sink once."""
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )
    from agentcore.runtime.events import EventSink, EventType
    from agentcore.workspace.limits import (
        CHANNEL_DEAD_USER_VISIBLE,
        WORKSPACE_CHANNEL_DEAD_RETIRE_STEER,
        WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS,
    )

    clear_active_coordination()
    sink = EventSink()
    session = CoordinationSession(
        execution_id="exec-notice",
        total_workers=2,
        conversation_id="conv-notice",
    )
    session.event_sink = sink
    set_active_coordination(session)
    try:
        c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
        meta = {
            "liveness_timeout": True,
            "timeout_layer": "channel",
            "error_class": "permanent",
            "workspace_channel_dead": True,
            "execution_id": "exec-notice",
            "retire_tools": list(WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS),
            "retire_message": WORKSPACE_CHANNEL_DEAD_RETIRE_STEER,
        }
        c.record(
            [
                ToolAttempt(
                    "dead1",
                    "file_read",
                    success=False,
                    error_summary="活性挂起",
                    meta=meta,
                )
            ]
        )
        assert session.workspace_channel_dead is True
        assert session.channel_dead_user_notice_emitted is True
        deltas = [e for e in sink._history if e.type is EventType.CONTENT_DELTA]
        assert len(deltas) == 1
        assert CHANNEL_DEAD_USER_VISIBLE in (deltas[0].payload.get("delta") or "")

        # Second channel-dead attempt must not double-emit.
        c.record(
            [
                ToolAttempt(
                    "dead2",
                    "file_list",
                    success=False,
                    error_summary="活性挂起",
                    meta=meta,
                )
            ]
        )
        deltas2 = [e for e in sink._history if e.type is EventType.CONTENT_DELTA]
        assert len(deltas2) == 1
    finally:
        clear_active_coordination()


def test_index_files_in_channel_dead_retire_family():
    """A3: ambient index_files retires with the local file tool family."""
    from agentcore.workspace.limits import WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS

    assert "index_files" in WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS


def test_retire_tools_honored_even_with_contract_failure():
    """Explicit retire_tools must hard-stop even when contract_failure skips tallies."""
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    steer = "工具 `browser` 因 egress 策略已停用：请改用其它手段"
    c.record(
        [
            ToolAttempt(
                "tip",
                "browser",
                success=False,
                error_summary="egress denied",
                contract_failure=True,
                meta={"retire_tools": ["browser"], "retire_message": steer},
            )
        ]
    )
    # Without retire honor, contract_failure would leave failures at 0.
    assert c.tool_failure_count("browser") >= 3
    cb = c.tool_circuit_breaker()
    assert cb.disabled == ("browser",)
    assert cb.retire_message == steer
    assert "egress" in (cb.message() or "")


def test_permanent_sandbox_network_retires_code_execute_on_first_fail():
    """sandbox network unsupported → permanent; first fail disables code_execute."""
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record(
        [
            ToolAttempt(
                "a",
                "code_execute",
                success=False,
                error_summary="sandbox network isn't supported with --rootless",
                meta={
                    "error_class": "permanent",
                    "code": "sandbox_network_unsupported",
                    "retire_tools": ["code_execute"],
                    "retire_message": "工具 `code_execute` 因沙箱网络能力不可用已停用",
                },
            )
        ]
    )
    cb = c.tool_circuit_breaker()
    assert cb.disabled == ("code_execute",)
    assert cb.warned == ()
    assert "沙箱网络" in (cb.message() or "")
    # Second identical failure must not re-fire or wait for threshold 3.
    c.record(
        [
            ToolAttempt(
                "b",
                "code_execute",
                success=False,
                meta={
                    "error_class": "permanent",
                    "retire_tools": ["code_execute"],
                },
            )
        ]
    )
    assert not c.tool_circuit_breaker()


def test_exec_env_timeout_retires_family_after_two_hits():
    """idle hang + code_execute sandbox timeout → retire both after 2 hits."""
    from agentcore.runtime.loop_controller import EXEC_ENV_TIMEOUT_RETIRE_STEER

    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record(
        [
            ToolAttempt(
                "t1",
                "test_run",
                success=False,
                contract_failure=True,
                error_summary="验证未在 300s 预算内完成（验证未完成，非工具故障）",
                meta={"code": "verify_budget", "exec_env_timeout": True},
            )
        ]
    )
    assert not c.tool_circuit_breaker()
    c.record(
        [
            ToolAttempt(
                "c1",
                "code_execute",
                success=False,
                error_summary="stderr:\nTimeout: execution exceeded 30s",
                meta={"code": "exec_timeout", "exec_env_timeout": True},
            )
        ]
    )
    cb = c.tool_circuit_breaker()
    assert set(cb.disabled) == {"code_execute", "test_run"}
    assert cb.retire_message == EXEC_ENV_TIMEOUT_RETIRE_STEER
    assert "执行环境连续超时" in (cb.message() or "")
    # Success of either family tool would have cleared the streak earlier; after
    # retire, further timeouts must not re-fire.
    c.record(
        [
            ToolAttempt(
                "t2",
                "test_run",
                success=False,
                contract_failure=True,
                meta={"code": "verify_budget", "exec_env_timeout": True},
            )
        ]
    )
    assert not c.tool_circuit_breaker()


def test_exec_env_timeout_emits_user_notice_once():
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )
    from agentcore.runtime.events import EventSink, EventType
    from agentcore.runtime.loop_controller import EXEC_ENV_TIMEOUT_RETIRE_STEER
    from agentcore.workspace.limits import EXEC_ENV_DEAD_USER_VISIBLE

    clear_active_coordination()
    sink = EventSink()
    session = CoordinationSession(
        execution_id="exec-env-notice",
        total_workers=1,
        conversation_id="conv-env",
    )
    session.event_sink = sink
    set_active_coordination(session)
    try:
        c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
        for i, tool in enumerate(("test_run", "code_execute")):
            c.record(
                [
                    ToolAttempt(
                        f"e{i}",
                        tool,
                        success=False,
                        contract_failure=tool == "test_run",
                        error_summary="Timeout: execution exceeded 30s",
                        meta={
                            "code": "exec_timeout",
                            "exec_env_timeout": True,
                            "execution_id": "exec-env-notice",
                        },
                    )
                ]
            )
        assert session.exec_env_dead is True
        assert session.exec_env_dead_user_notice_emitted is True
        deltas = [e for e in sink._history if e.type is EventType.CONTENT_DELTA]
        assert len(deltas) == 1
        assert EXEC_ENV_DEAD_USER_VISIBLE in (deltas[0].payload.get("delta") or "")
        cb = c.tool_circuit_breaker()
        assert set(cb.disabled) == {"code_execute", "test_run"}
        assert EXEC_ENV_TIMEOUT_RETIRE_STEER in (cb.message() or "")
    finally:
        clear_active_coordination()


def test_exec_env_timeout_streak_clears_on_success():
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record(
        [
            ToolAttempt(
                "t1",
                "test_run",
                success=False,
                contract_failure=True,
                meta={"code": "verify_budget", "exec_env_timeout": True},
            )
        ]
    )
    c.record([ToolAttempt("ok", "code_execute", success=True)])
    c.record(
        [
            ToolAttempt(
                "t2",
                "test_run",
                success=False,
                contract_failure=True,
                meta={"code": "verify_budget", "exec_env_timeout": True},
            )
        ]
    )
    assert not c.tool_circuit_breaker()


def test_permission_access_retires_tool_allowlist_does_not():
    """grep access permission retires; allowlist deny stays policy-only (no disable)."""
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record(
        [
            ToolAttempt(
                "g1",
                "grep",
                success=False,
                policy_failure=True,
                error_summary="没有访问权限",
                meta={
                    "error_class": "permission",
                    "permission_kind": "access",
                    "retire_tools": ["grep"],
                    "retire_message": "工具 `grep` 因无访问权限已停用",
                },
            )
        ]
    )
    cb = c.tool_circuit_breaker()
    assert cb.disabled == ("grep",)
    assert c.tool_failure_count("grep") >= 3

    c2 = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    deny = ToolAttempt(
        "a",
        "file_write",
        success=False,
        policy_failure=True,
        meta={"error_class": "permission", "permission_kind": "allowlist"},
    )
    c2.record([deny, deny, deny])
    assert not c2.tool_circuit_breaker()
    assert c2.tool_failure_count("file_write") == 0


def test_permission_does_not_affect_transient_thresholds():
    """Permission denials must not burn warn=2 / disable=3 for other tools."""
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    perm = ToolAttempt(
        "p",
        "grep",
        success=False,
        policy_failure=True,
        meta={"error_class": "permission", "permission_kind": "allowlist"},
    )
    c.record([perm, perm, perm])
    assert not c.tool_circuit_breaker()
    c.record([_fail("a", "read_url")])
    assert not c.tool_circuit_breaker()
    c.record([_fail("b", "read_url")])
    warn = c.tool_circuit_breaker()
    assert warn.warned == ("read_url",)
    assert warn.disabled == ()


def test_validation_same_fingerprint_stops_path_at_two():
    """Validation ×2 same fingerprint → path-stop steer; tool stays available.

    ×3 (re-hit after steer) → thrash latch + hard-stop pending (no second steer).
    """
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    rej = ToolAttempt(
        "same-fp",
        "delegate",
        success=False,
        contract_failure=True,
        meta={"error_class": "validation"},
    )
    c.record([rej])
    assert not c.tool_circuit_breaker()
    assert not c.is_thrashing()
    assert not c.take_validation_hard_stop()
    c.record([rej])
    cb = c.tool_circuit_breaker()
    assert cb.disabled == ()
    assert cb.warned == ()
    assert c.tool_failure_count("delegate") == 0
    assert cb.validation_stop is not None
    assert "delegate" in (cb.validation_stop or "")
    msg = cb.message() or ""
    assert "同因" in msg or "路径" in msg
    assert not c.is_thrashing()
    # Re-hit after path-stop: hard stop / thrashing (do not burn max_rounds).
    c.record([rej])
    assert not c.tool_circuit_breaker()
    assert c.is_thrashing()
    assert c.validation_thrash_latched
    assert c.take_validation_hard_stop()
    assert not c.take_validation_hard_stop()  # one-shot
    # Different fp can still self-correct with a fresh path-stop steer.
    other = ToolAttempt(
        "other-fp",
        "delegate",
        success=False,
        contract_failure=True,
        meta={"error_class": "validation"},
    )
    c.record([other])
    assert not c.tool_circuit_breaker()
    c.record([other])
    cb2 = c.tool_circuit_breaker()
    assert cb2.validation_stop is not None
    assert cb2.disabled == ()


def test_validation_stopped_fps_seed_round_trip_hard_stops_on_rehit():
    """export_seed keeps stopped fps; new controller + seed thrashs on first re-hit."""
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    rej = ToolAttempt(
        "same-fp",
        "str_replace",
        success=False,
        contract_failure=True,
        meta={"error_class": "validation"},
    )
    c.record([rej])
    c.record([rej])
    assert c.tool_circuit_breaker().validation_stop is not None
    seed = c.export_seed()
    assert "same-fp" in seed["validation_stopped_fps"]
    assert seed["validation_thrash_latched"] is False

    restored = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    restored.apply_seed(seed)
    assert not restored.is_thrashing()
    # First re-hit of a seeded stopped fp → hard stop (no second steer-only pass).
    restored.record([rej])
    assert not restored.tool_circuit_breaker()
    assert restored.is_thrashing()
    assert restored.take_validation_hard_stop()


def test_validation_empty_old_string_collapse_same_fp_thrash():
    """空 old_string 塌缩为同一指纹：第2次 steer，第3次 thrash（回归）。"""
    from agentcore.runtime.loop_controller import fingerprint_tool_call

    fp = fingerprint_tool_call(
        "str_replace",
        '{"path": "a.md", "old_string": "", "new_string": "AAA"}',
    )
    fp2 = fingerprint_tool_call(
        "str_replace",
        '{"path": "b.md", "old_string": "   ", "new_string": "BBB"}',
    )
    assert fp == fp2
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    rej = ToolAttempt(
        fp,
        "str_replace",
        success=False,
        contract_failure=True,
        meta={"error_class": "validation"},
    )
    c.record([rej])
    assert not c.tool_circuit_breaker()
    c.record([rej])
    assert c.tool_circuit_breaker().validation_stop is not None
    c.record([rej])
    assert c.is_thrashing()
    assert c.take_validation_hard_stop()
    # Landing tools stay available (no disable / force_segmented from this path).
    assert c.tool_failure_count("str_replace") == 0


def test_govern_validation_rehit_finalizes_without_burning_rounds():
    """Governance consumes validation hard-stop → Finalize(UNPRODUCTIVE)."""
    from agentcore.runtime.engine.directive import Finalize
    from agentcore.runtime.engine.governance import govern_after_tools
    from agentcore.runtime.engine.outcome import RoundOutcome
    from agentcore.runtime.events import FinishReason

    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    rej = ToolAttempt(
        "same-fp",
        "str_replace",
        success=False,
        contract_failure=True,
        meta={"error_class": "validation"},
    )
    c.record([rej])
    c.record([rej])
    assert c.tool_circuit_breaker().validation_stop is not None
    c.record([rej])
    assert c.is_thrashing()
    messages: list = []
    directive = govern_after_tools(
        RoundOutcome(
            content="",
            reasoning="",
            usage=None,
            tool_calls=[],
            tool_results=[],
            attempts=[rej],
        ),
        c,
        messages=messages,
        round_idx=2,
        run_id="r-val",
        breaker_message=None,
    )
    assert isinstance(directive, Finalize)
    assert directive.reason == "validation_thrash"
    assert directive.finish_reason is FinishReason.UNPRODUCTIVE


@pytest.mark.asyncio
async def test_validation_thrash_finalize_escalates_gap_upward(monkeypatch):
    """08-08 定案①：validation thrash 早停时向上交缺口（escalation），不重做 e94 PARTIAL。"""
    from unittest.mock import MagicMock

    from agentcore.llm.provider.protocol import TokenUsage
    from agentcore.runtime.engine.directive import Finalize
    from agentcore.runtime.engine.directive_apply import apply_loop_directive
    from agentcore.runtime.engine.outcome import RoundOutcome
    from agentcore.runtime.events import EventSink, EventType, FinishReason

    async def _fake_finalize(**_kwargs):
        return "", "", TokenUsage(), 3, None

    monkeypatch.setattr(
        "agentcore.runtime.engine.directive_apply.force_finalize",
        _fake_finalize,
    )
    sink = EventSink()
    gate: list[dict] = []
    finish: list[FinishReason] = []
    controller = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    result = await apply_loop_directive(
        directive=Finalize(
            reason="validation_thrash", finish_reason=FinishReason.UNPRODUCTIVE
        ),
        outcome=RoundOutcome(content="", reasoning="", usage=None),
        messages=[],
        llm=MagicMock(),
        tools=MagicMock(),
        tool_context=MagicMock(agent_id="worker-a"),
        sink=sink,
        profile=MagicMock(),
        active_model="m",
        base_model="m",
        allowed_tool_names=None,
        disabled_tools=set(),
        emit_content=lambda _d: None,
        emit_reasoning=lambda _d: None,
        emit_reset=lambda _r: None,
        final_content="",
        final_reasoning="",
        total_usage=TokenUsage(),
        round_idx=2,
        run_id="del_ws",
        role="worker",
        finish_override_sink=finish,
        approval_gate=None,
        citation_sink=None,
        annotate_citations=False,
        turn_evidence_ledger=None,
        ledger_registrant="",
        gate_escalation_sink=gate,
        controller=controller,
        content_before_round="",
        finish_guard_reworks=0,
    )
    assert result.action == "return"
    assert finish == [FinishReason.UNPRODUCTIVE]
    assert len(gate) == 1
    assert gate[0]["source"] == "validation_thrash"
    assert "早停" in gate[0]["question"] or "缺口" in gate[0]["question"]
    assert "validation_thrash" in gate[0]["evidence"]
    raised = [e for e in sink._history if e.type is EventType.RUN_ESCALATION]
    assert len(raised) == 1
    assert raised[0].payload.get("source") == "validation_thrash"


def test_transient_still_warns_at_two_disables_at_three():
    """Unclassified / transient failures keep warn=2 / disable=3."""
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record([_fail("a", "web_search")])
    assert not c.tool_circuit_breaker()
    c.record([_fail("b", "web_search")])
    assert c.tool_circuit_breaker().warned == ("web_search",)
    c.record([_fail("c", "web_search")])
    assert c.tool_circuit_breaker().disabled == ("web_search",)


def test_circuit_breaker_ignores_contract_failures_in_one_round():
    # 参数契约拒绝 (web_search A3 query 过长/过多) 是零成本可修正的参数打回：一个研究员
    # 同轮扇出 5 条超长查询也不该烧穿断路器，否则模型还没看到「改 2–4 个核心词重试」的
    # 提示就永久失去 web_search。5 > disable(3)，若计数早已 disable。
    # Same fingerprint ×2+ may fire validation path-stop (tool stays available).
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    rej = ToolAttempt("a", "web_search", success=False, contract_failure=True)
    c.record([rej, rej, rej, rej, rej])
    cb = c.tool_circuit_breaker()
    assert cb.disabled == ()
    assert cb.warned == ()
    assert c.tool_failure_count("web_search") == 0
    assert cb.validation_stop is not None
    assert "web_search" in (cb.validation_stop or "")


def test_circuit_breaker_contract_failures_across_rounds_still_ignored():
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    for fp in ("a", "b", "c", "d"):
        c.record([ToolAttempt(fp, "web_search", success=False, contract_failure=True)])
        cb = c.tool_circuit_breaker()
        assert cb.disabled == ()
        assert cb.warned == ()
    assert c.tool_failure_count("web_search") == 0


def test_circuit_breaker_ignores_path_not_found_env_failures():
    """Environment / wrong-path missing files must not disable file_read.

    Accident shape: platform left an attachment out of a delegated workspace →
    repeated file_read PathNotFound must not warn/disable the tool. Distinct
    missing paths stay free of the fuse; same-fingerprint thrash still hits
    validation path-stop (tool remains available).
    """
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    for i in range(6):
        c.record(
            [
                ToolAttempt(
                    f"missing-{i}",
                    "file_read",
                    success=False,
                    contract_failure=True,
                    error_summary=f"文件不存在：ghost/{i}.md",
                )
            ]
        )
    cb = c.tool_circuit_breaker()
    assert cb.disabled == ()
    assert cb.warned == ()
    assert c.tool_failure_count("file_read") == 0
    assert cb.validation_stop is None

    # Same call fingerprint ×2 → validation path-stop; still no disable.
    same = ToolAttempt(
        "same-missing",
        "file_read",
        success=False,
        contract_failure=True,
        error_summary="文件不存在：ghost/same.md",
    )
    c.record([same])
    assert not c.tool_circuit_breaker()
    c.record([same])
    stop = c.tool_circuit_breaker()
    assert stop.disabled == ()
    assert stop.warned == ()
    assert stop.validation_stop is not None
    assert "file_read" in (stop.validation_stop or "")
    assert c.tool_failure_count("file_read") == 0


def test_circuit_breaker_counts_only_real_failures_when_mixed_with_contract():
    # 契约拒绝与真实失败（网络错误等）混合时：只有真实失败计入 warn/disable。
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    rej = ToolAttempt("a", "web_search", success=False, contract_failure=True)
    real = ToolAttempt("b", "web_search", success=False)
    # 3 契约拒绝 + 2 真实失败 → 只数到 2 → warn，不 disable。
    c.record([rej, real, rej, real, rej])
    cb = c.tool_circuit_breaker()
    assert cb.warned == ("web_search",)
    assert cb.disabled == ()
    assert c.tool_failure_count("web_search") == 2
    # 第 3 个真实失败才 disable；夹带的契约拒绝仍不计数。
    c.record([rej, real, rej])
    cb2 = c.tool_circuit_breaker()
    assert cb2.disabled == ("web_search",)
    assert c.tool_failure_count("web_search") == 3


def test_contract_failure_still_counts_as_round_failure_for_detection():
    # 「该轮失败」语义不变：契约拒绝仍进滑窗，同指纹重复≥阈值照常触发 REPEATED_FAILURE。
    c = LoopController(window=8, threshold=3)
    rej = ToolAttempt("same", "web_search", success=False, contract_failure=True)
    c.record([rej, rej, rej])
    signal = c.detect()
    assert signal is not None
    assert signal.reason is StuckReason.REPEATED_FAILURE
    assert signal.tool_name == "web_search"


def test_circuit_breaker_tally_survives_nudge_window_clear():
    # The cumulative per-tool tally is run-scoped: the nudge's sliding-window reset
    # must NOT reset it (otherwise a tool failing across a nudge would never trip).
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record([_fail("a", "t"), _fail("a", "t"), _fail("a", "t")])  # 3 failures
    assert c.decide(c.detect()) is Intervention.NUDGE  # clears the window
    cb = c.tool_circuit_breaker()
    assert cb.disabled == ("t",)


def test_circuit_breaker_parse_failures_get_typed_warn_message():
    """Parse failures still trip warn@2; orchestration stays enabled at disable threshold."""
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    parse = ToolAttempt("a", "delegate", success=False, parse_failure=True)
    c.record([parse])
    assert not c.tool_circuit_breaker()
    c.record([ToolAttempt("b", "delegate", success=False, parse_failure=True)])
    cb = c.tool_circuit_breaker()
    assert cb.warned == ("delegate",)
    assert "delegate" in cb.parse_only
    msg = cb.message() or ""
    assert "不是合法 JSON" in msg
    assert "XML" in msg or "parameter" in msg or "合法 JSON" in msg
    assert "换不同的输入" not in msg
    # Parse-only: keep dispatcher (do not circuit-disable delegate).
    c.record([ToolAttempt("c", "delegate", success=False, parse_failure=True)])
    cb2 = c.tool_circuit_breaker()
    assert cb2.disabled == ()
    assert "停用" not in (cb2.message() or "")
    assert c.tool_failure_count("delegate") == 3


def test_circuit_breaker_orchestration_still_disables_on_real_failures():
    """Mixed / non-parse failures still retire delegate at disable threshold."""
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    real = ToolAttempt("a", "delegate", success=False, parse_failure=False)
    c.record([real, real, real])
    cb = c.tool_circuit_breaker()
    assert cb.disabled == ("delegate",)


def test_circuit_breaker_mixed_failures_keep_generic_warn():
    """If any non-parse failure contributed, keep the generic「换不同的输入」steer."""
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record([ToolAttempt("a", "delegate", success=False, parse_failure=True)])
    c.record([ToolAttempt("b", "delegate", success=False, parse_failure=False)])
    cb = c.tool_circuit_breaker()
    assert cb.warned == ("delegate",)
    assert "delegate" not in cb.parse_only
    assert "换不同的输入" in (cb.message() or "")


def test_circuit_breaker_remember_parse_only_keeps_and_memory_steer():
    """remember parse-only thrashing keeps the tool + memory-facing format steer."""
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    parse = ToolAttempt("a", "remember", success=False, parse_failure=True)
    c.record([parse])
    assert not c.tool_circuit_breaker()
    c.record([ToolAttempt("b", "remember", success=False, parse_failure=True)])
    cb = c.tool_circuit_breaker()
    assert cb.warned == ("remember",)
    assert "remember" in cb.parse_only
    msg = cb.message() or ""
    assert "不是合法 JSON" in msg
    assert "记规则" in msg
    assert "禁止截断时原样重发全部" in msg
    assert "勿改用空回复交差" not in msg
    assert "后原样重发全部参数" not in msg
    # Parse-only: keep remember (do not circuit-disable).
    c.record([ToolAttempt("c", "remember", success=False, parse_failure=True)])
    cb2 = c.tool_circuit_breaker()
    assert cb2.disabled == ()
    assert "停用" not in (cb2.message() or "")
    assert c.tool_failure_count("remember") == 3


def test_circuit_breaker_remember_still_disables_on_real_failures():
    """Non-parse remember failures still retire at disable threshold."""
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    real = ToolAttempt("a", "remember", success=False, parse_failure=False)
    c.record([real, real, real])
    cb = c.tool_circuit_breaker()
    assert cb.disabled == ("remember",)


def test_circuit_breaker_other_parse_warn_is_class_aware():
    """Default-tool parse warn must cover truncate vs escape — not only「原样重发全部」."""
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    parse = ToolAttempt("a", "web_search", success=False, parse_failure=True)
    c.record([parse, ToolAttempt("b", "web_search", success=False, parse_failure=True)])
    cb = c.tool_circuit_breaker()
    assert cb.warned == ("web_search",)
    assert "web_search" in cb.parse_only
    msg = cb.message() or ""
    assert "截断" in msg
    assert "转义" in msg
    # Must not teach truncated retries as verbatim resend-only.
    assert "后原样重发全部参数" not in msg
    assert "截断场景禁止原样重发全部" in msg


def _prose_append_reject(fp: str, path: str) -> ToolAttempt:
    return ToolAttempt(
        fp,
        "file_append",
        success=False,
        contract_failure=True,
        error_summary=f"拒绝追加：`{path}` 本 run 已落成篇正文（非骨架）。",
        meta={"path": path, "segmented_write_reject": "prose_append"},
    )


def _code_integrity_reject(fp: str, path: str) -> ToolAttempt:
    return ToolAttempt(
        fp,
        "file_write",
        success=False,
        contract_failure=True,
        error_summary=(
            f"拒绝写入代码文件 `{path}`：括号/方括号/圆括号结构不完整（缺 `}}`）。"
        ),
        meta={"path": path, "segmented_write_reject": "code_integrity"},
    )


def test_path_write_reject_streak_trips_force_segmented_at_two():
    """Same path + same class ×2 → force_segmented (early strategy, not disable)."""
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record([_prose_append_reject("a", "report.md")])
    assert not c.tool_circuit_breaker()
    assert c.tool_failure_count("file_append") == 0  # still contract_failure-skipped
    c.record([_prose_append_reject("b", "report.md")])
    cb = c.tool_circuit_breaker()
    assert cb.disabled == ()
    assert "file_append" in cb.force_segmented
    assert "file_write" in cb.force_segmented
    msg = cb.message() or ""
    assert "短骨架" in msg or "分段" in msg
    assert "停用" not in msg
    # Idempotent: further same-path rejects do not re-fire.
    c.record([_prose_append_reject("c", "report.md")])
    assert not c.tool_circuit_breaker()


def test_path_write_reject_below_threshold_does_not_trip():
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record([_code_integrity_reject("a", "app.ts")])
    assert not c.tool_circuit_breaker()


def test_path_write_reject_different_paths_do_not_combine():
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record([_prose_append_reject("a", "a.md")])
    c.record([_prose_append_reject("b", "b.md")])
    assert not c.tool_circuit_breaker()


def test_path_write_reject_different_class_resets_streak():
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record([_prose_append_reject("a", "x.md")])
    # Same path but different class → streak restarts at 1.
    c.record(
        [
            ToolAttempt(
                "b",
                "file_write",
                success=False,
                contract_failure=True,
                error_summary="拒绝写入代码文件 `x.md`：正文含省略标记。",
                meta={"path": "x.md", "segmented_write_reject": "code_integrity"},
            )
        ]
    )
    assert not c.tool_circuit_breaker()
    c.record(
        [
            ToolAttempt(
                "c",
                "file_write",
                success=False,
                contract_failure=True,
                error_summary="拒绝写入代码文件 `x.md`：正文含省略标记。",
                meta={"path": "x.md", "segmented_write_reject": "code_integrity"},
            )
        ]
    )
    cb = c.tool_circuit_breaker()
    assert "file_write" in cb.force_segmented


def test_path_write_reject_success_resets_streak():
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record([_prose_append_reject("a", "report.md")])
    c.record(
        [
            ToolAttempt(
                "ok",
                "file_append",
                success=True,
                meta={"path": "report.md"},
            )
        ]
    )
    c.record([_prose_append_reject("b", "report.md")])
    assert not c.tool_circuit_breaker()


def test_path_write_reject_two_in_one_round_trips():
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record(
        [
            _code_integrity_reject("a", "main.py"),
            _code_integrity_reject("b", "main.py"),
        ]
    )
    cb = c.tool_circuit_breaker()
    assert "file_write" in cb.force_segmented
    assert "file_append" in cb.force_segmented


def test_classify_segmented_write_reject_covers_prose_and_integrity_not_length():
    from agentcore.runtime.loop_controller import classify_segmented_write_reject

    assert (
        classify_segmented_write_reject(
            "file_append",
            error="拒绝追加：`a.md` 本 run 已落成篇正文（非骨架）。",
            contract_failure=True,
        )
        == "prose_append"
    )
    assert (
        classify_segmented_write_reject(
            "file_write",
            error="拒绝写入代码文件 `a.ts`：括号结构不完整（缺 `}`）。",
            contract_failure=True,
        )
        == "code_integrity"
    )
    assert (
        classify_segmented_write_reject(
            "file_write",
            error="内容过长 length_rejected 请缩短",
            contract_failure=True,
        )
        is None
    )
    assert (
        classify_segmented_write_reject(
            "file_write",
            error="拒绝整篇截断覆盖：`报告.md` 旧稿约 2000 字 → 新稿 300 字（低于旧稿 50%）。",
            contract_failure=True,
        )
        == "severe_shrink"
    )
    assert (
        classify_segmented_write_reject(
            "file_append",
            error="拒绝追加：`a.md` 本 run 已落成篇正文（非骨架）。",
            contract_failure=False,
        )
        is None
    )


def test_path_severe_shrink_reject_streak_trips_force_segmented():
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    rej = ToolAttempt(
        "a",
        "file_write",
        success=False,
        contract_failure=True,
        error_summary="拒绝整篇截断覆盖：`报告.md` 旧稿约 2000 字 → 新稿 300 字。",
        meta={"path": "报告.md", "segmented_write_reject": "severe_shrink"},
    )
    c.record([rej])
    assert not c.tool_circuit_breaker()
    c.record([rej])
    cb = c.tool_circuit_breaker()
    assert "file_write" in cb.force_segmented
    assert "file_append" in cb.force_segmented


def test_apply_circuit_breaker_narrows_file_append_on_force_segmented():
    """force_segmented keeps file_write; narrows file_append out of the toolset."""
    from agentcore.llm.provider.protocol import LLMMessage
    from agentcore.runtime.engine.governance import apply_circuit_breaker

    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record(
        [
            _prose_append_reject("a", "report.md"),
            _prose_append_reject("b", "report.md"),
        ]
    )
    disabled: set[str] = set()
    messages: list[LLMMessage] = []
    out = apply_circuit_breaker(
        c, messages=messages, run_id="r1", round_idx=0, disabled_tools=disabled
    )
    assert out.message is not None
    assert "file_append" in disabled
    assert "file_write" not in disabled
    assert "str_replace" not in disabled
    assert out.refresh_tool_defs is True


# --- B2: no-output early stop (unproductive rounds) ---


def _unproductive(c: LoopController) -> None:
    c.note_round_productivity(had_tool_calls=True, all_failed=True, had_content=False)


def test_unproductive_streak_trips_after_threshold():
    c = LoopController(unproductive_threshold=3)
    _unproductive(c)
    assert not c.unproductive_early_stop()
    _unproductive(c)
    assert not c.unproductive_early_stop()
    _unproductive(c)
    assert c.unproductive_early_stop()


def test_unproductive_streak_resets_on_content():
    c = LoopController(unproductive_threshold=2)
    _unproductive(c)
    # content this round (even with a failing tool) is progress → resets
    c.note_round_productivity(had_tool_calls=True, all_failed=True, had_content=True)
    _unproductive(c)
    assert not c.unproductive_early_stop()  # streak restarted at 1


def test_unproductive_streak_resets_on_tool_success():
    c = LoopController(unproductive_threshold=2)
    _unproductive(c)
    # a tool succeeded → not all-failed → resets
    c.note_round_productivity(had_tool_calls=True, all_failed=False, had_content=False)
    _unproductive(c)
    assert not c.unproductive_early_stop()


def test_no_tool_round_is_not_unproductive():
    # A no-tool round is the empty/degraded path, not the unproductive one.
    c = LoopController(unproductive_threshold=1)
    c.note_round_productivity(had_tool_calls=False, all_failed=False, had_content=False)
    assert not c.unproductive_early_stop()


def test_parse_failure_only_rounds_do_not_count_unproductive():
    """纯协议失败轮不计入 unproductive streak。"""
    c = LoopController(unproductive_threshold=2)
    c.note_round_productivity(
        had_tool_calls=True,
        all_failed=True,
        had_content=False,
        all_parse_failures=True,
    )
    c.note_round_productivity(
        had_tool_calls=True,
        all_failed=True,
        had_content=False,
        all_parse_failures=True,
    )
    assert not c.unproductive_early_stop()
    # 执行失败仍计
    _unproductive(c)
    assert not c.unproductive_early_stop()
    _unproductive(c)
    assert c.unproductive_early_stop()


# --- B2 periodic reflection inject: retired (soft cadence; little effect + browser false nags) ---


def test_progress_tools_reset_investigation_spin():
    """PROGRESS_TOOLS success still clears same-target investigation spin."""
    from agentcore.runtime.loop_controller import PROGRESS_TOOLS

    assert {
        "file_write",
        "file_append",
        "str_replace",
        "write_section",
        "handoff",
        "delegate",
        "ask_user",
    } <= PROGRESS_TOOLS

    c = LoopController(
        investigation_tools=frozenset({"web_search"}),
        convergence_spin_rounds=2,
    )
    c.record([_ok("r1", "web_search")])
    c.record([_ok("r1", "web_search")])
    assert c.same_target_investigation_streak >= 1
    c.record([_ok("w", "file_write")])
    assert c.same_target_investigation_streak == 0


# --- absolute-round finalize (explicit LoopController API) ---
#
# Product factory always passes convergence_finalize_rounds=0. These tests pin
# the constructor knob: when callers pass N>0, different-target investigation
# rounds still FINALIZE at N. Same-target spin is separate and stays on.
# Soft nudge is gone.


def _worker(finalize_rounds: int = 6) -> LoopController:
    # Explicit constructor: investigation tools advance the round clock. Flavor /
    # delegation do not change the knob.
    return LoopController(
        convergence_finalize_rounds=finalize_rounds,
        investigation_tools=frozenset({"web_search", "read_url", "file_read"}),
    )


def test_safety_net_disabled_by_default():
    # finalize_rounds 0 (constructor default / product factory) never force-finalizes
    # on investigation-round count — rounds are still tracked.
    c = LoopController(investigation_tools=frozenset({"web_search"}))
    for i in range(20):
        c.record([_ok(f"s{i}", "web_search")])
    assert c.investigation_rounds == 20
    assert c.convergence_action() is Intervention.CONTINUE


def test_safety_net_counts_rounds_not_calls_so_a_batch_is_one():
    # THE batch-robustness invariant: a parallel fan-out of N reads in ONE round bumps
    # the clock by one, so a worker can't be guillotined right after fanning out wide.
    c = _worker(finalize_rounds=6)
    c.record(
        [
            _ok("a", "web_search"),
            _ok("b", "web_search"),
            _ok("c", "web_search"),
            _ok("d", "web_search"),
        ]
    )  # batch of 4 → still 1 investigation round
    assert c.investigation_calls == 4
    assert c.investigation_rounds == 1
    assert c.convergence_action() is Intervention.CONTINUE  # 1 ≪ 6


def test_all_fail_investigation_round_does_not_spend_budget():
    """一轮内调查工具全失败：calls 照记，rounds 不扣；同目标 spin 仍推进。"""
    c = _worker(finalize_rounds=6)
    c.record([_fail("a", "web_search"), _fail("b", "file_read")])
    assert c.investigation_calls == 2
    assert c.investigation_rounds == 0
    # Mix: one success in the batch → round counts once
    c.record([_fail("c", "web_search"), _ok("d", "file_read")])
    assert c.investigation_calls == 4
    assert c.investigation_rounds == 1
    # Same-target spin still advances on all-fail rounds (fingerprint bookkeeping)
    spin = LoopController(
        convergence_finalize_rounds=30,
        convergence_spin_rounds=3,
        investigation_tools=frozenset({"file_read"}),
    )
    fp = "same"
    for _ in range(3):
        spin.record(
            [ToolAttempt(fingerprint=fp, tool_name="file_read", success=False)]
        )
        assert spin.convergence_action() is Intervention.CONTINUE
    spin.record([ToolAttempt(fingerprint=fp, tool_name="file_read", success=False)])
    assert spin.investigation_rounds == 0  # never succeeded
    assert spin.convergence_action() is Intervention.FINALIZE  # spin still trips


def test_safety_net_continues_below_the_bar_no_soft_nudge():
    # Explicit API: every round under the constructor's finalize_rounds is CONTINUE
    # (the old soft NUDGE is gone).
    c = _worker(finalize_rounds=6)
    for i in range(5):  # rounds 1..5, all below 6
        c.record([_ok(f"s{i}", "web_search")])
        assert c.convergence_action() is Intervention.CONTINUE
    assert c.investigation_rounds == 5


def test_safety_net_finalizes_a_true_runaway_at_the_bar():
    c = _worker(finalize_rounds=6)
    for i in range(5):
        c.record([_ok(f"s{i}", "web_search")])
    assert c.convergence_action() is Intervention.CONTINUE  # round 5 < 6
    c.record([_ok("s5", "web_search")])  # round 6 ≥ 6
    assert c.investigation_rounds == 6
    assert c.convergence_action() is Intervention.FINALIZE


def test_safety_net_is_flavor_agnostic():
    # Explicit API: an orchestration-capable tool set still FINALIZE at the
    # constructor's finalize_rounds (no leaf-only exception).
    c = LoopController(
        convergence_finalize_rounds=6,
        investigation_tools=frozenset({"file_read", "grep"}),
    )
    for i in range(6):
        c.record([_ok(f"r{i}", "file_read")])
    assert c.investigation_rounds == 6
    assert c.convergence_action() is Intervention.FINALIZE


def test_safety_net_ignores_non_investigation_tools():
    # Only read-only investigation tools advance the clock — a worker writing files /
    # asking the user / consulting a skill is making progress, not over-investigating.
    c = _worker(finalize_rounds=6)
    c.record([_ok("a", "file_write"), _ok("b", "ask_user")])
    c.record([_ok("c", "consult")])
    assert c.investigation_rounds == 0
    assert c.convergence_action() is Intervention.CONTINUE


def test_safety_net_round_clock_survives_nudge_window_clear():
    # The investigation-round clock is run-scoped (like the failure tally): a stuck-loop
    # NUDGE clears the sliding window but must NOT reset the safety-net clock.
    c = _worker(finalize_rounds=6)
    c.record([_ok("start", "web_search")])  # investigation round 1
    c.record([_fail("a", "web_search"), _fail("a", "web_search"), _fail("a", "web_search")])
    assert c.decide(c.detect()) is Intervention.NUDGE  # clears the window
    assert c.investigation_rounds == 1  # survived the clear (fail round doesn't add)
    c.record([_ok("b", "read_url")])  # 2nd successful investigation round
    assert c.investigation_rounds == 2
    assert c.convergence_action() is Intervention.CONTINUE  # still ≪ 6


def test_spinning_same_target_triggers_finalize_before_absolute_cap():
    c = LoopController(
        convergence_finalize_rounds=30,
        convergence_spin_rounds=3,
        investigation_tools=frozenset({"file_read"}),
    )
    fp = "same"
    for _ in range(3):
        c.record([ToolAttempt(fingerprint=fp, tool_name="file_read", success=True)])
        assert c.convergence_action() is Intervention.CONTINUE
    c.record([ToolAttempt(fingerprint=fp, tool_name="file_read", success=True)])
    assert c.convergence_action() is Intervention.FINALIZE


def test_different_investigation_targets_do_not_spin():
    c = LoopController(
        convergence_finalize_rounds=30,
        convergence_spin_rounds=3,
        investigation_tools=frozenset({"file_read"}),
    )
    for i in range(10):
        c.record([ToolAttempt(fingerprint=f"f{i}", tool_name="file_read", success=True)])
        assert c.convergence_action() is Intervention.CONTINUE


def test_progress_tool_resets_spin_streak():
    c = LoopController(
        convergence_finalize_rounds=30,
        convergence_spin_rounds=2,
        investigation_tools=frozenset({"file_read"}),
    )
    c.record([ToolAttempt(fingerprint="same", tool_name="file_read", success=True)])
    c.record([ToolAttempt(fingerprint="same", tool_name="file_read", success=True)])
    assert c.same_target_investigation_streak == 1
    c.record([ToolAttempt(fingerprint="d1", tool_name="delegate", success=True)])
    assert c.same_target_investigation_streak == 0
    assert c.convergence_action() is Intervention.CONTINUE


# --- delivery_idle: 交文件空转已退役；recon 调查空转仍在（不中途 FINALIZE）---


def test_factory_ignores_convergence_finalize_rounds_setting(monkeypatch):
    """Factory 永远传 finalize_rounds=0；settings >0 不能把调查轮顶救活。"""
    from agentcore.config import settings
    from agentcore.runtime.engine.governance import create_loop_controller

    monkeypatch.setattr(settings, "engine_convergence_finalize_rounds", 6)
    c = create_loop_controller(frozenset({"file_read", "web_search"}))
    for i in range(12):
        c.record([ToolAttempt(fingerprint=f"f{i}", tool_name="file_read", success=True)])
    assert c.investigation_rounds == 12
    assert c.convergence_action() is Intervention.CONTINUE


def test_delivery_idle_does_not_finalize_mid_loop():
    """Idle 读不 FINALIZE；交文件与调查 factory tracking 均关（rounds=0）。"""
    from agentcore.runtime.engine.governance import create_loop_controller

    c = create_loop_controller(
        frozenset({"file_read", "file_list", "grep"}),
        files_expected=True,
        short_write_posture=False,
    )
    assert c.delivery_idle_nudge_rounds == 0
    assert c.delivery_idle_narrow_rounds == 0
    assert c.delivery_idle_report is False
    for i in range(12):
        c.record([ToolAttempt(fingerprint=f"f{i}", tool_name="file_read", success=True)])
    assert c.convergence_action() is Intervention.CONTINUE
    assert not c.is_thrashing()
    assert c.delivery_idle_rounds == 0
    assert not c.delivery_idle_nudge_due()
    assert not c.delivery_idle_narrow_due()

    recon = create_loop_controller(
        frozenset({"file_read"}),
        files_expected=False,
        short_write_posture=True,
        max_rounds=4,
    )
    assert recon.delivery_idle_nudge_rounds == 0
    assert recon.delivery_idle_narrow_rounds == 0
    assert recon.delivery_idle_recon is False
    for i in range(6):
        recon.record([ToolAttempt(fingerprint=f"p{i}", tool_name="file_read", success=True)])
    assert recon.convergence_action() is Intervention.CONTINUE


def test_factory_does_not_inject_files_or_report_delivery_idle():
    """Factory 对 files/report 不注入 nudge/narrow；退役梯子不再是产品路径。"""
    from agentcore.runtime.engine.governance import (
        create_loop_controller,
        maybe_inject_delivery_idle,
    )

    files = create_loop_controller(
        frozenset({"file_read", "file_list", "grep", "web_search"}),
        files_expected=True,
    )
    assert files.delivery_idle_nudge_rounds == 0
    assert files.delivery_idle_narrow_rounds == 0
    assert files.delivery_idle_report is False
    for i in range(12):
        files.record([ToolAttempt(fingerprint=f"r{i}", tool_name="file_read", success=True)])
    assert (
        maybe_inject_delivery_idle(
            files, messages=[], run_id="files", round_idx=12, role="worker"
        )
        == "none"
    )
    assert not files.take_delivery_idle_narrow_apply()

    report = create_loop_controller(
        frozenset({"grep", "file_read", "code_search"}),
        files_expected=True,
        report_delivery=True,
    )
    assert report.delivery_idle_nudge_rounds == 0
    assert report.delivery_idle_narrow_rounds == 0
    assert report.delivery_idle_report is False
    for i in range(12):
        report.record([ToolAttempt(fingerprint=f"g{i}", tool_name="grep", success=True)])
    assert (
        maybe_inject_delivery_idle(
            report, messages=[], run_id="report", round_idx=12, role="worker"
        )
        == "none"
    )
    assert not report.take_delivery_idle_narrow_apply()


def test_recon_idle_factory_never_nudges():
    """Recon-idle factory is retired: no conclude nudge, no tool narrow."""
    from agentcore.runtime.engine.governance import (
        create_loop_controller,
        maybe_inject_delivery_idle,
    )

    plain = create_loop_controller(
        frozenset({"file_read"}),
        files_expected=False,
    )
    assert plain.delivery_idle_recon is False
    assert plain.delivery_idle_nudge_rounds == 0
    assert plain.delivery_idle_narrow_rounds == 0
    for i in range(12):
        plain.record(
            [ToolAttempt(fingerprint=f"p{i}", tool_name="file_read", success=True)]
        )
    assert plain.delivery_idle_rounds == 0
    messages: list = []
    assert (
        maybe_inject_delivery_idle(
            plain, messages=messages, run_id="r", round_idx=12, role="worker"
        )
        == "none"
    )
    assert messages == []
    assert not plain.take_delivery_idle_narrow_apply()


def test_explicit_controller_still_injects_recon_idle():
    """Leftover API: constructing LoopController with bars still injects."""
    from agentcore.runtime.engine.governance import maybe_inject_delivery_idle

    explicit = LoopController(
        investigation_tools=frozenset({"file_read"}),
        delivery_idle_nudge_rounds=2,
        delivery_idle_recon=True,
    )
    explicit.record([ToolAttempt(fingerprint="p0", tool_name="file_read", success=True)])
    explicit.record([ToolAttempt(fingerprint="p1", tool_name="file_read", success=True)])
    messages: list = []
    assert (
        maybe_inject_delivery_idle(
            explicit, messages=messages, run_id="r", round_idx=2, role="worker"
        )
        == "nudge"
    )
    assert any("调查空转提醒" in str(m.content) for m in messages)


def test_recon_idle_nudge_prompt_does_not_demand_writes():
    from agentcore.runtime.loop_controller import delivery_idle_nudge_prompt

    text = delivery_idle_nudge_prompt(rounds=8, recon=True)
    assert "调查空转提醒" in text
    assert "写盘" not in text
    assert "str_replace" not in text
    assert "handoff" in text.lower() or "escalate" in text.lower()


def test_landing_success_latches_for_wind_down():
    """Successful write still latches landing (wind_down keep_landing uses it)."""
    c = LoopController(
        convergence_finalize_rounds=30,
        convergence_spin_rounds=0,
        investigation_tools=frozenset({"file_read"}),
    )
    c.record([ToolAttempt(fingerprint="f0", tool_name="file_read", success=True)])
    c.record([ToolAttempt(fingerprint="w", tool_name="str_replace", success=True)])
    assert c.landing_succeeded


def test_landing_attempt_does_not_require_zero_write_bar():
    """Failed write is 落盘意图 — landing not yet succeeded, no mid-loop cut."""
    c = LoopController(
        convergence_finalize_rounds=30,
        convergence_spin_rounds=0,
        investigation_tools=frozenset({"file_read"}),
    )
    c.record(
        [
            ToolAttempt(fingerprint="r", tool_name="file_read", success=True),
            ToolAttempt(fingerprint="w", tool_name="str_replace", success=False),
        ]
    )
    assert not c.landing_succeeded
    assert c.convergence_action() is Intervention.CONTINUE


def test_reviews_md_landing_latches():
    """Writing dossier notes counts as product landing."""
    from agentcore.workspace.stage_dirs import REVIEWS_DIR

    c = LoopController(
        convergence_finalize_rounds=30,
        convergence_spin_rounds=0,
        investigation_tools=frozenset({"file_read", "file_list", "grep"}),
    )
    c.record(
        [
            ToolAttempt(
                fingerprint="w",
                tool_name="file_write",
                success=True,
                meta={"path": f"{REVIEWS_DIR}/某修复方案.md"},
            )
        ]
    )
    assert c.landing_succeeded
    # No-path success still latches (compat with older ToolAttempt).
    c2 = LoopController(
        convergence_finalize_rounds=30,
        convergence_spin_rounds=0,
        investigation_tools=frozenset({"file_read"}),
    )
    c2.record(
        [ToolAttempt(fingerprint="legacy", tool_name="str_replace", success=True)]
    )
    assert c2.landing_succeeded


def test_declared_research_artifact_latches_landing():
    from agentcore.workspace.stage_dirs import RESEARCH_DIR

    art = f"{RESEARCH_DIR}/报告.md"
    c = LoopController(
        convergence_finalize_rounds=30,
        convergence_spin_rounds=0,
        investigation_tools=frozenset({"file_read"}),
        product_landing_artifacts=(art,),
    )
    c.record([ToolAttempt(fingerprint="f0", tool_name="file_read", success=True)])
    c.record(
        [
            ToolAttempt(
                fingerprint="w",
                tool_name="file_write",
                success=True,
                meta={"path": art},
            )
        ]
    )
    assert c.landing_succeeded


def test_different_targets_no_longer_trip_zero_write():
    """换文件通读不算 spin；零写梯子已退役 → 不 FINALIZE。"""
    c = LoopController(
        convergence_finalize_rounds=30,
        convergence_spin_rounds=0,
        investigation_tools=frozenset({"file_read", "file_list", "grep"}),
    )
    for i in range(8):
        c.record([ToolAttempt(fingerprint=f"path{i}", tool_name="file_read", success=True)])
    assert c.same_target_investigation_streak == 0
    assert c.convergence_action() is Intervention.CONTINUE
    assert not c.is_thrashing()
