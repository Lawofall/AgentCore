"""CEO turn envelope: volatile facts outside ``role: system``."""

from __future__ import annotations

import re

from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.facts import TurnStartedFact
from agentcore.runtime.journal import window_from_journal
from agentcore.runtime.resolve.prompt import (
    TURN_ENVELOPE_FENCE,
    assemble_system_prompt,
    compose_ceo_chat_prompt,
    compose_worker_base_prompt,
    opening_ceo_messages,
    render_ceo_turn_envelope,
    render_worker_turn_envelope,
    visualization_system_body,
)
from agentcore.runtime.skills import build_system_skill_registry


def test_frozen_ceo_system_omits_runtime_and_workspace():
    ceo = compose_ceo_chat_prompt(
        assemble_system_prompt(),
        skill_registry=build_system_skill_registry(),
        ceo_tool_names={"delegate", "consult"},
    )
    assert "<运行时>" not in ceo
    assert "</工作区>" not in ceo
    assert "<身份>" not in ceo
    assert "<按需目录>" in ceo


def test_worker_system_omits_runtime_workspace_and_attachments():
    worker = compose_worker_base_prompt(
        assemble_system_prompt(),
        workspace_context="<工作区>\n执行：云端\n</工作区>",
        attachment_context="<附件>\nfile.md\n</附件>",
    )
    assert "<运行时>" not in worker
    assert "</工作区>" not in worker
    assert "<附件>" not in worker
    assert re.search(r"当前日期：\d{4}-\d{2}-\d{2}", worker) is None


def test_worker_envelope_carries_date_and_workspace_without_file_index():
    env = render_worker_turn_envelope(
        workspace_context="<工作区>\n执行：云端\n</工作区>",
        attachment_context="<附件>\nfile.md\n</附件>",
    )
    assert env.startswith(TURN_ENVELOPE_FENCE)
    assert re.search(r"当前日期：\d{4}-\d{2}-\d{2}", env)
    assert "<工作区>" in env
    assert "执行：云端" in env
    assert "<附件>" in env
    assert "文件：空" not in env
    assert "文件索引" not in env


def test_envelope_carries_date_workspace_and_fence():
    env = render_ceo_turn_envelope(
        workspace_context="<工作区>\n执行：云端\n</工作区>",
        workspace_file_index="文件：空",
    )
    assert env.startswith(TURN_ENVELOPE_FENCE)
    assert re.search(r"当前日期：\d{4}-\d{2}-\d{2}", env)
    assert not re.search(r"\d{2}:\d{2}:\d{2}", env)
    assert "<工作区>" in env
    assert "文件：空" in env
    assert env.index("执行：云端") < env.index("文件：空") < env.index("</工作区>")


def test_envelope_date_stable_within_a_day():
    a = render_ceo_turn_envelope(include_runtime=True)
    b = render_ceo_turn_envelope(include_runtime=True)
    assert a == b


def test_opening_messages_insert_envelope_between_history_and_user():
    history = [LLMMessage(role="user", content="hi"), LLMMessage(role="assistant", content="ok")]
    messages = opening_ceo_messages(
        system_prompt="SYS",
        history=history,
        turn_envelope=f"{TURN_ENVELOPE_FENCE}\n<运行时/>",
        user_content="go",
    )
    assert [m.role for m in messages] == ["system", "user", "assistant", "user", "user"]
    assert messages[0].content == "SYS"
    assert messages[3].content.startswith(TURN_ENVELOPE_FENCE)
    assert messages[4].content == "go"


def test_opening_replays_prior_envelope_then_appends():
    env1 = f"{TURN_ENVELOPE_FENCE}\n<运行时/>"
    env2 = f"{TURN_ENVELOPE_FENCE}\n<表格/>"
    history = [
        LLMMessage(role="user", content=env1),
        LLMMessage(role="user", content="q1"),
        LLMMessage(role="assistant", content="a1"),
    ]
    messages = opening_ceo_messages(
        system_prompt="SYS",
        history=history,
        turn_envelope=env2,
        user_content="q2",
    )
    assert [m.content for m in messages] == ["SYS", env1, "q1", "a1", env2, "q2"]


def test_opening_omits_envelope_identical_to_last_in_window():
    env = f"{TURN_ENVELOPE_FENCE}\n<运行时/>"
    history = [
        LLMMessage(role="user", content=env),
        LLMMessage(role="user", content="q1"),
        LLMMessage(role="assistant", content="a1"),
    ]
    messages = opening_ceo_messages(
        system_prompt="SYS",
        history=history,
        turn_envelope=env,
        user_content="q2",
    )
    assert [m.content for m in messages] == ["SYS", env, "q1", "a1", "q2"]


