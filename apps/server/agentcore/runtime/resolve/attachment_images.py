"""Resident image attachments: native multimodal parts, or an honest note."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

from agentcore.core.logging import get_logger
from agentcore.workspace.attachment_parse import extension_of
from agentcore.workspace.image_files import (
    EXT_TO_IMAGE_MIME,
    IMAGE_EXTENSIONS,
    IMAGE_MIME_EXCLUDE,
    IMAGE_MIMES,
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


def _image_data_mime(att: dict, *, name: str, ws_path: str | None) -> str:
    """MIME for data-URL parts — prefer attachment mime, else extension map."""
    mime = _attachment_mime(att)
    if mime.startswith("image/") and mime not in IMAGE_MIME_EXCLUDE:
        return mime
    ext = extension_of(name, ws_path if isinstance(ws_path, str) else None)
    return EXT_TO_IMAGE_MIME.get(ext, "image/png")


async def _build_native_image_part(
    *,
    att: dict,
    name: str,
    ws_path: str,
    backend: WorkspaceBackend,
) -> dict | None:
    """Read resident image bytes into an OpenAI ``image_url`` content part."""
    try:
        raw = await backend.read_bytes(ws_path)
    except Exception:  # noqa: BLE001 — native path must not break prepare
        logger.warning("attachment.native_image_read_failed", path=ws_path, exc_info=True)
        return None
    if not raw:
        return None
    b64 = base64.b64encode(raw).decode("ascii")
    mime = _image_data_mime(att, name=name, ws_path=ws_path)
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{b64}"},
    }


def _image_unsupported_block(*, name: str, path: str) -> str:
    return f"--- File: {name} ({path}) [image] ---\n{_IMAGE_UNSUPPORTED}"
