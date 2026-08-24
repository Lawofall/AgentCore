"""Capacity contract vs liveness timeout for FILESYSTEM tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcore.runtime.loop_controller import CircuitBreak, LoopController, ToolAttempt
from agentcore.runtime.tool_deadline import (
    current_tool_deadline,
    derive_channel_timeout,
    reset_tool_deadline,
    set_tool_deadline,
)
from agentcore.tools.builtin.file_ops import FileListTool, FileReadTool, FileWriteTool, MkdirTool
from agentcore.tools.builtin.grep import GrepTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.limits import (
    FILE_TOO_LARGE_DETAIL,
    OFFICE_EXTRACT_MAX_BYTES,
    WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS,
    WORKSPACE_READ_MAX_BYTES,
)
from agentcore.workspace.protocol import WorkspaceIOError
from agentcore.workspace.server import ServerWorkspace


def _ws(root: Path) -> ServerWorkspace:
    return ServerWorkspace(root=root, sandbox=SubprocessSandbox())


def _ctx(ws: ServerWorkspace) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a1",
        backend=ws,
        user_id="u",
    )


@pytest.mark.asyncio
async def test_server_workspace_read_rejects_over_5mib(tmp_path: Path):
    big = tmp_path / "huge.bin"
    big.write_bytes(b"x" * (WORKSPACE_READ_MAX_BYTES + 1))
    ws = _ws(tmp_path)
    with pytest.raises(WorkspaceIOError) as ei:
        await ws.read("huge.bin")
    assert str(ei.value) == FILE_TOO_LARGE_DETAIL


@pytest.mark.asyncio
async def test_server_workspace_read_bytes_rejects_over_5mib(tmp_path: Path):
    big = tmp_path / "huge.bin"
    big.write_bytes(b"x" * (WORKSPACE_READ_MAX_BYTES + 1))
    ws = _ws(tmp_path)
    with pytest.raises(WorkspaceIOError) as ei:
        await ws.read_bytes("huge.bin")
    assert str(ei.value) == FILE_TOO_LARGE_DETAIL


@pytest.mark.asyncio
async def test_resolve_for_download_bypasses_ai_read_gate(tmp_path: Path):
    """Panel download may serve files above AI 5 MiB, under upload-aligned ceiling."""
    from agentcore.config import settings

    mid = WORKSPACE_READ_MAX_BYTES + 1
    assert mid < settings.workspace_upload_max_bytes
    big = tmp_path / "deck.pptx"
    big.write_bytes(b"P" * mid)
    ws = _ws(tmp_path)
    resolved = await ws.resolve_for_download(
        "deck.pptx", max_bytes=settings.workspace_upload_max_bytes
    )
    assert resolved == big.resolve()
    # AI path still gated:
    with pytest.raises(WorkspaceIOError) as ei:
        await ws.read_bytes("deck.pptx")
    assert str(ei.value) == FILE_TOO_LARGE_DETAIL


@pytest.mark.asyncio
async def test_resolve_for_download_rejects_over_upload_ceiling(tmp_path: Path):
    ceiling = 64
    big = tmp_path / "too-big.bin"
    big.write_bytes(b"x" * (ceiling + 1))
    ws = _ws(tmp_path)
    with pytest.raises(WorkspaceIOError) as ei:
        await ws.resolve_for_download("too-big.bin", max_bytes=ceiling)
    assert str(ei.value) == FILE_TOO_LARGE_DETAIL


@pytest.mark.asyncio
async def test_file_read_oversized_is_contract_failure(tmp_path: Path):
    big = tmp_path / "huge.txt"
    big.write_bytes(b"a" * (WORKSPACE_READ_MAX_BYTES + 1))
    result = await FileReadTool().execute({"path": "huge.txt"}, _ctx(_ws(tmp_path)))
    assert result.success is False
    assert result.contract_failure is True
    assert "MiB" in (result.error or "")
    assert result.metadata.get("capacity_contract") == "bytes"


@pytest.mark.asyncio
async def test_file_read_office_extract_budget_is_contract(tmp_path: Path):
    # Under whole-file read max but over extract budget.
    path = tmp_path / "deck.pdf"
    path.write_bytes(b"%PDF-" + b"x" * (OFFICE_EXTRACT_MAX_BYTES + 100))
    assert WORKSPACE_READ_MAX_BYTES > len(path.read_bytes()) > OFFICE_EXTRACT_MAX_BYTES
    result = await FileReadTool().execute({"path": "deck.pdf"}, _ctx(_ws(tmp_path)))
    assert result.success is False
    assert result.contract_failure is True
    assert result.metadata.get("capacity_contract") == "extract_bytes"
    assert "抽取预算" in (result.error or "")


@pytest.mark.asyncio
async def test_file_read_office_extract_timeout_is_contract_not_liveness(tmp_path: Path):
    from unittest.mock import AsyncMock, patch

    from agentcore.workspace.attachment_parse import ExtractResult, ParseStatus

    (tmp_path / "slow.pdf").write_bytes(b"%PDF-x")
    with patch(
        "agentcore.tools.builtin.file_ops.read.extract_office_bytes",
        new=AsyncMock(
            return_value=ExtractResult(
                status=ParseStatus.FAILED, detail="extract_timeout"
            )
        ),
    ):
        result = await FileReadTool().execute({"path": "slow.pdf"}, _ctx(_ws(tmp_path)))
    assert result.success is False
    assert result.contract_failure is True
    assert result.metadata.get("liveness_timeout") is not True
    assert result.metadata.get("extract_timeout") is True
    assert "活性挂起" not in (result.error or "")
    assert "抽文本失败或超时" in (result.error or "")
    assert "markitdown" not in (result.error or "").lower()
    assert "code_execute" not in (result.error or "")


@pytest.mark.asyncio
async def test_file_read_channel_liveness_maps_meta(tmp_path: Path):
    """Single-op settle timeout: liveness meta only — no family sticky retire."""

    class _HangBackend(ServerWorkspace):
        async def read_lines(  # noqa: ARG002
            self, path: str, *, offset: int = 1, limit: int | None = None
        ):
            raise WorkspaceIOError("local workspace op 'read_lines' timed out（活性挂起）")

    result = await FileReadTool().execute(
        {"path": "a.txt"}, _ctx(_HangBackend(tmp_path, sandbox=SubprocessSandbox()))
    )
    assert result.success is False
    assert result.contract_failure is False
    assert result.metadata.get("liveness_timeout") is True
    assert result.metadata.get("timeout_layer") == "channel_op"
    assert result.metadata.get("workspace_channel_dead") is not True
    assert not result.metadata.get("retire_tools")
    assert "活性挂起" in (result.error or "")
    assert "停用全部本地文件" not in (result.error or "")
    assert "禁止再调用文件工具" not in (result.error or "")


@pytest.mark.asyncio
async def test_file_read_channel_dead_stamps_family_retire(tmp_path: Path):
    """Sticky channel-dead detail still stamps family retire + workspace_channel_dead."""

    class _HangBackend(ServerWorkspace):
        async def read_lines(  # noqa: ARG002
            self, path: str, *, offset: int = 1, limit: int | None = None
        ):
            raise WorkspaceIOError(
                "local workspace op 'read_lines' timed out; channel dead（活性挂起）"
            )

    result = await FileReadTool().execute(
        {"path": "a.txt"}, _ctx(_HangBackend(tmp_path, sandbox=SubprocessSandbox()))
    )
    assert result.success is False
    assert result.contract_failure is False
    assert result.metadata.get("liveness_timeout") is True
    assert result.metadata.get("workspace_channel_dead") is True
    assert "file_write" in (result.metadata.get("retire_tools") or [])
    assert "mkdir" in (result.metadata.get("retire_tools") or [])
    assert "活性挂起" in (result.error or "")
    assert "禁止再调用文件工具" in (result.error or "") or "停用全部本地文件" in (
        result.error or ""
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "args", "method"),
    [
        (FileListTool(), {"directory": "."}, "list"),
        (FileWriteTool(), {"path": "a.txt", "content": "x"}, "write"),
        (MkdirTool(), {"path": "nested/d"}, "mkdir"),
        (GrepTool(), {"pattern": "x"}, "grep"),
    ],
)
async def test_filesystem_tools_single_timeout_no_family_retire(
    tmp_path: Path, tool, args, method: str
):
    """Plain settle timeout must not stamp channel-dead family retire meta."""

    class _HangBackend(ServerWorkspace):
        async def list(self, *a, **k):  # noqa: ANN002, ANN003
            raise WorkspaceIOError(f"local workspace op '{method}' timed out（活性挂起）")

        async def write(self, *a, **k):  # noqa: ANN002, ANN003
            raise WorkspaceIOError(f"local workspace op '{method}' timed out（活性挂起）")

        async def mkdir(self, *a, **k):  # noqa: ANN002, ANN003
            raise WorkspaceIOError(f"local workspace op '{method}' timed out（活性挂起）")

        async def grep(self, *a, **k):  # noqa: ANN002, ANN003
            raise WorkspaceIOError(f"local workspace op '{method}' timed out（活性挂起）")

    result = await tool.execute(
        args, _ctx(_HangBackend(tmp_path, sandbox=SubprocessSandbox()))
    )
    assert result.success is False
    assert result.metadata.get("liveness_timeout") is True
    assert result.metadata.get("timeout_layer") == "channel_op"
    assert result.metadata.get("workspace_channel_dead") is not True
    assert not result.metadata.get("retire_tools")
    assert "停用全部本地文件" not in (result.error or "")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "args", "method"),
    [
        (FileListTool(), {"directory": "."}, "list"),
        (FileWriteTool(), {"path": "a.txt", "content": "x"}, "write"),
        (MkdirTool(), {"path": "nested/d"}, "mkdir"),
        (GrepTool(), {"pattern": "x"}, "grep"),
    ],
)
async def test_filesystem_tools_channel_dead_stamps_retire(
    tmp_path: Path, tool, args, method: str
):
    """B2: list / write / mkdir / grep stamp channel-dead retire meta on sticky-dead."""

    class _HangBackend(ServerWorkspace):
        async def list(self, *a, **k):  # noqa: ANN002, ANN003
            raise WorkspaceIOError(
                f"local workspace op '{method}' timed out; channel dead（活性挂起）"
            )

        async def write(self, *a, **k):  # noqa: ANN002, ANN003
            raise WorkspaceIOError(
                f"local workspace op '{method}' timed out; channel dead（活性挂起）"
            )

        async def mkdir(self, *a, **k):  # noqa: ANN002, ANN003
            raise WorkspaceIOError(
                f"local workspace op '{method}' timed out; channel dead（活性挂起）"
            )

        async def grep(self, *a, **k):  # noqa: ANN002, ANN003
            raise WorkspaceIOError(
                f"local workspace op '{method}' timed out; channel dead（活性挂起）"
            )

    result = await tool.execute(
        args, _ctx(_HangBackend(tmp_path, sandbox=SubprocessSandbox()))
    )
    assert result.success is False
    assert result.metadata.get("liveness_timeout") is True
    assert result.metadata.get("workspace_channel_dead") is True
    retire = result.metadata.get("retire_tools") or []
    for name in WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS:
        assert name in retire
    assert "停用全部本地文件" in (result.error or "") or "禁止再调用文件工具" in (
        result.error or ""
    )


@pytest.mark.asyncio
async def test_file_write_preread_timeout_does_not_pretend_success(tmp_path: Path):
    """Pre-read settle timeout surfaces as failure; must not swallow into write success."""

    class _HangBackend(ServerWorkspace):
        async def read(self, path: str) -> str:  # noqa: ARG002
            raise WorkspaceIOError("local workspace op 'read' timed out（活性挂起）")

        async def write(self, *a, **k):  # noqa: ANN002, ANN003
            raise AssertionError("write must not be called after timed-out pre-read")

    result = await FileWriteTool().execute(
        {"path": "a.txt", "content": "x"},
        _ctx(_HangBackend(tmp_path, sandbox=SubprocessSandbox())),
    )
    assert result.success is False
    assert result.metadata.get("liveness_timeout") is True
    assert result.metadata.get("workspace_channel_dead") is not True
    assert "活性挂起" in (result.error or "")


@pytest.mark.asyncio
async def test_file_write_preread_channel_dead_does_not_pretend_success(tmp_path: Path):
    """Pre-read sticky-dead must surface channel-dead; must not swallow into write success."""

    class _HangBackend(ServerWorkspace):
        async def read(self, path: str) -> str:  # noqa: ARG002
            raise WorkspaceIOError(
                "local workspace op 'read' timed out; channel dead（活性挂起）"
            )

        async def write(self, *a, **k):  # noqa: ANN002, ANN003
            raise AssertionError("write must not be called after channel-dead pre-read")

    result = await FileWriteTool().execute(
        {"path": "a.txt", "content": "x"},
        _ctx(_HangBackend(tmp_path, sandbox=SubprocessSandbox())),
    )
    assert result.success is False
    assert result.metadata.get("liveness_timeout") is True
    assert result.metadata.get("workspace_channel_dead") is True
    assert "活性挂起" in (result.error or "")


def test_derive_channel_timeout_from_outer_deadline():
    token = set_tool_deadline(60.0)
    try:
        assert current_tool_deadline() == 60.0
        # Slave ≤ outer − slack
        assert derive_channel_timeout(channel_default=60.0) == 59.0
        assert derive_channel_timeout(explicit=90.0, channel_default=60.0) == 59.0
        assert derive_channel_timeout(explicit=30.0, channel_default=60.0) == 30.0
    finally:
        reset_tool_deadline(token)
    assert current_tool_deadline() is None
    assert derive_channel_timeout(channel_default=60.0) == 60.0


def test_liveness_circuit_first_fail_retires():
    """Workspace / outer liveness is permanent: first fail retires the tool."""
    ctrl = LoopController(
        tool_failure_warn=2,
        tool_failure_disable=3,
        unproductive_threshold=99,
    )
    ctrl.record(
        [
            ToolAttempt(
                "fp",
                "file_read",
                success=False,
                error_summary="活性挂起",
                meta={"liveness_timeout": True, "error_class": "permanent"},
            )
        ]
    )
    br = ctrl.tool_circuit_breaker()
    assert br.disabled == ("file_read",)
    assert br.warned == ()
    msg = br.message() or ""
    assert "停用" in msg or "原样重试" in msg or "换路径" in msg


def test_circuit_break_liveness_field_defaults():
    assert CircuitBreak().liveness_warned == frozenset()
    assert CircuitBreak().validation_stop is None