def test_opening_omits_empty_envelope():
    messages = opening_ceo_messages(
        system_prompt="SYS",
        history=None,
        turn_envelope="",
        user_content="go",
    )
    assert messages == [
        LLMMessage(role="system", content="SYS"),
        LLMMessage(role="user", content="go"),
    ]


def test_fold_inserts_envelope_from_turn_started():
    env = f"{TURN_ENVELOPE_FENCE}\n<工作区>\n执行：云端\n</工作区>"
    entries = [
        TurnStartedFact(
            system_prompt="SYS",
            user_message="go",
            model_profile="m",
            turn_envelope=env,
        )
        .to_fact()
        .entry()
    ]
    assert window_from_journal(entries) == opening_ceo_messages(
        system_prompt="SYS",
        history=None,
        turn_envelope=env,
        user_content="go",
    )


def test_fold_old_journal_without_envelope_unchanged():
    entries = [
        {
            "kind": "turn_started",
            "payload": {
                "system_prompt": "SYS",
                "user_message": "go",
                "model_profile": "m",
                "history_len": 0,
            },
            "ts": None,
        }
    ]
    assert window_from_journal(entries) == [
        LLMMessage(role="system", content="SYS"),
        LLMMessage(role="user", content="go"),
    ]


def test_visualization_concatenates_envelope_xml_into_system():
    env = render_ceo_turn_envelope(
        workspace_context="<工作区>\n桌：设计\n</工作区>",
        include_runtime=False,
    )
    body = visualization_system_body("你是 CEO。", env)
    assert TURN_ENVELOPE_FENCE not in body
    assert body.startswith("你是 CEO。")
    assert "<工作区>" in body
    assert "桌：设计" in body


def test_opening_appends_in_history_system_after_history_before_envelope():
    env = f"{TURN_ENVELOPE_FENCE}\n<运行时/>"
    history = [
        LLMMessage(role="user", content="q1"),
        LLMMessage(role="assistant", content="a1"),
    ]
    messages = opening_ceo_messages(
        system_prompt="SYS v1",
        history=history,
        turn_envelope=env,
        user_content="q2",
        in_history_system="SYS v2",
    )
    assert [m.role for m in messages] == [
        "system",
        "user",
        "assistant",
        "system",
        "user",
        "user",
    ]
    assert messages[0].content == "SYS v1"
    assert messages[3].content == "SYS v2"
    assert messages[4].content.startswith(TURN_ENVELOPE_FENCE)
    assert messages[5].content == "q2"


def test_opening_skips_duplicate_in_history_already_in_window():
    env = f"{TURN_ENVELOPE_FENCE}\n<运行时/>"
    history = [
        LLMMessage(role="system", content="SYS v2"),
        LLMMessage(role="user", content=env),
        LLMMessage(role="user", content="q1"),
        LLMMessage(role="assistant", content="a1"),
    ]
    messages = opening_ceo_messages(
        system_prompt="SYS v1",
        history=history,
        turn_envelope=env,
        user_content="q2",
        in_history_system="SYS v2",
    )
    assert [m.role for m in messages] == [
        "system",
        "system",
        "user",
        "user",
        "assistant",
        "user",
    ]
    assert [m.content for m in messages if m.role == "system"] == ["SYS v1", "SYS v2"]
    assert [m.content for m in messages].count(env) == 1


def test_opening_skips_in_history_when_equal_to_node0():
    messages = opening_ceo_messages(
        system_prompt="SYS",
        history=None,
        turn_envelope="",
        user_content="go",
        in_history_system="SYS",
    )
    assert [m.role for m in messages] == ["system", "user"]


def test_fold_inserts_in_history_system_from_turn_started():
    env = f"{TURN_ENVELOPE_FENCE}\n<工作区>\n执行：云端\n</工作区>"
    entries = [
        TurnStartedFact(
            system_prompt="SYS v1",
            user_message="go",
            model_profile="m",
            turn_envelope=env,
            in_history_system="SYS v2",
        )
        .to_fact()
        .entry()
    ]
    assert window_from_journal(entries) == opening_ceo_messages(
        system_prompt="SYS v1",
        history=None,
        turn_envelope=env,
        user_content="go",
        in_history_system="SYS v2",
    )
