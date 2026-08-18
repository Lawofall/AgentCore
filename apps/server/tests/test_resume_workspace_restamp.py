"""Resume restamps ``<workspace_context>`` after bind-during-ask_user."""

from agentcore.runtime.pipeline.resume.pipeline import _restamp_workspace_facts


def test_restamp_replaces_stale_cloud_facts_with_local():
    old = (
        "<runtime_context>\n当前日期：2026-07-12\n</runtime_context>\n"
        "<workspace_context>\n执行位置：云端沙箱（服务端）\n</workspace_context>\n"
        "rest of prompt"
    )
    new = (
        "<workspace_context>\n"
        "执行位置：用户本机（经桌面通道遥控）\n"
        "</workspace_context>"
    )
    out = _restamp_workspace_facts(old, new)
    assert "云端沙箱" not in out
    assert "用户本机" in out
    assert out.count("<workspace_context>") == 1
    assert "rest of prompt" in out
    # Facts sit after the resident prefix (order 750), not after the date line.
    assert out.index("rest of prompt") < out.index("<workspace_context>")


def test_restamp_inserts_before_attachment_tail():
    old = (
        "<runtime_context>\n当前日期：2026-07-12\n</runtime_context>\n"
        "<按需目录>\n- terminal\n</按需目录>\n"
        "<attached_files>\nfile.md\n</attached_files>"
    )
    new = "<workspace_context>\n执行位置：用户本机\n</workspace_context>"
    out = _restamp_workspace_facts(old, new)
    assert out.index("</按需目录>") < out.index("<workspace_context>")
    assert out.index("</workspace_context>") < out.index("<attached_files>")
