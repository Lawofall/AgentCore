"""Attachment prompt block: structure preview + capability-aware steer.

``_build_attachment_context`` walks this-turn attachments and renders the
``<attached_files>`` block. ``code_execute`` copy follows ``available_tools``
(this turn's assembled table) — never invents the tool.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING

from agentcore.core.logging import get_logger
from agentcore.runtime.resolve.attachment_conversation import (
    _deep_read_conversation_attachment,
)
from agentcore.runtime.resolve.attachment_images import (
    _IMAGE_NATIVE_INDEX,
    _build_native_image_part,
    _is_image_attachment,
    _read_image_attachment_block,
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

if TYPE_CHECKING:
    from agentcore.runtime.costing import RunCost
    from agentcore.vision.protocol import VisionReader
    from agentcore.workspace.protocol import WorkspaceBackend

logger = get_logger(__name__)

# 本条 vs 工作区历轮 attachments/：同名跨轮复用同一路径是故意设计，缺判别位会让模型
# 把上一轮同名文件当成本条、或把「无 [resident missing]」误读成「本条没传文件」。
_ATTACH_THIS_MESSAGE_FRAME = (
    "以下是本条消息的附件。attachments/ 同名跨轮复用同一路径，最新上传覆盖旧字节；"
    "工作区索引里其它 attachments/ 条目属历史轮。"
)

# Unconditional: office/PDF extract is never a product-grade table source.
_OFFICE_TABLE_LOSSY = (
    "Office/PDF text extraction is lossy for tabular content and is not a data "
    "grid. Do not use this extract as the data source for a spreadsheet/CSV "
    "product when an execution environment is available this turn."
)


def _tool_table_has(available_tools: Collection[str] | None, name: str) -> bool | None:
    """None when the caller did not supply this turn's assembled tool table."""
    if available_tools is None:
        return None
    return name in available_tools


def _code_execute_steer(*, has_code_execute: bool | None, spreadsheet: bool) -> str:
    """Capability copy that follows the assembled tool table — never invents the tool."""
    sheet = " (e.g. openpyxl / pandas for .xlsx/.csv)" if spreadsheet else ""
    if has_code_execute is True:
        return (
            "This turn's tool table includes code_execute — parse the "
            f"workspace-relative path with it{sheet}. CEO has no code_execute "
            "and must delegate a worker. Do NOT use an OS absolute path."
        )
    if has_code_execute is False:
        return (
            "This turn's tool table does not include code_execute — do not call "
            "or plan code_execute. Complete delivery this turn is a structure report "
            "from the preview facts plus a ready-to-run transform script; do not invent "
            "or hand-copy rows. Do NOT use an OS absolute path."
        )
    return (
        "Full data stays in the workspace file and is not inlined. "
        "Do not invent rows. Do NOT use an OS absolute path."
    )


def _office_lossy_steer(*, has_code_execute: bool | None) -> str:
    extra = ""
    if has_code_execute is True:
        extra = (
            " Parse the original workspace file with code_execute; do not treat "
            "this extract as the dataset."
        )
    elif has_code_execute is False:
        extra = (
            " This turn has no code_execute. Complete delivery is a structure report "
            "of what this extract actually shows plus a ready-to-run transform script "
            "— not a hand-copied spreadsheet."
        )
    return f"{_OFFICE_TABLE_LOSSY}{extra}"


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


