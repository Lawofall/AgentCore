"""PDF page-window contract (orthogonal to ``read`` offset/limit line numbers)."""

from __future__ import annotations

from agentcore.workspace.limits import OFFICE_EXTRACT_PDF_MAX_PAGES


class PdfPagesError(ValueError):
    """Invalid ``pages`` argument. ``code`` is ``pages`` or ``pages_span``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def parse_pdf_pages(raw: object) -> tuple[int, int] | None:
    """Return 1-based inclusive ``(start, end)``, or ``None`` if omitted.

    Accepts ``\"1-40\"``, ``\"12\"``, or a JSON integer. Span may not exceed
    ``OFFICE_EXTRACT_PDF_MAX_PAGES``.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise PdfPagesError("pages")
    if isinstance(raw, int):
        start = end = raw
    elif isinstance(raw, float) and raw.is_integer():
        start = end = int(raw)
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if "-" in text:
            left, right = text.split("-", 1)
            try:
                start, end = int(left.strip()), int(right.strip())
            except ValueError as exc:
                raise PdfPagesError("pages") from exc
        else:
            try:
                start = end = int(text)
            except ValueError as exc:
                raise PdfPagesError("pages") from exc
    else:
        raise PdfPagesError("pages")
    if start < 1 or end < start:
        raise PdfPagesError("pages")
    if end - start + 1 > OFFICE_EXTRACT_PDF_MAX_PAGES:
        raise PdfPagesError("pages_span")
    return start, end


def format_pages_example(start: int = 1) -> str:
    end = start + OFFICE_EXTRACT_PDF_MAX_PAGES - 1
    return f'{start}-{end}'
