"""Resume restamps ``<工作区>`` after bind-during-ask_user."""

from agentcore.runtime.pipeline.resume.pipeline import _restamp_workspace_facts


def test_restamp_replaces_stale_cloud_facts_with_local():
    old = (
        "<运行时>\n当前日期：2026-07-12\n</运行时>\n"
        "<工作区>\n执行：云端沙箱\n</工作区>\n"
        "rest of prompt"
    )
    new = (
        "<工作区>\n"
        "执行：用户本机\n"
        "</工作区>"
    )
    out = _restamp_workspace_facts(old, new)
    assert "云端沙箱" not in out
    assert "用户本机" in out
    assert out.count("<工作区>") == 1
    assert "rest of prompt" in out
    # Facts sit after the resident prefix (order 750), not after the date line.
    assert out.index("rest of prompt") < out.index("<工作区>")


def test_restamp_inserts_before_attachment_tail():
    old = (
        "<运行时>\n当前日期：2026-07-12\n</运行时>\n"
        "<按需目录>\n- terminal\n</按需目录>\n"
        "<附件>\nfile.md\n</附件>"
    )
    new = "<工作区>\n执行：用户本机\n</工作区>"
    out = _restamp_workspace_facts(old, new)
    assert out.index("</按需目录>") < out.index("<工作区>")
    assert out.index("</工作区>") < out.index("<附件>")
