"""Inline HTML assembly + preview screenshot paths for website visual critic."""

from __future__ import annotations

import base64
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from agentcore.core.logging import get_logger
from agentcore.tools.file_products import FileProduct

logger = get_logger(__name__)

_HTML_SOURCE_CANDIDATES = ("site/index.html", "index.html")
_TAG_REF_RE = re.compile(r"<(link|script|img)\b([^>]*)/?>", re.IGNORECASE)
_ATTR_RE = re.compile(r'(\w+)\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE)
_IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "avif"})
_IMAGE_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "svg": "image/svg+xml",
    "bmp": "image/bmp",
    "ico": "image/x-icon",
    "avif": "image/avif",
}

ViewportName = Literal["desktop", "narrow"]

DEFAULT_VIEWPORTS: tuple[tuple[ViewportName, int, int], ...] = (
    ("desktop", 1280, 800),
    ("narrow", 390, 844),
)


class PageScreenshotPort(Protocol):
    """Capture jpeg/png bytes for an assembled HTML document at a viewport size."""

    async def capture(
        self, *, document_html: str, width: int, height: int
    ) -> bytes | None:
        """Return image bytes, or ``None`` when capture is unavailable / failed."""
        ...


@dataclass(frozen=True)
class AssemblePreviewResult:
    """Single-file HTML for headless ``set_content`` preview."""

    document: str
    ok: bool
    reason: str = ""


def resolve_html_source_path(artifact_contents: dict[str, str]) -> str:
    """Pick the HTML entry path used for preview capture / ``derived_from``."""
    for candidate in _HTML_SOURCE_CANDIDATES:
        if candidate in artifact_contents and (artifact_contents[candidate] or "").strip():
            return candidate
    for key, text in artifact_contents.items():
        norm = _norm_workspace_path(key)
        if norm.endswith(".html") and (text or "").strip():
            return key
    return _HTML_SOURCE_CANDIDATES[0]


def other_html_source_paths(
    artifact_contents: dict[str, str], primary: str
) -> list[str]:
    """HTML candidates besides the primary critic entry (compare previews)."""
    primary_norm = _norm_workspace_path(primary)
    out: list[str] = []
    seen: set[str] = {primary_norm}
    for key, text in artifact_contents.items():
        if not (text or "").strip():
            continue
        norm = _norm_workspace_path(key)
        if not norm.endswith((".html", ".htm")) or norm in seen:
            continue
        seen.add(norm)
        out.append(key)
    return out


def preview_shot_path(viewport: str, *, source_path: str = "") -> str:
    """Workspace path for a critic preview frame (``kind=image`` ledger row).

    The primary site shell keeps ``site/preview-{viewport}.jpg``. Other HTML
    candidates sit next to their source as ``{stem}.preview-{viewport}.jpg``.
    """
    norm = _norm_workspace_path(source_path)
    if not norm or norm in _HTML_SOURCE_CANDIDATES:
        return f"site/preview-{viewport}.jpg"
    from pathlib import PurePosixPath

    posix = PurePosixPath(norm)
    parent = posix.parent.as_posix()
    prefix = "" if parent in ("", ".") else f"{parent}/"
    return f"{prefix}{posix.stem}.preview-{viewport}.jpg"


def _norm_workspace_path(path: str) -> str:
    return (path or "").strip().replace("\\", "/").lstrip("./")


def _parse_tag_attrs(attr_text: str) -> dict[str, str]:
    return {m.group(1).lower(): m.group(2) for m in _ATTR_RE.finditer(attr_text)}


def _resolve_ref_path(ref: str, source_path: str) -> str | None:
    """Resolve a relative ref to a workspace path; ``None`` = leave tag unchanged."""
    ref = (ref or "").strip()
    if not ref or ref.startswith(("#", "data:", "blob:", "javascript:", "mailto:")):
        return None
    lower = ref.lower()
    if lower.startswith(("http://", "https://", "//")):
        return None
    from pathlib import PurePosixPath

    base = PurePosixPath(_norm_workspace_path(source_path)).parent
    joined = (base / ref.replace("\\", "/")).as_posix()
    out: list[str] = []
    for part in joined.split("/"):
        if part == "..":
            if out:
                out.pop()
        elif part and part != ".":
            out.append(part)
    return "/".join(out) if out else ""


