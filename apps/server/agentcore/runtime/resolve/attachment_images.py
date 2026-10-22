"""Resident image attachments: native multimodal parts, or an honest note."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentcore.core.logging import get_logger
from agentcore.workspace.attachment_parse import extension_of
from agentcore.workspace.image_files import (
    IMAGE_EXTENSIONS,
    IMAGE_MIME_EXCLUDE,
    IMAGE_MIMES,
)
from agentcore.workspace.image_raster import (
    jpeg_native_part,
    raster_fail_note,
)

if TYPE_CHECKING:
    from agentcore.workspace.protocol import WorkspaceBackend

logger = get_logger(__name__)

_IMAGE_UNSUPPORTED = (
    "当前主模型不收图："
    "工作区路径仍可用，但本回合未把像素发给模型。"
    "勿把工作区路径当作已读图。"
)

_IMAGE_NATIVE_INDEX = (
    "此图已随当前用户消息以多模态附件发送给主模型；"
    "勿假定未看见像素。"
)

_IMAGE_TURN_CAP = (
    "已达本回合识图张数顶，本回合未把该图发给主模型。"
    "工作区路径仍可用。"
)


def _attachment_mime(att: dict) -> str:
    raw = att.get("mime") or att.get("content_type") or att.get("media_type") or ""
    return str(raw).split(";", 1)[0].strip().lower()


def _is_image_attachment(att: dict, *, name: str, ws_path: str | None) -> bool:
    """True when extension / MIME should take the pixel-image path.

    Recognizes known raster/HEIC extensions and MIMEs, plus any ``image/*`` that is
    not explicitly excluded (e.g. ``image/svg+xml``). Non-image MIMEs never match
    via the ``image/`` prefix alone.
    """
    mime = _attachment_mime(att)
    if mime:
        if mime in IMAGE_MIME_EXCLUDE:
            return False
        if mime in IMAGE_MIMES or mime.startswith("image/"):
            return True
    ext = extension_of(name, ws_path if isinstance(ws_path, str) else None)
    return ext in IMAGE_EXTENSIONS


async def _build_native_image_part(
    *,
    ws_path: str,
    backend: WorkspaceBackend,
) -> tuple[dict | None, str]:
    """Raster a resident image into an OpenAI ``image_url`` part, or an honest note."""
    try:
        result = await backend.raster_for_vision(ws_path)
    except Exception:  # noqa: BLE001 — native path must not break prepare
        logger.warning("attachment.native_image_read_failed", path=ws_path, exc_info=True)
        return None, "无法读取驻留图片，本回合未把该图发给主模型。"
    if result.status == "ok" and result.jpeg:
        return jpeg_native_part(result.jpeg), _IMAGE_NATIVE_INDEX
    return None, raster_fail_note(result)


def _image_unsupported_block(*, name: str, path: str) -> str:
    return f"--- File: {name} ({path}) [image] ---\n{_IMAGE_UNSUPPORTED}"


def _image_turn_cap_block(*, name: str, path: str) -> str:
    return (
        f"--- File: {name} ({path}) [image / multimodal skipped] ---\n"
        f"{_IMAGE_TURN_CAP}"
    )
