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
from agentcore.tools.builtin.file_ops import (
    FileListTool,
    FileReadTool,
    FileWriteTool,
    GlobTool,
    MkdirTool,
)
from agentcore.tools.builtin.grep import GrepTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.limits import (
    WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS,
    WORKSPACE_READ_MAX_BYTES,
    is_file_too_large_detail,
)
from agentcore.workspace.protocol import PathNotFound, WorkspaceIOError
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
    assert is_file_too_large_detail(str(ei.value))


@pytest.mark.asyncio
async def test_server_workspace_read_bytes_rejects_over_5mib(tmp_path: Path):
    big = tmp_path / "huge.bin"
    big.write_bytes(b"x" * (WORKSPACE_READ_MAX_BYTES + 1))
    ws = _ws(tmp_path)
    with pytest.raises(WorkspaceIOError) as ei:
        await ws.read_bytes("huge.bin")
    assert is_file_too_large_detail(str(ei.value))


@pytest.mark.asyncio
async def test_server_workspace_read_bytes_extract_cap_allows_over_5mib(tmp_path: Path):
    from agentcore.workspace.limits import OFFICE_EXTRACT_CHANNEL_MAX_BYTES

    mid = WORKSPACE_READ_MAX_BYTES + 16
    big = tmp_path / "mid.bin"
    big.write_bytes(b"x" * mid)
    ws = _ws(tmp_path)
    data = await ws.read_bytes("mid.bin", max_bytes=OFFICE_EXTRACT_CHANNEL_MAX_BYTES)
    assert len(data) == mid


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
    assert is_file_too_large_detail(str(ei.value))


@pytest.mark.asyncio
async def test_resolve_for_download_rejects_over_upload_ceiling(tmp_path: Path):
    ceiling = 64
    big = tmp_path / "too-big.bin"
    big.write_bytes(b"x" * (ceiling + 1))
    ws = _ws(tmp_path)
    with pytest.raises(WorkspaceIOError) as ei:
        await ws.resolve_for_download("too-big.bin", max_bytes=ceiling)
    assert is_file_too_large_detail(str(ei.value))


@pytest.mark.asyncio
async def test_file_read_oversized_is_contract_failure(tmp_path: Path):
    big = tmp_path / "huge.txt"
    big.write_bytes(b"a" * (WORKSPACE_READ_MAX_BYTES + 1))
    result = await FileReadTool().execute({"path": "huge.txt"}, _ctx(_ws(tmp_path)))
    assert result.success is False
    assert result.contract_failure is True
    assert result.failure_code == "too_large"
    assert "MiB" in (result.error or "")
    assert "请用户" not in (result.error or "")
    assert result.metadata.get("capacity_contract") == "bytes"


@pytest.mark.asyncio
async def test_file_read_office_midsize_extracts_not_budget_failure(tmp_path: Path):
    """3 MiB-class PDF is under the text 5 MiB gate and must extract, not fail-fast."""
    from unittest.mock import AsyncMock, patch

    from agentcore.workspace.attachment_parse import ExtractResult, ParseStatus

    path = tmp_path / "deck.pdf"
    path.write_bytes(b"%PDF-" + b"x" * (3 * 1024 * 1024))
    assert len(path.read_bytes()) < WORKSPACE_READ_MAX_BYTES
    with patch(
        "agentcore.tools.builtin.file_ops.read._extract_office",
        new=AsyncMock(
            return_value=ExtractResult(
                status=ParseStatus.OK, text="Abstract in output\n", detail="ok"
            )
        ),
    ):
        result = await FileReadTool().execute({"path": "deck.pdf"}, _ctx(_ws(tmp_path)))
    assert result.success is True
    assert "Abstract in output" in (result.output or "")
    assert "抽取预算" not in (result.output or "")
    assert "请用户" not in (result.output or "")


