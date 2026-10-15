"""用例级预置用户规则（DB ``documents`` 行）.

心智对齐 ``workspace_fixture``：``fixtures/<name>/`` + ``EvalCase.documents_fixture`` +
``seed_lint`` 校验。与工作区夹具不同，本夹具写入共享 ``_EVAL_USER_ID`` 的 DB 行，故
harness **每例前后硬清**，避免用例间污染。

目录约定：夹具根下必有 ``documents.json``：

```json
{
  "entries": [
    {
      "layer": "user_rule",
      "name": "用户规则.md",
      "apply_mode": "always",
      "content": "- …"
    },
    {
      "layer": "user_rule",
      "name": "合规附录.md",
      "apply_mode": "on_demand",
      "file": "bodies/合规附录.md"
    }
  ]
}
```

``layer=user_rule`` → 一篇普通 Markdown（``ai_maintained=false``，不挂父夹）。
``content`` 与 ``file``（相对夹具根）二选一。

本模块顶层只依赖 stdlib + ``EvalConfigError``，好让 ``seed_lint`` / ``evals lint``
不拖 DB 实现。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from agentcore.evals.types import EvalConfigError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

ApplyMode = Literal["always", "on_demand", "paths"]
Layer = Literal["user_rule"]

_MANIFEST_NAME = "documents.json"
_LAYERS = frozenset({"user_rule"})
_APPLY_MODES = frozenset({"always", "on_demand", "paths"})


@dataclass(frozen=True)
class DocumentsEntry:
    """一条预置用户规则。"""

    layer: Layer
    content: str
    name: str | None = None  # 文档名（含 .md）
    apply_mode: ApplyMode | None = None


@dataclass(frozen=True)
class DocumentsManifest:
    entries: tuple[DocumentsEntry, ...]


def manifest_path(fixture_root: Path) -> Path:
    return fixture_root / _MANIFEST_NAME


def lint_documents_fixture_dir(cid: str, fixture_root: Path) -> list[str]:
    """静态校验夹具目录 + ``documents.json``（空列表 = 合法）。"""
    if not fixture_root.is_dir():
        return [f"[{cid}] documents_fixture 目录不存在: {fixture_root}"]
    path = manifest_path(fixture_root)
    if not path.is_file():
        return [f"[{cid}] documents_fixture 缺 {_MANIFEST_NAME}: {path}"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [f"[{cid}] {_MANIFEST_NAME} JSON 非法: {e}"]
    return _lint_manifest_raw(cid, raw, fixture_root)


def load_documents_manifest(fixture_root: Path) -> DocumentsManifest:
    """读并解析夹具（调用方已 lint；此处再 raise 兜底）。"""
    path = manifest_path(fixture_root)
    if not path.is_file():
        raise EvalConfigError(f"documents_fixture 缺 {_MANIFEST_NAME}: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    errors = _lint_manifest_raw("<load>", raw, fixture_root)
    if errors:
        raise EvalConfigError("documents_fixture 校验失败:\n  " + "\n  ".join(errors))
    entries_raw = raw.get("entries") or []
    out: list[DocumentsEntry] = []
    for item in entries_raw:
        content = _resolve_content(item, fixture_root)
        out.append(
            DocumentsEntry(
                layer=item["layer"],
                content=content,
                name=item.get("name"),
                apply_mode=item.get("apply_mode"),
            )
        )
    return DocumentsManifest(entries=tuple(out))


def _lint_manifest_raw(cid: str, raw: Any, fixture_root: Path) -> list[str]:
    errors: list[str] = []
    if not isinstance(raw, dict):
        return [f"[{cid}] {_MANIFEST_NAME} 须为对象"]
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        return [f"[{cid}] {_MANIFEST_NAME}.entries 须为非空列表"]
    for i, item in enumerate(entries):
        prefix = f"[{cid}] entries[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} 须为对象")
            continue
        layer = item.get("layer")
        if layer not in _LAYERS:
            errors.append(f"{prefix}.layer={layer!r} 非法（须属 {sorted(_LAYERS)}）")
            continue
        has_content = "content" in item
        has_file = "file" in item
        if has_content == has_file:
            errors.append(f"{prefix} 须恰好提供 content 或 file 之一")
        elif has_file:
            rel = item.get("file")
            if not isinstance(rel, str) or not rel.strip():
                errors.append(f"{prefix}.file 须为非空字符串")
            else:
                body = (fixture_root / rel).resolve()
                try:
                    body.relative_to(fixture_root.resolve())
                except ValueError:
                    errors.append(f"{prefix}.file 越出夹具根: {rel!r}")
                else:
                    if not body.is_file():
                        errors.append(f"{prefix}.file 不存在: {rel}")
        elif not isinstance(item.get("content"), str):
            errors.append(f"{prefix}.content 须为字符串")

        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{prefix} user_rule 缺 name")
        mode = item.get("apply_mode", "always")
        if mode not in _APPLY_MODES:
            errors.append(
                f"{prefix}.apply_mode={mode!r} 非法（须属 {sorted(_APPLY_MODES)}）"
            )
        if item.get("path") is not None:
            errors.append(f"{prefix} user_rule 勿用 path（用 name）")
    return errors


def _resolve_content(item: dict[str, Any], fixture_root: Path) -> str:
    if "content" in item:
        return str(item["content"])
    rel = str(item["file"])
    return (fixture_root / rel).read_text(encoding="utf-8")


@asynccontextmanager
async def _session_scope(
    session: AsyncSession | None,
) -> AsyncIterator[AsyncSession]:
    if session is not None:
        yield session
        return
    from agentcore.db.base import async_session_factory

    async with async_session_factory() as owned:
        yield owned


async def purge_user_documents(
    user_id: str, *, session: AsyncSession | None = None
) -> int:
    """硬删该用户全部 ``documents`` 行（含软删残骸），返回删除行数。

    eval 固定 ``_EVAL_USER_ID`` 共享，软删不够——残留行仍会占名 / 干扰 ensure_*。
    """
    from sqlalchemy import delete
    from sqlalchemy.engine import CursorResult

    from agentcore.db.models import Document

    async with _session_scope(session) as sess:
        result = await sess.execute(delete(Document).where(Document.user_id == user_id))
        await sess.commit()
        # DML always yields a CursorResult; ``execute`` is only typed as ``Result``.
        return int(cast("CursorResult[Any]", result).rowcount or 0)


async def apply_documents_fixture(
    fixture_root: Path,
    user_id: str,
    *,
    session: AsyncSession | None = None,
) -> int:
    """把夹具条目写入 ``user_id`` 的 documents 树；返回写入条数。

    调用方须先 :func:`purge_user_documents`（harness 每例开头已做）。
    """
    from agentcore.db.repositories import DocumentRepository

    manifest = load_documents_manifest(fixture_root)
    async with _session_scope(session) as sess:
        repo = DocumentRepository(sess)
        n = 0
        for entry in manifest.entries:
            assert entry.name is not None
            mode: ApplyMode = entry.apply_mode or "always"
            await repo.create(
                user_id,
                name=entry.name,
                parent_id=None,
                role="rule",
                ai_maintained=False,
                apply_mode=mode,
                content=entry.content,
            )
            n += 1
        return n