def _lookup_text(path: str, contents: dict[str, str]) -> str | None:
    target = _norm_workspace_path(path)
    for key, val in contents.items():
        if _norm_workspace_path(key) == target:
            return val
    return None


def _lookup_bytes(path: str, bytes_map: dict[str, bytes]) -> bytes | None:
    target = _norm_workspace_path(path)
    for key, val in bytes_map.items():
        if _norm_workspace_path(key) == target:
            return val
    return None


def _image_data_url(
    path: str,
    contents: dict[str, str],
    bytes_map: dict[str, bytes],
) -> str | None:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    mime = _IMAGE_MIME.get(ext, "application/octet-stream")
    raw = _lookup_bytes(path, bytes_map)
    if raw is None and ext == "svg":
        text = _lookup_text(path, contents)
        if text is not None:
            raw = text.encode("utf-8")
            mime = "image/svg+xml"
    if raw is None:
        return None
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _inject_head_block(doc: str, block: str) -> str:
    lower = doc.lower()
    head_close = lower.rfind("</head>")
    if head_close >= 0:
        return doc[:head_close] + block + "\n" + doc[head_close:]
    body = lower.find("<body")
    if body >= 0:
        return doc[:body] + block + "\n" + doc[body:]
    return block + "\n" + doc


def _inline_html_refs(
    doc: str,
    source_path: str,
    contents: dict[str, str],
    bytes_map: dict[str, bytes],
) -> tuple[str, bool, str]:
    """Inline local ``link`` / ``script`` / ``img`` refs; fail on missing locals."""
    failures: list[str] = []
    out = doc
    for match in reversed(list(_TAG_REF_RE.finditer(doc))):
        tag = match.group(1).lower()
        attrs = _parse_tag_attrs(match.group(2))
        if tag == "link":
            rel = attrs.get("rel", "").lower()
            href = attrs.get("href", "")
            if "stylesheet" not in rel or not href:
                continue
            resolved = _resolve_ref_path(href, source_path)
            if resolved is None:
                continue
            text = _lookup_text(resolved, contents)
            if text is None:
                failures.append(href)
                continue
            replacement = f"<style>\n{text}\n</style>"
        elif tag == "script":
            src = attrs.get("src", "").strip()
            if not src:
                continue
            resolved = _resolve_ref_path(src, source_path)
            if resolved is None:
                continue
            text = _lookup_text(resolved, contents)
            if text is None:
                failures.append(src)
                continue
            replacement = f"<script>\n{text}\n</script>"
        elif tag == "img":
            src = attrs.get("src", "").strip()
            if not src:
                continue
            resolved = _resolve_ref_path(src, source_path)
            if resolved is None:
                continue
            data_url = _image_data_url(resolved, contents, bytes_map)
            if data_url is None:
                failures.append(src)
                continue
            new_attrs = {**attrs, "src": data_url}
            attr_str = " ".join(f'{k}="{v}"' for k, v in new_attrs.items())
            replacement = f"<img {attr_str}>"
        else:
            continue
        out = out[: match.start()] + replacement + out[match.end() :]
    if failures:
        return doc, False, "未能内联：" + "、".join(failures[:5])
    return out, True, ""