@pytest.mark.asyncio
async def test_file_read_office_over_text_gate_still_extracts(tmp_path: Path):
    """6 MiB-class PDF exceeds the text 5 MiB gate but must still extract."""
    from unittest.mock import AsyncMock, patch

    from agentcore.workspace.attachment_parse import ExtractResult, ParseStatus

    path = tmp_path / "contract.pdf"
    path.write_bytes(b"%PDF-" + b"x" * (WORKSPACE_READ_MAX_BYTES + 16))
    with patch(
        "agentcore.tools.builtin.file_ops.read._extract_office",
        new=AsyncMock(
            return_value=ExtractResult(
                status=ParseStatus.OK, text="Loan contract clause\n", detail="ok"
            )
        ),
    ):
        result = await FileReadTool().execute(
            {"path": "contract.pdf"}, _ctx(_ws(tmp_path))
        )
    assert result.success is True
    assert "Loan contract clause" in (result.output or "")
    assert "请用户" not in (result.output or "")


@pytest.mark.asyncio
async def test_file_read_office_parent_does_not_read_bytes(tmp_path: Path):
    """Sidecar/cloud disk extract must not slurp the PDF through ``read_bytes``."""
    from unittest.mock import AsyncMock, patch

    from agentcore.workspace.attachment_parse import ExtractResult, ParseStatus

    (tmp_path / "deck.pdf").write_bytes(b"%PDF-x")
    calls: list[str] = []
    orig = ServerWorkspace.read_bytes

    async def spy(self, path: str, *, max_bytes: int | None = None) -> bytes:
        calls.append(path)
        return await orig(self, path, max_bytes=max_bytes)

    with (
        patch.object(ServerWorkspace, "read_bytes", spy),
        patch(
            "agentcore.workspace.attachment_parse.extract_office_file",
            new=AsyncMock(
                return_value=ExtractResult(
                    status=ParseStatus.OK,
                    text="Abstract in output\n",
                    detail="ok",
                    size_bytes=6,
                )
            ),
        ),
    ):
        result = await FileReadTool().execute(
            {"path": "deck.pdf"}, _ctx(_ws(tmp_path))
        )
    assert result.success is True
    assert "Abstract in output" in (result.output or "")
    assert calls == []


@pytest.mark.asyncio
async def test_file_read_ole_does_not_read_bytes(tmp_path: Path):
    from unittest.mock import patch

    (tmp_path / "memo.doc").write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 32)
    calls: list[str] = []
    orig = ServerWorkspace.read_bytes

    async def spy(self, path: str, *, max_bytes: int | None = None) -> bytes:
        calls.append(path)
        return await orig(self, path, max_bytes=max_bytes)

    with patch.object(ServerWorkspace, "read_bytes", spy):
        result = await FileReadTool().execute(
            {"path": "memo.doc"}, _ctx(_ws(tmp_path))
        )
    assert result.success is True
    assert "ole" in (result.output or "").lower()
    assert calls == []


@pytest.mark.asyncio
async def test_file_read_misnamed_oversize_pdf_is_truncated_envelope(tmp_path: Path):
    """``.txt`` that is a huge PDF must sniff via peek, not ``too_large`` contract."""
    from agentcore.workspace.limits import (
        FILE_TOO_LARGE_DETAIL,
        OFFICE_EXTRACT_DISK_MAX_BYTES,
    )
    from agentcore.workspace.protocol import ReadHeadResult

    class _PeekBackend(ServerWorkspace):
        async def read_lines(  # noqa: ARG002
            self, path: str, *, offset: int = 1, limit: int | None = None
        ):
            raise WorkspaceIOError(
                f"{FILE_TOO_LARGE_DETAIL}（{OFFICE_EXTRACT_DISK_MAX_BYTES + 1}字节）"
            )

        async def read_head(  # noqa: ARG002
            self, path: str, *, max_bytes: int | None = None
        ) -> ReadHeadResult:
            return ReadHeadResult(
                data=b"%PDF-x\x00",
                size_bytes=OFFICE_EXTRACT_DISK_MAX_BYTES + 1,
            )

        async def extract_office(
            self, path: str, *, ext: str, start_page: int = 1
        ):  # noqa: ARG002
            raise WorkspaceIOError(
                f"{FILE_TOO_LARGE_DETAIL}（{OFFICE_EXTRACT_DISK_MAX_BYTES + 1}字节）"
            )

    (tmp_path / "notes.txt").write_bytes(b"%PDF-x")
    result = await FileReadTool().execute(
        {"path": "notes.txt"},
        _ctx(_PeekBackend(tmp_path, sandbox=SubprocessSandbox())),
    )
    assert result.success is True
    assert result.contract_failure is not True
    out = result.output or ""
    assert "[观察信封]" in out
    assert "kind: truncated" in out
    assert "请用户" not in out


