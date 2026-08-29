"""批量 / 落字节工具的自报产物 → 交付物台账（契约见 ``tools/file_products.py``）。

``file_write`` 那批「笔」早已自报，但一次能产多件的 ``file_batch``、能落上千件的
``archive_extract``、以及把网络字节写进工作区的 ``download_url`` 都还没接上：它们产出的
文件于是全部不进台账——不出现在产物卡、不出现在用户面路径页脚、CEO 也看不见。

这里按事故形状端到端钉死：真跑工具 → 引擎盖章 → ``files_touched`` / ``file_acceptance``。
断言的是**真正落盘的路径**（已过 sanitize，不是模型请求的原始 path），且搬家 / 复制 /
解压一律不填 ``derived_from``（填错会让源文件在用户面被误折叠成中间稿）。
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.runs.file_acceptance import build_file_acceptance
from agentcore.runtime.runs.serialize import (
    file_products_from_transcript,
    files_touched_from_transcript,
)
from agentcore.runtime.runs.types import RunPhase
from agentcore.tools.builtin import archive_extract as archive_mod
from agentcore.tools.builtin.archive_create import ArchiveCreateTool
from agentcore.tools.builtin.archive_extract import ArchiveExtractTool
from agentcore.tools.builtin.file_ops import FileBatchTool
from agentcore.tools.builtin.web import download_url as download_mod
from agentcore.tools.builtin.web.download_url import DownloadUrlTool
from agentcore.tools.file_products import with_file_products_marker
from agentcore.tools.protocol import ToolContext, ToolResult
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from agentcore.workspace.stage_dirs import REVIEWS_PREFIX


def _ctx(workspace: Path) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=workspace, sandbox=SubprocessSandbox()),
        user_id="u",
    )


def _ledger(result: ToolResult) -> list[str]:
    """台账看到的路径：按引擎单点盖章的样子过一遍 transcript（tool_exec_call 同款）。"""
    message = LLMMessage(
        role="tool",
        content=with_file_products_marker(result.output, result.file_products),
        tool_call_id="c1",
    )
    return files_touched_from_transcript([message])


def _write_zip(path: Path, mapping: dict[str, str]) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in mapping.items():
            zf.writestr(name, text)
    path.write_bytes(buf.getvalue())


def _ok_response(body: bytes) -> httpx.Response:
    return httpx.Response(
        200,
        content=body,
        headers={
            "content-type": "application/octet-stream",
            "content-length": str(len(body)),
        },
        request=httpx.Request("GET", "https://example.com/file.bin"),
    )


def _stub_download(monkeypatch: pytest.MonkeyPatch, body: bytes = b"payload") -> None:
    async def _fake_safe_request(client: Any, method: str, url: str, **kwargs: Any):
        return _ok_response(body)

    monkeypatch.setattr(download_mod, "_safe_request", _fake_safe_request)


async def test_file_batch_reports_every_destination_it_landed(tmp_path: Path):
    """一次多件：move / copy 逐件自报落地路径；mkdir / delete 没有产物。"""
    (tmp_path / "src.md").write_text("alpha", encoding="utf-8")
    (tmp_path / "keep.md").write_text("beta", encoding="utf-8")
    (tmp_path / "gone.md").write_text("旧", encoding="utf-8")

    result = await FileBatchTool().execute(
        {
            "operations": [
                {"op": "mkdir", "path": "out"},
                {"op": "move", "source": "src.md", "destination": "out/moved.md"},
                {"op": "copy", "source": "keep.md", "destination": "out/copy.docx"},
                {"op": "delete", "path": "gone.md", "permanent": True},
            ]
        },
        _ctx(tmp_path),
    )

    assert result.success is True
    assert [(p.path, p.kind, p.derived_from) for p in result.file_products] == [
        ("out/moved.md", "md", None),
        ("out/copy.docx", "docx", None),
    ]
    assert _ledger(result) == ["out/moved.md", "out/copy.docx"]
    assert (tmp_path / "out" / "moved.md").is_file()
    assert (tmp_path / "out" / "copy.docx").is_file()


async def test_file_batch_reports_sanitized_destination_not_requested(tmp_path: Path):
    """自报的必须是真正落盘的路径：约定文档区嵌套路径会被压平。"""
    reviews = tmp_path / "AgentCore" / "文档" / "reviews"
    reviews.mkdir(parents=True)
    (reviews / "src.md").write_text("review", encoding="utf-8")

    result = await FileBatchTool().execute(
        {
            "operations": [
                {
                    "op": "move",
                    "source": f"{REVIEWS_PREFIX}src.md",
                    "destination": f"{REVIEWS_PREFIX}a/b.md",
                }
            ]
        },
        _ctx(tmp_path),
    )

    flat = f"{REVIEWS_PREFIX}a_b.md"
    assert result.success is True
    assert _ledger(result) == [flat]
    assert (tmp_path / Path(flat)).is_file()


async def test_file_batch_partial_failure_reports_only_the_successes(tmp_path: Path):
    """部分成功只报真正落地的那些：源不存在的那条不得入账。"""
    (tmp_path / "src.md").write_text("alpha", encoding="utf-8")

    result = await FileBatchTool().execute(
        {
            "operations": [
                {"op": "move", "source": "src.md", "destination": "out/ok.md"},
                {"op": "copy", "source": "missing.md", "destination": "out/nope.md"},
            ]
        },
        _ctx(tmp_path),
    )

    assert result.success is False
    assert result.metadata["ok"] == 1
    assert result.metadata["fail"] == 1
    # 失败的调用不自报，但整批失败不抹掉已落地的那件（漏账才是事故）。
    assert _ledger(result) == ["out/ok.md"]


async def test_file_batch_skipped_conflict_reports_no_product(tmp_path: Path):
    """目标已存在 = 跳过，没有落任何盘，不得入账。"""
    (tmp_path / "src.md").write_text("alpha", encoding="utf-8")
    (tmp_path / "taken.md").write_text("占位", encoding="utf-8")

    result = await FileBatchTool().execute(
        {
            "operations": [
                {"op": "copy", "source": "src.md", "destination": "taken.md"}
            ]
        },
        _ctx(tmp_path),
    )

    assert result.metadata["skip"] == 1
    assert result.file_products == []
    assert _ledger(result) == []


async def test_archive_extract_reports_every_member_it_wrote(tmp_path: Path):
    """解压落盘的每个成员都进台账，且带上归一后的产物类型。"""
    _write_zip(
        tmp_path / "pkg.zip",
        {"docs/a.md": "# hi", "img/logo.png": "fake-png", "run.py": "print(1)"},
    )

    result = await ArchiveExtractTool().execute(
        {"archive": "pkg.zip", "dest": "out"}, _ctx(tmp_path)
    )

    assert result.success is True
    touched = _ledger(result)
    assert touched == ["out/docs/a.md", "out/img/logo.png", "out/run.py"]

    products = file_products_from_transcript(
        [
            LLMMessage(
                role="tool",
                content=with_file_products_marker(result.output, result.file_products),
                tool_call_id="c1",
            )
        ]
    )
    acceptance = build_file_acceptance(
        touched, phase=RunPhase.COMPLETED, products=products
    )
    assert acceptance == [
        {"path": "out/docs/a.md", "kind": "md", "status": "accepted"},
        {"path": "out/img/logo.png", "kind": "image", "status": "accepted"},
        {"path": "out/run.py", "kind": "code", "status": "accepted"},
    ]
    # 解压不是派生：zip 不是中间稿，源不得被折叠。
    assert all(p.derived_from is None for p in products)


async def test_archive_extract_caps_reported_products_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """上千件时台账只登记前 N 条，回执如实说明其余也已落盘（不假装只产了这些）。"""
    monkeypatch.setattr(archive_mod, "_PRODUCT_REPORT_MAX", 2)
    _write_zip(
        tmp_path / "many.zip",
        {f"f{i}.txt": str(i) for i in range(5)},
    )

    result = await ArchiveExtractTool().execute(
        {"archive": "many.zip", "dest": "."}, _ctx(tmp_path)
    )

    assert result.success is True
    assert result.metadata["files_written"] == 5
    assert _ledger(result) == ["f0.txt", "f1.txt"]
    assert "交付物台账只逐条登记前 2 个路径" in result.output
    # 限的只是记账条数，落盘一个都不少。
    assert all((tmp_path / f"f{i}.txt").is_file() for i in range(5))


async def test_archive_extract_partial_write_still_reports_landed_members(
    tmp_path: Path,
):
    """中途写失败：停下前真躺在盘上的成员照记，后面没写的不记。"""
    _write_zip(
        tmp_path / "pkg.zip",
        {"a.txt": "alpha", "b.txt": "beta", "c.txt": "gamma"},
    )
    ctx = _ctx(tmp_path)
    backend = ctx.backend
    real_write = backend.write_bytes
    seen: list[str] = []

    async def _fail_on_third(path: str, data: bytes) -> int:
        seen.append(path)
        if len(seen) > 2:
            from agentcore.workspace.protocol import WorkspaceIOError

            raise WorkspaceIOError("disk full")
        return await real_write(path, data)

    backend.write_bytes = _fail_on_third  # type: ignore[method-assign]

    result = await ArchiveExtractTool().execute(
        {"archive": "pkg.zip", "dest": "out"}, ctx
    )

    assert result.success is False
    assert result.error and "已写出 2 个文件后停下" in result.error
    assert _ledger(result) == ["out/a.txt", "out/b.txt"]


async def test_archive_extract_failed_call_reports_no_product(tmp_path: Path):
    result = await ArchiveExtractTool().execute(
        {"archive": "missing.zip", "dest": "out"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert result.file_products == []


async def test_archive_create_reports_the_zip_it_wrote(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("# hi", encoding="utf-8")

    result = await ArchiveCreateTool().execute(
        {"sources": ["src"], "dest": "pkg.zip"}, _ctx(tmp_path)
    )

    assert result.success is True
    assert _ledger(result) == ["pkg.zip"]
    products = file_products_from_transcript(
        [
            LLMMessage(
                role="tool",
                content=with_file_products_marker(result.output, result.file_products),
                tool_call_id="c1",
            )
        ]
    )
    acceptance = build_file_acceptance(
        _ledger(result), phase=RunPhase.COMPLETED, products=products
    )
    assert acceptance == [
        {"path": "pkg.zip", "kind": "archive", "status": "accepted"},
    ]
    assert all(p.derived_from is None for p in products)
    assert (tmp_path / "pkg.zip").is_file()


async def test_archive_create_failed_call_reports_no_product(tmp_path: Path):
    result = await ArchiveCreateTool().execute(
        {"sources": ["missing"], "dest": "out.zip"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert result.file_products == []
    assert _ledger(result) == []


async def test_download_url_reports_the_path_it_actually_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """下载落盘进台账；报的是 sanitize 之后的真实路径，不是请求里的原始 path。"""
    _stub_download(monkeypatch, b"hello-download")

    result = await DownloadUrlTool().execute(
        {
            "url": "https://example.com/file.bin",
            "path": f"{REVIEWS_PREFIX}a/data.csv",
        },
        _ctx(tmp_path),
    )

    flat = f"{REVIEWS_PREFIX}a_data.csv"
    assert result.success is True
    assert [(p.path, p.kind, p.derived_from) for p in result.file_products] == [
        (flat, "csv", None)
    ]
    assert _ledger(result) == [flat]
    assert (tmp_path / Path(flat)).read_bytes() == b"hello-download"


async def test_download_url_failed_call_reports_no_product(tmp_path: Path):
    result = await DownloadUrlTool().execute(
        {"url": "http://127.0.0.1/secret", "path": "out.bin"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert result.file_products == []
    assert _ledger(result) == []
