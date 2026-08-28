"""Persist user attachments into the conversation's workspace.

Two shapes:

* **Citation** — file already in the current workspace (project path). Client
  sends a workspace-relative ``workspace_path``; we verify it exists and do
  **not** copy or write a sibling ``*.md``.
* **Transfer** — file from outside the home. Bytes land under ``attachments/``
  (client-pre-resident copy, or we write from extracted text).

Absolute paths never appear here. Traversal / empty / drive-letter claims are
dropped before the exists check.

Text-like binaries (docx/pdf/pptx/txt …) are **pre-parsed** after residency
(``attachment_parse``): a readable ``*.md`` copy is written beside the original
when extraction succeeds. Spreadsheets (xlsx/csv/tsv) get a **structure preview**
only (columns / row count / types / sample rows) — the full table stays on disk
and is never inlined. Parse failures never break the turn — path-hint fallback
remains.

Directory attachments carry only a listing (no file bodies), so nothing is
written for them. Conversation references likewise pass through untouched.

This is intentionally a thin service over ``WorkspaceBackend`` (it never touches
``Path``), so the same residency works for ``LocalWorkspace``.
"""

from __future__ import annotations

import os

from agentcore.core.logging import get_logger
from agentcore.workspace.attachment_parse import (
    ParseStatus,
    TablePreviewResult,
    extension_of,
    extract_table_preview,
    preparse_resident,
    preview_table_resident,
    should_preparse,
    should_preview_table,
)
from agentcore.workspace.protocol import WorkspaceBackend, WorkspaceError

logger = get_logger(__name__)

# Subdirectory that holds resident user attachments inside every workspace.
ATTACHMENTS_DIR = "attachments"


async def _workspace_file_present(backend: WorkspaceBackend, rel: str) -> bool:
    """True when ``rel`` names an existing file (stat/exists — no content load).

    Must not use AI-noise-filtered ``list``: local ``opList`` hides ``.zip``/media,
    which would false-negative residency. Large binaries must not go through
    ``read_bytes`` (capacity gate + memory). Outside / I/O → False.
    """
    cleaned = (rel or "").replace("\\", "/").strip("/")
    if not cleaned or cleaned.endswith("/"):
        return False
    try:
        return await backend.exists(cleaned)
    except WorkspaceError:
        return False


def _safe_attachment_name(name: str) -> str:
    """Reduce a user-supplied name to a single safe filename component.

    Strips any directory parts (``/`` and ``\\``) so an attachment can only land
    directly inside ``attachments/`` — the backend's traversal guard is the hard
    boundary, this just keeps names tidy and predictable. Falls back to a generic
    name when nothing usable remains.
    """
    base = os.path.basename((name or "").replace("\\", "/").strip())
    base = base.strip().strip(".")
    return base or "attachment"


def _dedup(name: str, used: set[str]) -> str:
    """Disambiguate ``name`` against ``used`` by inserting ``" (n)"`` before ext.

    Two attachments in one turn can share a filename; without this the second
    ``write`` would clobber the first. Across turns the same name maps back to
    the same path on purpose (the latest copy wins — history lives in snapshots).
    """
    if name not in used:
        used.add(name)
        return name
    root, ext = os.path.splitext(name)
    i = 2
    candidate = f"{root} ({i}){ext}"
    while candidate in used:
        i += 1
        candidate = f"{root} ({i}){ext}"
    used.add(candidate)
    return candidate


def _normalize_client_workspace_path(raw: str | None) -> str | None:
    """Accept a workspace-relative POSIX file path; reject traversal / abs / empty.

    Citations may be nested (``docs/guide.md``). Copies still live at
    ``attachments/<name>``; that single-segment form remains valid here.
    Existence is checked by the caller — this only sanitizes the claim.
    """
    if not raw or not isinstance(raw, str):
        return None
    cleaned = raw.replace("\\", "/").strip().lstrip("/")
    if not cleaned or cleaned in (".", ".."):
        return None
    if len(cleaned) >= 2 and cleaned[1] == ":":
        return None
    parts = [p for p in cleaned.split("/") if p and p != "."]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def _is_copied_attachment_rel(rel: str) -> bool:
    """True when ``rel`` is a transfer copy ``attachments/<single file>``."""
    prefix = f"{ATTACHMENTS_DIR}/"
    if not rel.startswith(prefix):
        return False
    rest = rel[len(prefix) :]
    return bool(rest) and "/" not in rest and ".." not in rest


def _apply_table_preview(item: dict, result: TablePreviewResult) -> None:
    """Attach a structure preview and drop any inline table body from the item."""
    item["parse_status"] = result.status.value
    if result.preview is not None:
        item["table_preview"] = result.preview.to_dict()
    # Data stays on disk. Prompt assembly must not see the raw table body.
    item["text"] = ""


