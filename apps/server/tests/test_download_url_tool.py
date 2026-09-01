"""Unit tests for ``download_url`` — SSRF, path gates, successful write, aliases."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from agentcore.tools.builtin.web import download_url as download_mod
from agentcore.tools.builtin.web.download_url import DownloadUrlTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


class _StubTool:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def schema(self):  # noqa: ANN201 — minimal stub for registry
        from agentcore.core.types import ToolApproval, ToolCategory
        from agentcore.tools.protocol import ToolSchema

        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.RESEARCH,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext):
        raise AssertionError("stub not executed")


def _ctx(workspace: Path, *, write_scope: str = "project") -> ToolContext:
    keep = workspace / "README.md"
    if not keep.exists():
        keep.write_text("desk\n", encoding="utf-8")
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=workspace, sandbox=SubprocessSandbox()),
        user_id="u",
        write_scope=write_scope,
    )


def _ok_response(body: bytes, *, content_type: str = "application/octet-stream") -> httpx.Response:
    return httpx.Response(
        200,
        content=body,
        headers={"content-type": content_type, "content-length": str(len(body))},
        request=httpx.Request("GET", "https://example.com/file.bin"),
    )


async def test_download_url_success_writes_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    payload = b"hello-download"

    async def _fake_safe_request(client, method, url, **kwargs):  # noqa: ANN001
        assert method == "GET"
        assert url == "https://example.com/file.bin"
        return _ok_response(payload, content_type="text/plain")

    monkeypatch.setattr(download_mod, "_safe_request", _fake_safe_request)

    result = await DownloadUrlTool().execute(
        {"url": "https://example.com/file.bin", "path": "uploads/file.bin"},
        _ctx(tmp_path),
    )
    assert result.success is True
    assert (tmp_path / "uploads" / "file.bin").read_bytes() == payload
    assert result.metadata is not None
    assert result.metadata["bytes_written"] == len(payload)
    assert result.metadata["installer_like"] is False
    assert "已下载" in result.output


async def test_download_url_marks_governance_and_self_reports_product(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """一次成功下载要盖两条章：治理面（landed-files）与交付物台账（自报产物）。

    两者职责不同、不可互相顶替：台账口径是 ``ToolResult.file_products``（引擎盖尾注、
    ``files_touched`` 只读它）；``_mark_landed_files`` 管的是与台账无关的治理——落盘闸、
    Artifact-first path kind、首写者归属、兄弟 verify 缓存
    失效。曾因「废掉与台账重复的口径」把治理一起删掉，这条用例钉住并存形状。
    """

    async def _fake_safe_request(client, method, url, **kwargs):  # noqa: ANN001
        return _ok_response(b"col_a,col_b\n1,2\n", content_type="text/csv")

    monkeypatch.setattr(download_mod, "_safe_request", _fake_safe_request)
    context = _ctx(tmp_path)

    result = await DownloadUrlTool().execute(
        {"url": "https://example.com/data.csv", "path": "uploads/data.csv"},
        context,
    )

    assert result.success is True
    assert [(p.path, p.kind, p.derived_from) for p in result.file_products] == [
        ("uploads/data.csv", "csv", None)
    ]
    assert context.has_landed_files is True
    assert context.landed_artifact_authors["uploads/data.csv"] == "a"
    # 下载来的字节不是成篇正文：按 skeleton 记，同 path 后续 file_append 不该被锁。
    assert context.landed_artifact_kinds["uploads/data.csv"] == "skeleton"


async def test_download_url_rejects_private_url_ssrf(tmp_path: Path):
    result = await DownloadUrlTool().execute(
        {"url": "http://127.0.0.1/secret", "path": "out.bin"},
        _ctx(tmp_path),
    )
    assert result.success is False
    assert result.error and ("SSRF" in result.error or "内网" in result.error)
    assert not (tmp_path / "out.bin").exists()


async def test_download_url_rejects_write_scope_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def _should_not_fetch(*_a, **_k):
        raise AssertionError("scope rejection must short-circuit before fetch")

    monkeypatch.setattr(download_mod, "_safe_request", _should_not_fetch)
    result = await DownloadUrlTool().execute(
        {"url": "https://example.com/a.bin", "path": "a.bin"},
        _ctx(tmp_path, write_scope="none"),
    )
    assert result.success is False
    assert result.error and "write_scope=none" in result.error


async def test_download_url_rejects_invalid_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def _should_not_fetch(*_a, **_k):
        raise AssertionError("invalid path must not fetch")

    monkeypatch.setattr(download_mod, "_safe_request", _should_not_fetch)
    result = await DownloadUrlTool().execute(
        {"url": "https://example.com/a.bin", "path": ""},
        _ctx(tmp_path),
    )
    assert result.success is False
    assert result.error and "path" in result.error


async def test_download_url_labels_installer_ext(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def _fake_safe_request(client, method, url, **kwargs):  # noqa: ANN001
        return _ok_response(b"MZ-fake", content_type="application/octet-stream")

    monkeypatch.setattr(download_mod, "_safe_request", _fake_safe_request)
    result = await DownloadUrlTool().execute(
        {"url": "https://example.com/Setup.exe", "path": "vendor/Setup.exe"},
        _ctx(tmp_path),
    )
    assert result.success is True
    assert result.metadata is not None
    assert result.metadata["installer_like"] is True
    assert "未执行" in result.output
    assert (tmp_path / "vendor" / "Setup.exe").is_file()


async def test_download_url_schema_points_off_shell_wget():
    schema = DownloadUrlTool().schema
    assert schema.name == "download_url"
    assert "read_url" in schema.description
    assert "code_execute" not in schema.description
    assert "terminal" not in schema.description
    assert "host(action=shell)" not in schema.description
    assert schema.approval.value == "grantable"


def test_fetch_aliases_point_to_download_url_not_read_url():
    reg = ToolRegistry()
    reg.register(_StubTool("read_url"))
    reg.register(_StubTool("download_url"))
    reg.register(_StubTool("web_search"))

    for alias in ("fetch", "fetch_url", "web_fetch", "wget", "curl"):
        assert reg.suggest_names(alias) == ["download_url"], alias
    # Deep-read aliases stay on read_url.
    assert reg.suggest_names("web_read") == ["read_url"]
    assert reg.suggest_names("browse") == ["read_url"]


async def test_download_url_size_gate_via_content_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(download_mod.settings, "workspace_upload_max_bytes", 64)

    async def _fake_safe_request(client, method, url, **kwargs):  # noqa: ANN001
        req = httpx.Request("GET", url)
        return httpx.Response(
            200,
            content=b"x" * 100,
            headers={"content-type": "application/octet-stream", "content-length": "100"},
            request=req,
        )

    monkeypatch.setattr(download_mod, "_safe_request", _fake_safe_request)
    result = await DownloadUrlTool().execute(
        {"url": "https://example.com/big.bin", "path": "big.bin"},
        _ctx(tmp_path),
    )
    assert result.success is False
    assert result.error and "上限" in result.error
    assert not (tmp_path / "big.bin").exists()
