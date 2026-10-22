"""Render a PDF page window to JPEG bytes for native multimodal ``read``."""

from __future__ import annotations

from pathlib import Path

from agentcore.workspace.image_raster import VISION_MAX_EDGE, rgb_jpeg_bytes
from agentcore.workspace.limits import OFFICE_EXTRACT_PDF_MAX_IMAGES

_SCALE = 1.5


def render_pdf_page_jpegs(
    *,
    data: bytes | None = None,
    path: Path | None = None,
    start: int,
    end: int,
    max_images: int | None = None,
) -> list[bytes]:
    """JPEG bytes for pages ``start``–``end`` (1-based), capped per read."""
    cap = max_images if max_images is not None else OFFICE_EXTRACT_PDF_MAX_IMAGES
    last = min(end, start + max(1, cap) - 1)
    source: Path | bytes | None = path if path is not None else data
    if source is None or start < 1 or last < start:
        return []
    try:
        from pypdfium2 import PdfDocument
    except ImportError:
        return []
    try:
        doc = PdfDocument(str(source) if isinstance(source, Path) else source)
    except Exception:
        return []
    out: list[bytes] = []
    try:
        n = len(doc)
        for index in range(start, last + 1):
            if index > n:
                break
            page = doc[index - 1]
            try:
                bitmap = page.render(scale=_SCALE)
                image = bitmap.to_pil()
            finally:
                page.close()
            jpeg = _to_jpeg(image)
            if jpeg:
                out.append(jpeg)
    except Exception:
        return out
    finally:
        doc.close()
    return out


def _to_jpeg(image: object) -> bytes:
    from PIL import Image

    if not isinstance(image, Image.Image):
        return b""
    rgb = image.convert("RGB")
    return rgb_jpeg_bytes(rgb, max_edge=VISION_MAX_EDGE)