def assemble_preview_document(
    html: str,
    css: str = "",
    js: str = "",
    *,
    artifact_contents: dict[str, str] | None = None,
    artifact_bytes: dict[str, bytes] | None = None,
    source_path: str = "",
) -> AssemblePreviewResult:
    """Build a single-file HTML document for headless preview.

    When ``artifact_contents`` is set, inline **local** refs the HTML actually
    uses (stylesheet / script / image). Any missing local asset ⇒ ``ok=False``
    (caller must not screenshot). Legacy ``css`` / ``js`` args still inject when
    no artifact map is supplied (unit tests / callers without a workspace index).
    """
    doc = (html or "").strip() or "<!doctype html><html><body></body></html>"
    contents = artifact_contents or {}
    if contents:
        src_path = (source_path or resolve_html_source_path(contents)).replace("\\", "/")
        inlined, ok, reason = _inline_html_refs(
            doc, src_path, contents, artifact_bytes or {}
        )
        if not ok:
            return AssemblePreviewResult(document=doc, ok=False, reason=reason)
        doc = inlined
        return AssemblePreviewResult(document=doc, ok=True)

    injections: list[str] = []
    if css.strip():
        injections.append(f"<style>\n{css.strip()}\n</style>")
    if js.strip():
        injections.append(f"<script>\n{js.strip()}\n</script>")
    if injections:
        doc = _inject_head_block(doc, "\n".join(injections))
    return AssemblePreviewResult(document=doc, ok=True)


async def load_preview_asset_bytes(
    html: str,
    source_path: str,
    artifact_contents: dict[str, str],
    read_bytes: Callable[[str], Awaitable[bytes]] | None,
) -> dict[str, bytes]:
    """Pre-load binary locals referenced by ``html`` for image inlining."""
    if read_bytes is None:
        return {}
    out: dict[str, bytes] = {}
    for match in _TAG_REF_RE.finditer(html):
        tag = match.group(1).lower()
        if tag != "img":
            continue
        src = _parse_tag_attrs(match.group(2)).get("src", "").strip()
        if not src:
            continue
        resolved = _resolve_ref_path(src, source_path)
        if resolved is None:
            continue
        ext = resolved.rsplit(".", 1)[-1].lower() if "." in resolved else ""
        if ext not in _IMAGE_EXTS or ext == "svg":
            continue
        if _lookup_text(resolved, artifact_contents) is not None:
            continue
        if _norm_workspace_path(resolved) in {_norm_workspace_path(k) for k in out}:
            continue
        try:
            out[resolved] = await read_bytes(resolved)
        except Exception:  # noqa: BLE001
            logger.debug("website.preview_asset_read_failed", path=resolved)
    return out


async def capture_html_preview_products(
    *,
    screenshot: PageScreenshotPort | None,
    persist_preview_shot: Callable[[str, bytes], Awaitable[None]] | None,
    read_bytes: Callable[[str], Awaitable[bytes]] | None,
    artifact_contents: dict[str, str],
    source_path: str,
) -> list[FileProduct]:
    """Screenshot one assembled HTML without running the VLM critic."""
    if screenshot is None or persist_preview_shot is None:
        return []
    html = artifact_contents.get(source_path, "")
    if not (html or "").strip():
        return []
    asset_bytes = await load_preview_asset_bytes(
        html, source_path, artifact_contents, read_bytes
    )
    assembled = assemble_preview_document(
        html,
        artifact_contents=artifact_contents,
        artifact_bytes=asset_bytes,
        source_path=source_path,
    )
    if not assembled.ok:
        return []
    products: list[FileProduct] = []
    for name, width, height in DEFAULT_VIEWPORTS:
        try:
            frame = await screenshot.capture(
                document_html=assembled.document, width=width, height=height
            )
        except Exception:  # noqa: BLE001
            continue
        if not frame:
            continue
        preview_path = preview_shot_path(name, source_path=source_path)
        try:
            await persist_preview_shot(preview_path, frame)
        except Exception:  # noqa: BLE001
            logger.warning(
                "website.visual_critic_preview_write_failed",
                path=preview_path,
                viewport=name,
                exc_info=True,
            )
            continue
        products.append(
            FileProduct(path=preview_path, kind="image", derived_from=source_path)
        )
    return products
