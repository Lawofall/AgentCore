"""file_ops: mount-policy deny must surface the real reason (not 「超出工作区」)."""

import io
import zipfile
from pathlib import Path

import httpx
import pytest

from agentcore.tools.builtin.archive_extract import ArchiveExtractTool
from agentcore.tools.builtin.file_ops import FileReadTool, FileWriteTool
from agentcore.tools.builtin.file_ops import errors as file_ops_errors
from agentcore.tools.builtin.file_ops.errors import (
    _MOUNT_OP_DENIED_MARKERS,
    _is_mount_op_denied_reason,
    _outside_workspace_msg,
)
from agentcore.tools.builtin.grep import GrepTool
from agentcore.tools.builtin.web import download_url as download_mod
from agentcore.tools.builtin.web.download_url import DownloadUrlTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace import external_mounts as em
from agentcore.workspace.external_mounts import ExternalMount, organize_deny_error
from agentcore.workspace.protocol import OutsideWorkspace
from agentcore.workspace.server import ServerWorkspace


def _module_msg_constants(mod: object) -> frozenset[str]:
    return frozenset(
        value
        for name, value in vars(mod).items()
        if name.endswith("_MSG") and isinstance(value, str) and value
    )


def _ctx(workspace: Path) -> ToolContext:
    keep = workspace / "README.md"
    if not keep.exists():
        keep.write_text("desk\n", encoding="utf-8")
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=workspace, sandbox=SubprocessSandbox()),
        user_id="u",
    )


def _ctx_organize(workspace: Path, ext: Path, *, alias: str = "AgentCode") -> ToolContext:
    ctx = _ctx(workspace)
    backend = ctx.backend
    assert isinstance(backend, ServerWorkspace)
    backend.attach_external_mounts(
        {
            alias: ExternalMount(
                alias=alias,
                root_id="ext-r1",
                label=alias,
                abs_path=str(ext),
                mode="organize",
            )
        }
    )
    return ctx


def _assert_organize_policy(err: str) -> None:
    assert "整理授权不允许" in err
    assert "超出了工作区范围" not in err
    assert "AgentCore/文档" not in err


def test_outside_workspace_msg_organize_deny_is_not_out_of_range():
    path = "external/AgentCode/out/report.md"
    reason = organize_deny_error(path, "write")
    msg = _outside_workspace_msg(path, location="server", reason=reason)
    assert "整理授权不允许此操作" in msg
    assert "write" in msg
    assert path in msg
    assert "超出了工作区范围" not in msg
    assert "AgentCore/文档" not in msg


def test_outside_workspace_msg_does_not_stuff_policy_into_path_slot():
    """move/copy used to interpolate str(e) into 「路径 '{path}' 超出了…」."""
    reason = organize_deny_error("external/AgentCode/a.md", "write")
    msg = _outside_workspace_msg(reason, location="local")
    assert msg == reason
    assert "超出了工作区范围" not in msg
    assert not msg.startswith("路径 '")


def test_outside_workspace_msg_true_outside_keeps_range_hint():
    msg = _outside_workspace_msg("../escaped.md", location="server")
    assert "超出了工作区范围" in msg
    assert "AgentCore/文档" in msg


async def test_write_organize_mount_rejects_with_real_reason(tmp_path: Path):
    ws = tmp_path / "ws"
    ext = tmp_path / "AgentCode"
    ws.mkdir()
    ext.mkdir()
    result = await FileWriteTool().execute(
        {"path": "external/AgentCode/out/report.md", "content": "leak"},
        _ctx_organize(ws, ext),
    )
    assert result.success is False
    err = result.error or ""
    _assert_organize_policy(err)
    assert "整理授权不允许此操作" in err
    assert "write" in err
    assert not (ext / "out" / "report.md").exists()
    assert not (ws / "out" / "report.md").exists()


async def test_archive_extract_organize_mount_rejects_with_real_reason(tmp_path: Path):
    ws = tmp_path / "ws"
    ext = tmp_path / "AgentCode"
    ws.mkdir()
    ext.mkdir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.md", "# leak")
    (ws / "pkg.zip").write_bytes(buf.getvalue())
    result = await ArchiveExtractTool().execute(
        {"archive": "pkg.zip", "dest": "external/AgentCode/out"},
        _ctx_organize(ws, ext),
    )
    assert result.success is False
    _assert_organize_policy(result.error or "")
    assert not (ext / "out" / "readme.md").exists()
    assert not (ws / "out" / "readme.md").exists()