async def _enrich_with_preparse(
    backend: WorkspaceBackend, item: dict, *, workspace_path: str
) -> None:
    """Attempt分流预解析 for a binary resident; mutate ``item`` in place on success."""
    if should_preview_table(item.get("name"), workspace_path):
        preview = await preview_table_resident(
            backend, workspace_path=workspace_path, name=item.get("name")
        )
        _apply_table_preview(item, preview)
        return

    if not should_preparse(item.get("name"), workspace_path):
        item["parse_status"] = ParseStatus.SKIPPED.value
        return

    parsed = await preparse_resident(
        backend, workspace_path=workspace_path, name=item.get("name")
    )
    item["parse_status"] = parsed.status.value
    if parsed.parsed_workspace_path:
        item["parsed_workspace_path"] = parsed.parsed_workspace_path
    if parsed.status == ParseStatus.OK and parsed.text:
        item["text"] = parsed.text
    elif parsed.status == ParseStatus.SCANNED and parsed.text:
        # Surface the scan notice as the attachment body so prompt assembly sees it.
        item["text"] = parsed.text
    # FAILED / SKIPPED: leave text empty → prompt falls back to binary path hint.


async def persist_attachments(
    backend: WorkspaceBackend, attachments: list[dict] | None
) -> list[dict]:
    """Write file attachments into the workspace; return them enriched in order.

    Each returned dict is the input dict plus a ``workspace_path`` key for every
    file actually written or client-claimed **and verified on disk** (citation
    of an in-workspace path, or a copy under ``attachments/<name>``).
    Client-claimed paths that fail the residency check clear ``workspace_path``,
    set ``resident_missing`` + ``claimed_workspace_path``, and log
    ``attachment.resident_missing`` so prompt assembly stays honest.
    Office pre-parse (sibling ``*.md``) runs only for transfer copies under
    ``attachments/`` — citations must not mutate the user's tree.
    Only ``kind="file"`` is persisted; directory listings, conversation references
    and empty-text non-binary files are passed through untouched. A per-file write
    failure is logged and skipped — a bad attachment must never break the turn.

    Binary residents in the text-document bucket may also gain ``text``,
    ``parsed_workspace_path``, and ``parse_status`` from pre-parse. Spreadsheet
    / delimited residents gain ``table_preview`` (structure only) and have
    ``text`` cleared so the full table never rides into the prompt.
    """
    if not attachments:
        return []

    used: set[str] = set()
    enriched: list[dict] = []
    for att in attachments:
        item = dict(att)
        kind = att.get("kind") or "file"
        text = att.get("text") or ""
        binary = bool(att.get("binary"))
        pre = _normalize_client_workspace_path(att.get("workspace_path"))

        if kind == "file" and pre:
            # 桌面声称路径已在工作区（区内引用或 attachments/ 副本）；写进提示前先验盘。
            if not await _workspace_file_present(backend, pre):
                logger.warning(
                    "attachment.resident_missing",
                    name=att.get("name"),
                    workspace_path=pre,
                )
                item.pop("workspace_path", None)
                item["resident_missing"] = True
                item["claimed_workspace_path"] = pre
                item["binary"] = binary
            else:
                used.add(os.path.basename(pre))
                item["workspace_path"] = pre
                item["binary"] = binary
                item.pop("resident_missing", None)
                item.pop("claimed_workspace_path", None)
                if _is_copied_attachment_rel(pre) and (
                    binary or should_preview_table(item.get("name"), pre)
                ):
                    await _enrich_with_preparse(backend, item, workspace_path=pre)
        elif kind == "file" and text.strip() and not binary:
            item.pop("workspace_path", None)
            rel = f"{ATTACHMENTS_DIR}/{_dedup(_safe_attachment_name(att.get('name') or ''), used)}"
            try:
                await backend.write(rel, text)
                item["workspace_path"] = rel
                if should_preview_table(item.get("name"), rel):
                    ext = extension_of(item.get("name"), rel)
                    _apply_table_preview(
                        item, extract_table_preview(text.encode("utf-8"), ext)
                    )
            except WorkspaceError as e:
                logger.warning(
                    "attachment.persist_failed",
                    name=att.get("name"),
                    error=str(e),
                )
        else:
            # 无效 / 缺失的 client workspace_path 不得透传到 enriched 结果。
            if not pre:
                item.pop("workspace_path", None)
            if kind == "file" and binary and not pre:
                logger.warning(
                    "attachment.binary_missing_workspace_path",
                    name=att.get("name"),
                )
        enriched.append(item)
    return enriched


def to_stored_metadata(attachments: list[dict]) -> list[dict]:
    """Project enriched attachments to the columns persisted on the message.

    Drops the one-shot ``text`` (never stored) and keeps display metadata plus
    the durable ``workspace_path`` so the client can render and download it.
    Pre-parse copies live on disk under ``*.md``; they are not persisted as
    message columns (agents find them via workspace path / file tools).
    """
    return [
        {
            "name": a.get("name"),
            "path": a.get("path"),
            "truncated": bool(a.get("truncated")),
            "kind": a.get("kind") or "file",
            "workspace_path": a.get("workspace_path"),
            "conversation_id": a.get("conversation_id"),
            "binary": bool(a.get("binary")),
        }
        for a in attachments
    ]


def interjection_attachment_meta(attachments: list[dict]) -> list[dict]:
    """Project enriched attachments for SSE / coordination briefs (no inline text).

    Carries display name, durable ``workspace_path`` when present, and the binary
    flag so the CEO brief and team-block chips can surface path-only references.
    """
    out: list[dict] = []
    for a in attachments:
        name = a.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        leaf: dict = {"name": name, "binary": bool(a.get("binary"))}
        wp = a.get("workspace_path")
        if isinstance(wp, str) and wp.strip():
            leaf["workspace_path"] = wp
        out.append(leaf)
    return out

