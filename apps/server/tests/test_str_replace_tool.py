"""Tests for the str_replace tool (precise, byte-faithful, atomic file edit).

Hermetic: every test edits a throwaway file under ``tmp_path`` and reads the
bytes back to assert the on-disk result, including line-ending fidelity.
"""

from pathlib import Path

import pytest

from agentcore.tools.builtin.file_ops import StrReplaceTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def test_schema_old_string_min_length():
    """Schema 层声明 minLength=1，引导模型勿传空 old_string。"""
    props = StrReplaceTool().schema.parameters["properties"]["old_string"]
    assert props["type"] == "string"
    assert props["minLength"] == 1


def _ctx(workspace: Path) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=workspace, sandbox=SubprocessSandbox()),
        user_id="u",
    )


# --- validation / failure paths ---


async def test_requires_old_string(tmp_path: Path):
    (tmp_path / "f.txt").write_text("hi", encoding="utf-8")
    result = await StrReplaceTool().execute(
        {"path": "f.txt", "old_string": "", "new_string": "x"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert "old_string 不能为空" in result.error
    # 参数契约拒绝：不得计入 run 级工具熔断（连续空参空转）。
    assert result.contract_failure is True


async def test_empty_old_string_does_not_trip_circuit_breaker(tmp_path: Path):
    """回归：空 old_string 打回须跳过 cumulative breaker warn/disable。"""
    from agentcore.runtime.loop_controller import LoopController, ToolAttempt

    (tmp_path / "f.txt").write_text("hi", encoding="utf-8")
    tool = StrReplaceTool()
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    for i in range(5):
        result = await tool.execute(
            {"path": "f.txt", "old_string": "", "new_string": "x"}, _ctx(tmp_path)
        )
        assert result.contract_failure is True
        c.record(
            [
                ToolAttempt(
                    f"empty-{i}",
                    "str_replace",
                    success=False,
                    contract_failure=True,
                    error_summary=result.error or "",
                )
            ]
        )
        assert not c.tool_circuit_breaker(), (
            f"empty old_string round {i + 1} must not trip circuit breaker"
        )


async def test_rejects_identical_strings(tmp_path: Path):
    (tmp_path / "f.txt").write_text("hi", encoding="utf-8")
    result = await StrReplaceTool().execute(
        {"path": "f.txt", "old_string": "hi", "new_string": "hi"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert "相同" in result.error
    assert "空转" in result.error
    assert result.contract_failure is True


def test_identical_edit_fingerprint_collapses_per_path():
    """不同 noop 正文塌缩为同 path 指纹 → validation 早熔断（案 longdoc-revise）。"""
    from agentcore.runtime.loop_controller import (
        LoopController,
        ToolAttempt,
        fingerprint_tool_call,
    )

    fp_a = fingerprint_tool_call(
        "str_replace",
        '{"path": "报告.md", "old_string": "AAA", "new_string": "AAA"}',
    )
    fp_b = fingerprint_tool_call(
        "str_replace",
        '{"path": "报告.md", "old_string": "BBB 不同正文", "new_string": "BBB 不同正文"}',
    )
    fp_other = fingerprint_tool_call(
        "str_replace",
        '{"path": "other.md", "old_string": "AAA", "new_string": "AAA"}',
    )
    assert fp_a == fp_b
    assert fp_a != fp_other

    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    rej = ToolAttempt(
        fp_a,
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
    assert c.tool_failure_count("str_replace") == 0


def test_write_section_invalid_section_fingerprint_collapses_and_thrashes():
    """不同非法 section（chN-sM）同 path 塌缩 → validation thrash 早停。"""
    from agentcore.runtime.loop_controller import (
        LoopController,
        ToolAttempt,
        fingerprint_tool_call,
    )

    fp_a = fingerprint_tool_call(
        "write_section",
        '{"path": "site/index.html", "section": "ch5-s0", "content": "<p>a</p>"}',
    )
    fp_b = fingerprint_tool_call(
        "write_section",
        '{"path": "site/index.html", "section": "ch1-s2", "content": "<p>b</p>"}',
    )
    fp_ok = fingerprint_tool_call(
        "write_section",
        '{"path": "site/index.html", "section": "s0", "content": "<p>a</p>"}',
    )
    fp_other_path = fingerprint_tool_call(
        "write_section",
        '{"path": "site/other.html", "section": "ch5-s0", "content": "<p>a</p>"}',
    )
    assert fp_a == fp_b
    assert fp_a != fp_ok
    assert fp_a != fp_other_path

    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    rej = ToolAttempt(
        fp_a,
        "write_section",
        success=False,
        contract_failure=True,
        meta={"error_class": "validation"},
    )
    c.record([rej])
    c.record([rej])
    assert c.tool_circuit_breaker().validation_stop is not None
    c.record([rej])
    assert c.is_thrashing()
    assert c.take_validation_hard_stop()
    assert c.tool_failure_count("write_section") == 0


async def test_rejects_path_outside_workspace(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    result = await StrReplaceTool().execute(
        {"path": "../secret.txt", "old_string": "secret", "new_string": "x"},
        _ctx(ws),
    )
    assert result.success is False
    assert "超出了工作区范围" in result.error
    assert result.failure_code == "outside_workspace"
    # the out-of-tree file must be untouched
    assert (tmp_path / "secret.txt").read_text(encoding="utf-8") == "top secret"


async def test_access_denied_is_lock_not_grant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from agentcore.workspace.protocol import WorkspaceIOError

    (tmp_path / "f.txt").write_text("hello", encoding="utf-8")
    ctx = _ctx(tmp_path)

    async def locked(*_a, **_k):  # noqa: ANN001
        raise WorkspaceIOError("[WinError 5] 拒绝访问: 'f.txt'")

    monkeypatch.setattr(ctx.backend, "replace", locked)
    result = await StrReplaceTool().execute(
        {"path": "f.txt", "old_string": "hello", "new_string": "world"},
        ctx,
    )
    assert result.success is False
    assert result.failure_code == "access_denied"
    assert "写入被占用" in result.error
    assert "超出了工作区范围" not in result.error
    assert "没授权" not in result.error or "不是没授权" in result.error


async def test_file_not_found(tmp_path: Path):
    result = await StrReplaceTool().execute(
        {"path": "nope.txt", "old_string": "a", "new_string": "b"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert "文件不存在" in result.error


async def test_rejects_directory(tmp_path: Path):
    (tmp_path / "d").mkdir()
    result = await StrReplaceTool().execute(
        {"path": "d", "old_string": "a", "new_string": "b"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert "不是文件" in result.error


async def test_rejects_binary_file(tmp_path: Path):
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00\x01")
    result = await StrReplaceTool().execute(
        {"path": "blob.bin", "old_string": "a", "new_string": "b"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert "非 UTF-8" in result.error


async def test_old_string_not_found(tmp_path: Path):
    (tmp_path / "f.txt").write_text("hello world\nsecond line\n", encoding="utf-8")
    result = await StrReplaceTool().execute(
        {"path": "f.txt", "old_string": "xyz", "new_string": "b"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert "找不到" in result.error
    # 阶段3：失败回执必须带回磁盘片段（真源），不能只报「找不到」。
    assert "磁盘原文" in (result.error or "")
    assert "hello world" in (result.error or "")
    assert "勿残缺骨架交差" in (result.error or "")
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "hello world\nsecond line\n"


async def test_no_match_includes_fuzzy_near_miss(tmp_path: Path):
    """Near-miss old_string still gets disk candidates marked non-exact."""
    (tmp_path / "app.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8"
    )
    result = await StrReplaceTool().execute(
        {
            "path": "app.py",
            "old_string": "return a - b;",  # trailing ; drift
            "new_string": "return a + b",
        },
        _ctx(tmp_path),
    )
    assert result.success is False
    assert "找不到" in (result.error or "")
    assert "非精确" in (result.error or "")
    assert "return a - b" in (result.error or "")


async def test_non_unique_without_replace_all_fails(tmp_path: Path):
    (tmp_path / "f.txt").write_text("x = 1\nx = 2\n", encoding="utf-8")
    result = await StrReplaceTool().execute(
        {"path": "f.txt", "old_string": "x", "new_string": "y"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert "不唯一" in result.error
    assert "2 处" in result.error
    assert "精确命中" in (result.error or "")
    assert "x = 1" in (result.error or "")
    # nothing changed
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "x = 1\nx = 2\n"


# --- core edit behavior ---


async def test_single_unique_replacement(tmp_path: Path):
    f = tmp_path / "app.py"
    f.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    result = await StrReplaceTool().execute(
        {"path": "app.py", "old_string": "return a - b", "new_string": "return a + b"},
        _ctx(tmp_path),
    )
    assert result.success is True
    assert result.metadata["replacements"] == 1
    assert "约第 2 行" in result.output
    # 回执回显改动落点上下文（所改即所见），让 worker 当轮确认替换落对没、免掉回读自检那一轮。
    assert "return a + b" in result.output
    assert "改动落点" in result.output
    assert f.read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"


async def test_replace_all(tmp_path: Path):
    f = tmp_path / "f.txt"
    f.write_text("a\na\na\n", encoding="utf-8")
    result = await StrReplaceTool().execute(
        {"path": "f.txt", "old_string": "a", "new_string": "b", "replace_all": True},
        _ctx(tmp_path),
    )
    assert result.success is True
    assert result.metadata["replacements"] == 3
    assert f.read_text(encoding="utf-8") == "b\nb\nb\n"


async def test_multiline_old_string(tmp_path: Path):
    f = tmp_path / "f.txt"
    # write_bytes (not write_text) so the test controls line endings exactly —
    # on Windows write_text would translate \n to \r\n and the LF old_string
    # below would (correctly) no longer match.
    f.write_bytes(b"line1\nline2\nline3\n")
    result = await StrReplaceTool().execute(
        {
            "path": "f.txt",
            "old_string": "line1\nline2\n",
            "new_string": "lineA\n",
        },
        _ctx(tmp_path),
    )
    assert result.success is True
    assert f.read_bytes() == b"lineA\nline3\n"


async def test_preserves_crlf_line_endings(tmp_path: Path):
    """Byte fidelity: a CRLF file must stay CRLF after an edit (no translation)."""
    f = tmp_path / "win.txt"
    f.write_bytes(b"alpha\r\nTARGET\r\nomega\r\n")
    result = await StrReplaceTool().execute(
        {"path": "win.txt", "old_string": "TARGET", "new_string": "DONE"},
        _ctx(tmp_path),
    )
    assert result.success is True
    assert f.read_bytes() == b"alpha\r\nDONE\r\nomega\r\n"


async def test_crlf_file_accepts_lf_multiline_old_string(tmp_path: Path):
    """CRLF on disk + LF multiline old_string: normalize fallback, write-back CRLF."""
    f = tmp_path / "win.txt"
    f.write_bytes(b"line1\r\nline2\r\nline3\r\n")
    result = await StrReplaceTool().execute(
        {
            "path": "win.txt",
            "old_string": "line1\nline2\n",
            "new_string": "lineA\n",
        },
        _ctx(tmp_path),
    )
    assert result.success is True
    assert result.metadata["replacements"] == 1
    assert f.read_bytes() == b"lineA\r\nline3\r\n"


async def test_replacement_inserts_new_text_verbatim(tmp_path: Path):
    f = tmp_path / "f.txt"
    f.write_text("key: old\n", encoding="utf-8")
    result = await StrReplaceTool().execute(
        {"path": "f.txt", "old_string": "old", "new_string": "new value 123"},
        _ctx(tmp_path),
    )
    assert result.success is True
    assert f.read_text(encoding="utf-8") == "key: new value 123\n"
