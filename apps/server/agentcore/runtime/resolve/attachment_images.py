"""Resident image attachments: native multimodal parts or VisionReader eye→text."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any, Literal, cast

from agentcore.core.logging import get_logger
from agentcore.workspace.attachment_parse import extension_of

if TYPE_CHECKING:
    from agentcore.runtime.costing import RunCost
    from agentcore.vision.protocol import VisionReader
    from agentcore.workspace.protocol import WorkspaceBackend

logger = get_logger(__name__)

_CredentialSource = Literal["user", "platform", "vendor"]

# Raster / camera image set for eye→text (desktop inline bitmaps + HEIC/HEIF).
_IMAGE_EXTENSIONS = frozenset({
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".avif",
    ".heic",
    ".heif",
})
_IMAGE_MIMES = frozenset({
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/avif",
    "image/x-ms-bmp",
    "image/heic",
    "image/heif",
})
# Non-raster image/* that must not take the eye→text path (e.g. vector markup).
_IMAGE_MIME_EXCLUDE = frozenset({
    "image/svg+xml",
})
_EXT_TO_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
    ".heic": "image/heic",
    ".heif": "image/heif",
}

# Visible-facts prompt for conversation image attachments (eye→text; main LLM stays text).
_ATTACHMENT_VISION_PROMPT = (
    "用中文简要列出这张图片中可见的事实：文字、物体、人物、布局与颜色等。"
    "只写图上能看见的内容，不要臆测图外信息或作者意图。"
)

_IMAGE_VISION_UNCONFIGURED = (
    "当前主模型不收图且未配置识图兜底："
    "工作区路径仍可用，但本回合未注入可见事实。"
    "勿把工作区路径当作已读图；勿默认建议用 run 打开图片；"
    "勿索要重发，应直说限制。"
)

_IMAGE_NATIVE_INDEX = (
    "此图已随当前用户消息以多模态附件发送给主模型；"
    "勿再要求 run 开图，也勿假定未看见像素。"
)


def _attachment_mime(att: dict) -> str:
    raw = att.get("mime") or att.get("content_type") or att.get("media_type") or ""
    return str(raw).split(";", 1)[0].strip().lower()


def _is_image_attachment(att: dict, *, name: str, ws_path: str | None) -> bool:
    """True when extension / MIME should take the vision eye→text path.

    Recognizes known raster/HEIC extensions and MIMEs, plus any ``image/*`` that is
    not explicitly excluded (e.g. ``image/svg+xml``). Non-image MIMEs (xlsx, etc.)
    never match via the ``image/`` prefix alone.
    """
    mime = _attachment_mime(att)
    if mime:
        if mime in _IMAGE_MIME_EXCLUDE:
            return False
        if mime in _IMAGE_MIMES or mime.startswith("image/"):
            return True
    ext = extension_of(name, ws_path if isinstance(ws_path, str) else None)
    return ext in _IMAGE_EXTENSIONS


def _image_data_mime(att: dict, *, name: str, ws_path: str | None) -> str:
    """MIME for data-URL parts — prefer attachment mime, else extension map."""
    mime = _attachment_mime(att)
    if mime.startswith("image/") and mime not in _IMAGE_MIME_EXCLUDE:
        return mime
    ext = extension_of(name, ws_path if isinstance(ws_path, str) else None)
    return _EXT_TO_IMAGE_MIME.get(ext, "image/png")


async def _build_native_image_part(
    *,
    att: dict,
    name: str,
    ws_path: str,
    backend: WorkspaceBackend,
) -> dict | None:
    """Read resident image bytes into an OpenAI ``image_url`` content part."""
    import base64

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


def _vision_credential_source(reader: VisionReader) -> _CredentialSource | None:
    """Pricing origin stamped on the reader (BYOK→user, platform→platform)."""
    src = getattr(reader, "credential_source", None)
    if src in ("user", "platform", "vendor"):
        return cast(_CredentialSource, src)
    return None


def _bill_attachment_vision(
    reading: Any,
    *,
    cost_sink: list[RunCost] | None,
    reader: VisionReader,
    parent_run_id: str | None,
) -> None:
    """Append a ``role=vision`` ledger row; never raise into prepare."""
    if cost_sink is None or not reading.model or reading.usage.total_tokens == 0:
        return
    try:
        from agentcore.runtime.costing import vision_run_cost

        cost_sink.append(
            vision_run_cost(
                reading.model,
                reading.usage,
                parent_run_id=parent_run_id,
                credential_source=_vision_credential_source(reader),
            )
        )
    except Exception:  # noqa: BLE001 — billing must never break a successful read
        logger.warning("attachment.vision_billing_failed", exc_info=True)


async def _read_image_attachment_block(
    *,
    name: str,
    path: str,
    ws_path: str,
    vision_reader: VisionReader | None,
    backend: WorkspaceBackend | None,
    cost_sink: list[RunCost] | None,
    parent_run_id: str | None,
) -> str:
    """Eye→text for a resident image, or an honest unconfigured / failure note."""
    if vision_reader is None or backend is None:
        return f"--- File: {name} ({path}) [image] ---\n{_IMAGE_VISION_UNCONFIGURED}"
    try:
        raw = await backend.read_bytes(ws_path)
    except Exception as exc:  # noqa: BLE001 — prepare must not crash on one attachment
        logger.warning(
            "attachment.vision_read_failed",
            name=name,
            path=ws_path,
            error=str(exc),
            exc_info=True,
        )
        return (
            f"--- File: {name} ({path}) [image / read failed] ---\n"
            f"无法读取工作区图片字节：{exc}。本回合未注入可见事实。"
        )
    if not raw:
        return (
            f"--- File: {name} ({path}) [image / empty] ---\n工作区图片为空，本回合未注入可见事实。"
        )
    b64 = base64.b64encode(raw).decode("ascii")
    try:
        reading = await vision_reader.read(b64, _ATTACHMENT_VISION_PROMPT)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "attachment.vision_read_failed",
            name=name,
            path=ws_path,
            error=str(exc),
            exc_info=True,
        )
        return (
            f"--- File: {name} ({path}) [image / vision failed] ---\n"
            f"识图失败：{exc}。工作区路径仍可用，但本回合未注入可见事实。"
        )
    _bill_attachment_vision(
        reading,
        cost_sink=cost_sink,
        reader=vision_reader,
        parent_run_id=parent_run_id,
    )
    logger.info("attachment.vision_read", name=name, path=ws_path)
    body = (reading.text or "").strip() or "(视觉模型未返回可见事实)"
    return f"--- File: {name} ({path}) [image / vision] ---\n{body}"