@pytest.mark.asyncio
async def test_file_read_office_source_over_extract_cap_is_truncated_envelope(
    tmp_path: Path,
):
    from agentcore.workspace.limits import (
        FILE_TOO_LARGE_DETAIL,
        OFFICE_EXTRACT_DISK_MAX_BYTES,
    )

    class _CapBackend(ServerWorkspace):
        async def read(self, path: str) -> str:  # noqa: ARG002
            raise PathNotFound(path)

        async def extract_office(
            self, path: str, *, ext: str, start_page: int = 1
        ):  # noqa: ARG002
            raise WorkspaceIOError(
                f"{FILE_TOO_LARGE_DETAIL}（{OFFICE_EXTRACT_DISK_MAX_BYTES + 1}字节）"
            )

    (tmp_path / "huge.pdf").write_bytes(b"%PDF-x")
    result = await FileReadTool().execute(
        {"path": "huge.pdf"}, _ctx(_CapBackend(tmp_path, sandbox=SubprocessSandbox()))
    )
    assert result.success is True
    out = result.output or ""
    assert "[观察信封]" in out
    assert "kind: truncated" in out
    assert "请用户" not in out


@pytest.mark.asyncio
async def test_file_read_office_extract_timeout_is_observation_not_liveness(tmp_path: Path):
    from unittest.mock import AsyncMock, patch

    from agentcore.workspace.attachment_parse import ExtractResult, ParseStatus

    (tmp_path / "slow.pdf").write_bytes(b"%PDF-x")
    with patch(
        "agentcore.tools.builtin.file_ops.read._extract_office",
        new=AsyncMock(
            return_value=ExtractResult(
                status=ParseStatus.FAILED, detail="extract_timeout"
            )
        ),
    ):
        result = await FileReadTool().execute({"path": "slow.pdf"}, _ctx(_ws(tmp_path)))
    assert result.success is True
    out = result.output or ""
    assert result.metadata.get("liveness_timeout") is not True
    assert "活性挂起" not in out
    assert "[观察信封]" in out
    assert "kind: extract" in out
    assert "超时" in out
    assert "extract_timeout" not in out
    assert "markitdown" not in out.lower()
    assert "请用 code_execute" not in out
    assert "不要用 code_execute" in out
    assert "请用户" not in out


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
        (GlobTool(), {"pattern": "*.py"}, "list_tree"),
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

        async def list_tree(self, *a, **k):  # noqa: ANN002, ANN003
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
        (GlobTool(), {"pattern": "*.py"}, "list_tree"),
        (FileWriteTool(), {"path": "a.txt", "content": "x"}, "write"),
        (MkdirTool(), {"path": "nested/d"}, "mkdir"),
        (GrepTool(), {"pattern": "x"}, "grep"),
    ],
)
async def test_filesystem_tools_channel_dead_stamps_retire(
    tmp_path: Path, tool, args, method: str
):
    """B2: list / write / mkdir / grep / glob stamp channel-dead retire meta on sticky-dead."""

    class _HangBackend(ServerWorkspace):
        async def list(self, *a, **k):  # noqa: ANN002, ANN003
            raise WorkspaceIOError(
                f"local workspace op '{method}' timed out; channel dead（活性挂起）"
            )

        async def list_tree(self, *a, **k):  # noqa: ANN002, ANN003
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
