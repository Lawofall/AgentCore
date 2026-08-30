"""Product-landing path gate (dossier notes count as product)."""

from __future__ import annotations

from agentcore.runtime.runs.landing_product import (
    filter_product_landing_paths,
    is_dossier_intermediate_path,
    is_product_landing_path,
    landing_tool_path_from_args,
)
from agentcore.workspace.stage_dirs import RESEARCH_DIR, REVIEWS_DIR


def test_dossier_intermediate_paths():
    assert is_dossier_intermediate_path(f"{REVIEWS_DIR}/修复方案.md")
    assert is_dossier_intermediate_path(f"{RESEARCH_DIR}/报告.md")
    assert is_dossier_intermediate_path(f"{RESEARCH_DIR}/")
    assert not is_dossier_intermediate_path("apps/server/foo.py")
    assert not is_dossier_intermediate_path("site/index.html")


def test_reviews_md_counts_as_product_without_artifacts():
    path = f"{REVIEWS_DIR}/某修复方案.md"
    assert is_product_landing_path(path, None)
    assert is_product_landing_path(path, [])
    assert filter_product_landing_paths([path, "src/a.py"], None) == [
        path,
        "src/a.py",
    ]


def test_research_artifact_still_product():
    art = f"{RESEARCH_DIR}/报告.md"
    assert is_product_landing_path(art, [art])
    assert is_product_landing_path(art, [f"{RESEARCH_DIR}/"])
    assert filter_product_landing_paths([art], [art]) == [art]


def test_missing_path_compat_counts_as_product():
    assert is_product_landing_path(None, None)
    assert is_product_landing_path("", [])


def test_landing_tool_path_from_args():
    assert (
        landing_tool_path_from_args("file_write", {"path": "a.py"}) == "a.py"
    )
    assert (
        landing_tool_path_from_args(
            "file_move", {"source": "a.py", "destination": "b.py"}
        )
        == "b.py"
    )
    assert (
        landing_tool_path_from_args(
            "file_copy", {"source": "a.py", "destination": "out/b.py"}
        )
        == "out/b.py"
    )
    # file_copy / file_move use destination, not source / path.
    assert (
        landing_tool_path_from_args(
            "file_copy", {"source": "a.py", "path": "wrong.py"}
        )
        is None
    )
    assert landing_tool_path_from_args("file_read", {"path": "a.py"}) is None


def test_landing_tools_is_one_object_everywhere():
    """治理面的「笔」只有一份: 各处必须 re-export 同一个对象，不得再抄一份名单。

    从前 tip allowlist / 写参解析 / delivery-idle 各存一份 frozenset，只能靠对齐测试防
    「加了不同步」——``file_copy`` 仍旧在 ``_WRITE_PARSE_TOOLS`` 里漏了一整版。现在同一性
    即对齐：抄一份新的会让这里立刻红。
    """
    from agentcore.runtime.engine import tool_exec_args
    from agentcore.runtime.loop_controller import LANDING_TOOLS as CONTROLLER_TOOLS
    from agentcore.runtime.loop_controller import types as controller_types
    from agentcore.runtime.runs import landing_product
    from agentcore.runtime.runs.serialize import LANDING_TOOLS as SERIALIZE_TOOLS
    from agentcore.tools.file_products import LANDING_TOOLS

    for seen in (
        CONTROLLER_TOOLS,
        controller_types.LANDING_TOOLS,
        tool_exec_args.LANDING_TOOLS,
        landing_product.LANDING_TOOLS,
        SERIALIZE_TOOLS,
    ):
        assert seen is LANDING_TOOLS
    # Every pen names its target through the shared arg reader (no per-tool key table).
    for name in LANDING_TOOLS:
        assert (
            landing_tool_path_from_args(name, {"path": "p.txt"}) == "p.txt"
            or landing_tool_path_from_args(
                name, {"source": "src", "destination": "dst.txt"}
            )
            == "dst.txt"
        )


async def test_every_landing_tool_self_reports_its_product(tmp_path):
    """台账防「谁都没加」: 每支笔执行成功后必须自报它真正落的盘。

    这是白名单消失后的守门测试——旧的对齐测试只能发现「名单加了没同步」，发现不了
    「新工具压根没自报」（正是 .docx 漏账事故的形状）。这里真跑每支笔，断言
    ``ToolResult.file_products`` 就是磁盘上那个路径；不自报即红。
    """
    from agentcore.tools.builtin.file_ops import (
        FileAppendTool,
        FileCopyTool,
        FileMoveTool,
        FileWriteTool,
        StrReplaceTool,
    )
    from agentcore.tools.file_products import LANDING_TOOLS
    from agentcore.tools.protocol import ToolContext
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.server import ServerWorkspace

    def _ctx() -> ToolContext:
        return ToolContext.create(
            execution_id="e",
            run_id="s",
            agent_id="a",
            backend=ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox()),
            user_id="u",
        )

    (tmp_path / "src.txt").write_text("alpha\n", encoding="utf-8")
    cases: list[tuple[str, object, dict, str, str]] = [
        ("file_write", FileWriteTool(), {"path": "报告.md", "content": "# 标题"}, "报告.md", "md"),
        (
            "file_append",
            FileAppendTool(),
            {"path": "报告.md", "content": "\n更多"},
            "报告.md",
            "md",
        ),
        (
            "str_replace",
            StrReplaceTool(),
            {"path": "src.txt", "old_string": "alpha", "new_string": "beta"},
            "src.txt",
            "txt",
        ),
        (
            "file_copy",
            FileCopyTool(),
            {"source": "src.txt", "destination": "out/copy.py"},
            "out/copy.py",
            "code",
        ),
        (
            "file_move",
            FileMoveTool(),
            {"source": "src.txt", "destination": "out/moved.docx"},
            "out/moved.docx",
            "docx",
        ),
    ]
    assert {name for name, *_ in cases} == set(LANDING_TOOLS)

    for name, tool, args, landed, kind in cases:
        result = await tool.execute(args, _ctx())  # type: ignore[attr-defined]
        assert result.success is True, f"{name}: {result.error}"
        assert [(p.path, p.kind, p.derived_from) for p in result.file_products] == [
            (landed, kind, None)
        ], f"{name} 未自报产物"
        assert (tmp_path / landed).exists()


def test_landing_tool_path_sanitizes_dossier_nested():
    nested = f"{RESEARCH_DIR}/子目录/笔记.md"
    assert (
        landing_tool_path_from_args("file_write", {"path": nested})
        == f"{RESEARCH_DIR}/子目录_笔记.md"
    )
