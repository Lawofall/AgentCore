"""This-turn material parts: name, kind, extracted text or path pointer.

File facts are woven into the user message in pill order. HOW lives on tools
and the one-sentence ``<本轮材料>`` scene gate — not in these parts.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentcore.core.logging import get_logger
from agentcore.runtime.resolve.attachment_conversation import (
    _deep_read_conversation_attachment,
)
from agentcore.runtime.resolve.attachment_images import (
    _IMAGE_NATIVE_INDEX,
    _build_native_image_part,
    _image_turn_cap_block,
    _image_unsupported_block,
    _is_image_attachment,
)
from agentcore.workspace.attachment_parse import (
    MARKITDOWN_EXTENSIONS,
    TABLE_EXTENSIONS,
    TablePreview,
    extension_of,
    extract_table_preview,
    format_table_preview,
    table_preview_from_mapping,
    truncate_for_prompt,
)
from agentcore.workspace.file_kind import name_looks_like_archive
from agentcore.workspace.limits import VISION_MAX_ATTACHMENTS_PER_TURN

if TYPE_CHECKING:
    from agentcore.workspace.protocol import WorkspaceBackend

logger = get_logger(__name__)

# @ 点名按需设定：本回合当常驻。单独成段，不是附件。
_PINNED_ENTRIES_FRAME = (
    "用户本条点名了下列按需设定，本回合当作常驻遵守。"
    "不是本条上传的附件，不必落盘。"
)
_PINNED_MISS = "（点名的设定未能加载，本回合不按该条执行。）"


@dataclass(frozen=True)
class AttachmentPrompt:
    """This-turn material parts. No HOW envelope."""

    file_blocks: tuple[str, ...]
    pinned_xml: str | None
    has_file_parts: bool
    has_resident_missing: bool


def _archive_attachment_block(att: dict, *, name: str, path: str) -> str:
    """An attached archive is a folder. Facts only — unpack is platform."""
    status = str(att.get("archive_status") or "")
    dest = att.get("archive_dir")
    if status == "ok" and isinstance(dest, str) and dest:
        members = att.get("archive_members") or []
        count = att.get("archive_member_count")
        if not isinstance(count, int) or count < 0:
            count = len(members) if isinstance(members, list) else 0
        lines = [f"{dest}/{m}" for m in members if isinstance(m, str) and m]
        listing = "\n".join(lines) if lines else "(empty archive)"
        more = ""
        if att.get("archive_listing_truncated"):
            more = f"\n… {count} files total; listing truncated."
        return (
            f"--- File: {name} ({path}) [archive / unpacked → {dest}] ---\n"
            f"{count} files in `{dest}`.\n"
            f"{listing}{more}"
        )
    if status == "password":
        return (
            f"--- File: {name} ({path}) [archive / password] ---\n"
            "Password-protected. Need the password or an unpacked folder."
        )
    return (
        f"--- File: {name} ({path}) [archive / unreadable] ---\n"
        "Could not unpack (too large, encrypted, or no decoder)."
    )


async def _resolve_table_preview(
    att: dict,
    *,
    name: str,
    ws_path: str | None,
    text: str,
    backend: WorkspaceBackend | None,
) -> TablePreview | None:
    """Prefer persist-time preview; fall back to inline text or a workspace read."""
    preview = table_preview_from_mapping(att.get("table_preview"))
    if preview is not None:
        return preview
    ext = extension_of(name, ws_path)
    if text and ext in {".csv", ".tsv"}:
        result = extract_table_preview(text.encode("utf-8"), ext)
        return result.preview
    if backend is not None and ws_path:
        try:
            data = await backend.read_bytes(ws_path)
        except Exception:  # noqa: BLE001 — preview must not break prepare
            logger.warning("attachment.preparse_read_failed", path=ws_path, exc_info=True)
            return None
        result = extract_table_preview(data, ext)
        return result.preview
    return None


def _pinned_entries_envelope(body: str) -> str:
    return f"<钉住条目>\n{_PINNED_ENTRIES_FRAME}\n\n{body}\n</钉住条目>"


async def _load_pinned_document_body(
    *, document_id: str, user_id: str | None
) -> str | None:
    """Owner-scoped load; disputed / unreadable / missing → None (soft miss)."""
    if not user_id:
        return None
    try:
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories import DocumentRepository
        from agentcore.documents.frontmatter import (
            frontmatter_error_message,
            strip_entry_frontmatter,
        )

        async with async_session_factory() as session:
            repo = DocumentRepository(session)
            doc = await repo.get(document_id, user_id=user_id)
            if doc is None or doc.kind != "document":
                return None
            if doc.disputed_at is not None:
                return None
            raw = doc.content or ""
            if frontmatter_error_message(raw):
                return None
            stripped = strip_entry_frontmatter(raw)
            if stripped is None:
                return None
            body = stripped.strip()
            return body or None
    except Exception:  # noqa: BLE001 — pin miss must not break prepare
        logger.warning(
            "attachment.pinned_entry_failed",
            document_id=document_id,
            exc_info=True,
        )
        return None


async def _render_pinned_document(
    att: dict, *, user_id: str | None, name: str
) -> str:
    """Body from the documents tree when ``document_id`` is set; else client ``text`` (tests)."""
    doc_id = str(att.get("document_id") or "").strip()
    raw_text = (att.get("text") or "").strip()
    body: str | None = None
    if doc_id:
        body = await _load_pinned_document_body(document_id=doc_id, user_id=user_id)
    elif raw_text:
        body = raw_text
    if not body:
        return f"--- 设定: {name} ---\n{_PINNED_MISS}"
    clipped_body, clipped = truncate_for_prompt(body)
    note = " (truncated)" if clipped or att.get("truncated") else ""
    return f"--- 设定: {name}{note} ---\n{clipped_body}"


async def _build_attachment_prompt(
    attachments: list[dict] | None,
    *,
    user_id: str | None = None,
    host_conversation_id: str | None = None,
    backend: WorkspaceBackend | None = None,
    main_native_vision: bool = False,
    native_image_parts: list[dict] | None = None,
    available_tools: Collection[str] | None = None,
) -> AttachmentPrompt:
    """Render this-turn files / dirs / conversations into ordered file parts."""
    del available_tools
    empty = AttachmentPrompt((), None, False, False)
    if not attachments:
        return empty

    blocks: list[str] = []
    slots: list[str] = []
    pinned_blocks: list[str] = []

    def add_block(block: str) -> None:
        blocks.append(block)
        slots.append(block)

    has_resident_missing = False
    for att in attachments:
        name = att.get("name") or "untitled"
        kind = att.get("kind") or "file"
        text = (att.get("text") or "").strip()
        binary = bool(att.get("binary"))
        ws_path = att.get("workspace_path")
        parse_status = att.get("parse_status")
        ws_str = ws_path if isinstance(ws_path, str) and ws_path else None
        ext = extension_of(name, ws_str)

        if kind == "file" and att.get("resident_missing"):
            # 验盘失败：元数据有路径、字节未落盘——禁当已交源码 / 禁派解压。
            has_resident_missing = True
            claimed = (
                att.get("claimed_workspace_path")
                or att.get("path")
                or name
            )
            add_block(
                f"--- File: {name} ({claimed}) [resident missing] ---\n"
                "Metadata lists this path, but the bytes are not in the workspace."
            )
            continue

        if kind == "document":
            pinned_blocks.append(
                await _render_pinned_document(att, user_id=user_id, name=name)
            )
            slots.append("")
            continue

        if kind == "dir":
            if not text:
                continue
            path = att.get("path") or name
            note = " (partial listing)" if att.get("truncated") else ""
            add_block(
                f"--- Directory: {name} ({path}){note} ---\n{text}"
            )
        elif kind == "conversation":
            add_block(
                await _deep_read_conversation_attachment(
                    att,
                    name=name,
                    user_id=user_id,
                    host_conversation_id=host_conversation_id,
                )
            )
        elif parse_status == "scanned" and text:
            path = ws_path or att.get("path") or name
            add_block(
                f"--- File: {name} ({path}) [扫描件 / 无文本层] ---\n{text}"
            )
        elif kind == "file" and ext in TABLE_EXTENSIONS:
            path = ws_path or att.get("path") or name
            preview = await _resolve_table_preview(
                att, name=name, ws_path=ws_str, text=text, backend=backend
            )
            if preview is not None:
                structure = format_table_preview(preview)
                add_block(
                    f"--- File: {name} ({path}) [表格 / 列名与样例] ---\n"
                    f"{structure}"
                )
            else:
                add_block(
                    f"--- File: {name} ({path}) [表格 / 仅路径] ---\n"
                    "Could not read column names and sample rows."
                )
        elif parse_status == "ok" and att.get("truncated") and not text:
            path = ws_path or att.get("path") or name
            size = att.get("size_bytes")
            lines = att.get("total_lines")
            bits: list[str] = []
            if isinstance(size, int) and size >= 0:
                bits.append(f"{size} bytes")
            if isinstance(lines, int) and lines >= 0:
                bits.append(f"{lines} lines")
            meta = ", ".join(bits) if bits else "size unknown"
            add_block(
                f"--- File: {name} ({path}) [large text / path pointer] ---\n"
                f"{meta}. Body stays on disk at this path."
            )
        elif parse_status == "ok" and text:
            path = ws_path or att.get("path") or name
            body, clipped = truncate_for_prompt(text)
            client_trunc = bool(att.get("truncated"))
            flag_s = " [truncated]" if clipped or client_trunc else ""
            block = f"--- File: {name} ({path}){flag_s} ---\n{body}"
            if clipped:
                block += f"\n\n… [上下文截到 {len(body)} 字]"
            add_block(block)
        elif att.get("archive_status") or name_looks_like_archive(name):
            path = ws_path or att.get("path") or name
            add_block(_archive_attachment_block(att, name=name, path=path))
        elif binary or (ws_path and not text):
            path = ws_path or att.get("path") or name
            if (
                binary
                and ws_str
                and _is_image_attachment(att, name=name, ws_path=ws_str)
            ):
                if (
                    main_native_vision
                    and native_image_parts is not None
                    and backend is not None
                ):
                    if len(native_image_parts) >= VISION_MAX_ATTACHMENTS_PER_TURN:
                        add_block(_image_turn_cap_block(name=name, path=path))
                        continue
                    part, note = await _build_native_image_part(
                        ws_path=ws_str,
                        backend=backend,
                    )
                    if part is not None:
                        native_image_parts.append(part)
                        add_block(
                            f"--- File: {name} ({path}) [image / multimodal] ---\n"
                            f"{note or _IMAGE_NATIVE_INDEX}"
                        )
                    else:
                        add_block(
                            f"--- File: {name} ({path}) [image / multimodal failed] ---\n"
                            f"{note}"
                        )
                    continue
                add_block(_image_unsupported_block(name=name, path=path))
                continue
            if ext in MARKITDOWN_EXTENSIONS:
                add_block(
                    f"--- File: {name} ({path}) [office-pdf] ---\n"
                    "No inline text."
                )
            else:
                add_block(
                    f"--- File: {name} ({path}) [binary] ---\n"
                    "No inline text."
                )
        elif text:
            path = ws_path or att.get("path") or name
            note = " (truncated)" if att.get("truncated") else ""
            add_block(f"--- File: {name} ({path}){note} ---\n{text}")

    pinned_xml = (
        _pinned_entries_envelope("\n\n".join(pinned_blocks)) if pinned_blocks else None
    )
    if not blocks and not pinned_xml:
        return empty
    return AttachmentPrompt(
        tuple(slots),
        pinned_xml,
        has_file_parts=bool(blocks),
        has_resident_missing=has_resident_missing,
    )


async def _build_attachment_context(
    attachments: list[dict] | None,
    *,
    user_id: str | None = None,
    host_conversation_id: str | None = None,
    backend: WorkspaceBackend | None = None,
    main_native_vision: bool = False,
    native_image_parts: list[dict] | None = None,
    available_tools: Collection[str] | None = None,
) -> str | None:
    """Join this-turn file parts (tests). No HOW envelope."""
    prompt = await _build_attachment_prompt(
        attachments,
        user_id=user_id,
        host_conversation_id=host_conversation_id,
        backend=backend,
        main_native_vision=main_native_vision,
        native_image_parts=native_image_parts,
        available_tools=available_tools,
    )
    parts = [
        p
        for p in (
            prompt.pinned_xml,
            "\n\n".join(b for b in prompt.file_blocks if b),
        )
        if p
    ]
    return "\n\n".join(parts) if parts else None