async def _build_attachment_context(
    attachments: list[dict] | None,
    *,
    user_id: str | None = None,
    host_conversation_id: str | None = None,
    vision_reader: VisionReader | None = None,
    backend: WorkspaceBackend | None = None,
    cost_sink: list[RunCost] | None = None,
    vision_parent_run_id: str | None = None,
    main_native_vision: bool = False,
    native_image_parts: list[dict] | None = None,
    available_tools: Collection[str] | None = None,
) -> str | None:
    """Render user-referenced files / dirs / conversations into a prompt block.

    Text files carry pre-extracted text; pre-parsed binaries (docx/pdf/…) carry
    inline text (context-capped) plus a pointer to the ``*.md`` workspace copy;
    office/PDF that missed pre-parse steer ``file_read`` (transparent extract);
    spreadsheet / delimited files carry a **structure preview** only (never the
    full table). ``code_execute`` steer follows ``available_tools`` (this turn's
    assembled table); when the table is omitted or lacks the tool, the block
    does not tell the model to call it.
    Resident **image** attachments: when ``main_native_vision`` and
    ``native_image_parts`` is provided, bytes become multimodal ``image_url``
    parts (no VisionReader); otherwise eye→text via ``vision_reader`` when
    wired; without a reader the block states识图未配置 honestly (never silent
    path-only, never「用 code_execute 开图」as primary).
    Directories carry a recursive file listing (paths only);
    ``kind=conversation`` is **server deep-read** via ``log_export``
    (client shallow ``text`` is ignored). A file with a ``workspace_path``
    was persisted into the workspace, so the header points the agent at that
    durable path. Returns None when there is nothing to inject so the base
    prompt stays unchanged.
    """
    if not attachments:
        return None

    blocks: list[str] = []
    resident = False
    has_binary = False
    has_table = False
    has_office_unparsed = False
    has_office_inline = False
    has_preparsed = False
    has_conversation = False
    has_resident_missing = False
    has_image_unconfigured = False
    has_code_execute = _tool_table_has(available_tools, "code_execute")
    for att in attachments:
        name = att.get("name") or "untitled"
        kind = att.get("kind") or "file"
        text = (att.get("text") or "").strip()
        binary = bool(att.get("binary"))
        ws_path = att.get("workspace_path")
        parse_status = att.get("parse_status")
        parsed_path = att.get("parsed_workspace_path")
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
            blocks.append(
                f"--- File: {name} ({claimed}) [resident missing] ---\n"
                "Attachment metadata lists this path, but the bytes are NOT in "
                "the workspace (upload/residency failed or incomplete). "
                "Do NOT treat this as delivered source. Do NOT delegate unzip/"
                "edit against this path. Immediately ask_user to re-upload."
            )
            continue

        if kind == "dir":
            if not text:
                continue
            path = att.get("path") or name
            note = " (partial listing)" if att.get("truncated") else ""
            blocks.append(
                f"--- Directory: {name} ({path}){note} ---\n"
                f"File paths (contents not included):\n{text}"
            )
        elif kind == "conversation":
            has_conversation = True
            blocks.append(
                await _deep_read_conversation_attachment(
                    att,
                    name=name,
                    user_id=user_id,
                    host_conversation_id=host_conversation_id,
                )
            )
        elif parse_status == "scanned" and text:
            path = ws_path or att.get("path") or name
            if ws_path:
                resident = True
            has_preparsed = True
            copy_note = f" [scan note → {parsed_path}]" if parsed_path else ""
            blocks.append(
                f"--- File: {name} ({path}) [scanned / no text layer]{copy_note} ---\n{text}"
            )
        elif kind == "file" and ext in TABLE_EXTENSIONS:
            path = ws_path or att.get("path") or name
            if ws_path:
                resident = True
            has_table = True
            preview = await _resolve_table_preview(
                att, name=name, ws_path=ws_str, text=text, backend=backend
            )
            steer = _code_execute_steer(
                has_code_execute=has_code_execute, spreadsheet=True
            )
            if preview is not None:
                structure = format_table_preview(preview)
                blocks.append(
                    f"--- File: {name} ({path}) [table / structure] ---\n"
                    f"{structure}\n\n"
                    "This is a structure preview only — the full table stays in "
                    f"the workspace file. {steer}"
                )
            else:
                blocks.append(
                    f"--- File: {name} ({path}) [table / path only] ---\n"
                    "Could not build a structure preview. "
                    f"{steer} Do NOT treat file_list emptiness as missing."
                )
        elif parse_status == "ok" and text:
            path = ws_path or att.get("path") or name
            if ws_path:
                resident = True
            has_preparsed = True
            body, clipped = truncate_for_prompt(text)
            client_trunc = bool(att.get("truncated"))
            flags: list[str] = []
            if parsed_path and parsed_path != path:
                flags.append(f"pre-parsed → {parsed_path}")
            if clipped or client_trunc:
                flags.append("truncated")
            flag_s = f" [{'; '.join(flags)}]" if flags else ""
            block = f"--- File: {name} ({path}){flag_s} ---\n{body}"
            if clipped and parsed_path:
                block += (
                    f"\n\n… [truncated at {len(body)} chars for context; "
                    f"full extracted text is at {parsed_path}]"
                )
            elif clipped:
                block += f"\n\n… [truncated at {len(body)} chars for context]"
            if ext in MARKITDOWN_EXTENSIONS:
                has_office_inline = True
                block += f"\n\n{_office_lossy_steer(has_code_execute=has_code_execute)}"
            blocks.append(block)
        elif binary or (ws_path and not text):
            # Binary (or empty-body resident / preparse failed): path only —
            # except resident images → native multimodal or VisionReader eye→text.
            path = ws_path or att.get("path") or name
            if ws_path:
                resident = True
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
                    part = await _build_native_image_part(
                        att=att,
                        name=name,
                        ws_path=ws_str,
                        backend=backend,
                    )
                    if part is not None:
                        native_image_parts.append(part)
                        blocks.append(
                            f"--- File: {name} ({path}) [image / multimodal] ---\n"
                            f"{_IMAGE_NATIVE_INDEX}"
                        )
                    else:
                        blocks.append(
                            f"--- File: {name} ({path}) [image / multimodal failed] ---\n"
                            "无法读取驻留图片字节，本回合未把该图发给主模型。"
                        )
                    continue
                if vision_reader is None or backend is None:
                    has_image_unconfigured = True
                blocks.append(
                    await _read_image_attachment_block(
                        name=name,
                        path=path,
                        ws_path=ws_str,
                        vision_reader=vision_reader,
                        backend=backend,
                        cost_sink=cost_sink,
                        parent_run_id=vision_parent_run_id,
                    )
                )
                continue
            if ext in MARKITDOWN_EXTENSIONS:
                has_office_unparsed = True
                blocks.append(
                    f"--- File: {name} ({path}) [binary / office-pdf] ---\n"
                    "No inline text for this office/PDF attachment (pre-parse missed or "
                    "failed). Use file_read on the workspace-relative path above — "
                    "text is extracted automatically. Do NOT default to code_execute "
                    "for office/PDF. Do NOT use an OS absolute path."
                )
            else:
                has_binary = True
                steer = _code_execute_steer(
                    has_code_execute=has_code_execute, spreadsheet=False
                )
                blocks.append(
                    f"--- File: {name} ({path}) [binary] ---\n"
                    "This is a binary file saved in the workspace (no text inline). "
                    f"{steer} Do NOT treat file_list emptiness as missing."
                )
        elif text:
            path = ws_path or att.get("path") or name
            if ws_path:
                resident = True
            note = " (truncated)" if att.get("truncated") else ""
            blocks.append(f"--- File: {name} ({path}){note} ---\n{text}")

    if not blocks:
        return None

    body = "\n\n".join(blocks)
    resident_note = (
        " Files shown with an in-workspace path have been saved into your "
        "workspace — read or edit them with the file tools by that path rather "
        "than trusting only the (possibly truncated) text below."
        if resident
        else ""
    )
    table_note = (
        " Spreadsheet / delimited attachments include a structure preview only "
        "(columns, row count, inferred types, sample rows). Full data stays in "
        "the workspace file and is not inlined."
        if has_table
        else ""
    )
    if has_binary:
        binary_note = (
            " Unknown binary attachments have no inline body. "
            + _code_execute_steer(has_code_execute=has_code_execute, spreadsheet=False)
        )
    else:
        binary_note = ""
    office_note = (
        " Office/PDF attachments without inline text: use file_read on the "
        "workspace path (automatic text extract); do not default to code_execute."
        if has_office_unparsed
        else ""
    )
    if has_preparsed:
        preparsed_note = (
            " Some office/PDF attachments were pre-parsed at upload: inline text may "
            "be truncated — use the ``*.md`` workspace copy (or the original path) "
            "with file tools for the full extract."
        )
        if has_office_inline:
            preparsed_note += f" {_OFFICE_TABLE_LOSSY}"
    else:
        preparsed_note = ""
    conversation_note = (
        " A Conversation block is a server-rendered deep transcript (messages +"
        " process layer); when truncated, delegate a Worker with"
        " ``read_conversation`` to continue — do not treat a truncated block as"
        " the full log."
        if has_conversation
        else ""
    )
    missing_note = (
        " A [resident missing] block means chip/metadata claimed a workspace "
        "path but bytes are absent — ask_user to re-upload; never dispatch "
        "unzip/remediation as if the file were already delivered."
        if has_resident_missing
        else ""
    )
    image_note = (
        " Image attachments are eye→text when识图 is configured (profile vision "
        "slot or platform VISION_*); without a reader, the block states that "
        "honestly — do not treat a bare path as a reading, and do not default to "
        "code_execute to open images."
        if has_image_unconfigured
        else ""
    )
    return (
        "<attached_files>\n"
        f"{_ATTACH_THIS_MESSAGE_FRAME} "
        "The user attached the following files, directories and past "
        "conversations as actionable inputs for this turn—not mere optional "
        "reference. When the user narrows scope to these materials and/or "
        "existing workspace products, start from them (gap analysis or a "
        "revision); do not idle solely because a full repo is missing. Cite "
        "them by name when relevant. Directory entries list file paths only "
        "(file contents are not included)."
        f"{conversation_note}"
        f"{resident_note}{table_note}{binary_note}{office_note}{preparsed_note}"
        f"{missing_note}{image_note}\n\n"
        f"{body}\n"
        "</attached_files>"
    )