async def test_download_url_organize_mount_rejects_with_real_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ws = tmp_path / "ws"
    ext = tmp_path / "AgentCode"
    ws.mkdir()
    ext.mkdir()

    async def _fake_safe_request(client, method, url, **kwargs):  # noqa: ANN001
        return httpx.Response(
            200,
            content=b"leak",
            headers={"content-type": "application/octet-stream", "content-length": "4"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(download_mod, "_safe_request", _fake_safe_request)
    result = await DownloadUrlTool().execute(
        {
            "url": "https://example.com/file.bin",
            "path": "external/AgentCode/out/file.bin",
        },
        _ctx_organize(ws, ext),
    )
    assert result.success is False
    _assert_organize_policy(result.error or "")
    assert not (ext / "out" / "file.bin").exists()
    assert not (ws / "out" / "file.bin").exists()


async def test_file_read_organize_root_surfaces_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Organize allows read; this covers the tool rewrite when backend sends policy."""
    ws = tmp_path / "ws"
    ext = tmp_path / "AgentCode"
    ws.mkdir()
    ext.mkdir()
    ctx = _ctx_organize(ws, ext)
    root = "external/AgentCode"

    async def _deny(path: str, **kwargs):
        raise OutsideWorkspace(organize_deny_error(path, "write"))

    monkeypatch.setattr(ctx.backend, "read_lines", _deny)
    result = await FileReadTool().execute({"path": root}, ctx)
    assert result.success is False
    _assert_organize_policy(result.error or "")


async def test_grep_organize_root_surfaces_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Organize allows grep; this covers the tool rewrite when backend sends policy."""
    ws = tmp_path / "ws"
    ext = tmp_path / "AgentCode"
    ws.mkdir()
    ext.mkdir()
    ctx = _ctx_organize(ws, ext)

    async def _deny(query):
        raise OutsideWorkspace(organize_deny_error(query.directory, "write"))

    monkeypatch.setattr(ctx.backend, "grep", _deny)
    result = await GrepTool().execute({"pattern": "x", "path": "external/AgentCode"}, ctx)
    assert result.success is False
    _assert_organize_policy(result.error or "")


def test_mount_op_denied_markers_are_exactly_the_module_msg_constants():
    """Discriminator is the imported ``*_MSG`` set — prefixes / 「（拒绝」 cannot sneak back."""
    expected = _module_msg_constants(em)
    assert expected, "policy modules exposed no *_MSG constants (parse failure?)"
    assert frozenset(_MOUNT_OP_DENIED_MARKERS) == expected
    assert "（拒绝" not in _MOUNT_OP_DENIED_MARKERS


def test_errors_py_does_not_rescribe_policy_sentences():
    """A wording change in the source modules must not leave a stale literal here."""
    src = Path(file_ops_errors.__file__).read_text(encoding="utf-8")
    for sentence in _module_msg_constants(em):
        assert sentence not in src, f"hand-copied policy sentence in errors.py: {sentence!r}"
    assert '"（拒绝"' not in src and "'（拒绝'" not in src


@pytest.mark.parametrize(
    "reason",
    [
        em._READONLY_MSG,
        em.readonly_write_error("external/desk/a.md"),
        em.organize_deny_error("external/desk/a.md", "write"),
        em.permanent_external_error("external/desk/a.md"),
        em.cross_root_copy_error("a", None),
        em.cross_root_copy_error("a", "b"),
        em.cross_root_move_error("a", None),
        em.cross_root_move_error("a", "b"),
    ],
)
def test_policy_sentence_is_surfaced_as_is(reason: str | None):
    assert isinstance(reason, str) and reason
    msg = _outside_workspace_msg("external/desk/a.md", location="server", reason=reason)
    assert msg == reason
    assert "超出了工作区范围" not in msg
    assert "AgentCore/文档" not in msg


def test_wide_reject_suffix_alone_is_not_a_policy_sentence():
    """The old 「（拒绝」 scrape must not classify an arbitrary detail as mount-policy."""
    assert not _is_mount_op_denied_reason("（拒绝")
    assert not _is_mount_op_denied_reason("foo（拒绝 bar")
    msg = _outside_workspace_msg("rel/a.md", location="local", reason="foo（拒绝 bar")
    assert "超出了工作区范围" in msg


def test_short_prefix_is_not_enough_without_the_real_sentence():
    """Hand-copied prefixes are not the discriminator; the full constant is."""
    assert not _is_mount_op_denied_reason("会话授权目录为只读")
    assert _is_mount_op_denied_reason(em._READONLY_MSG)
