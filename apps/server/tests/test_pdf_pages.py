"""PDF ``pages`` parse + page raster + coalesce key."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcore.runtime.engine.tool_exec_coalesce import _file_read_round_coalesce_key
from agentcore.workspace.limits import OFFICE_EXTRACT_PDF_MAX_PAGES
from agentcore.workspace.pdf_pages import (
    PdfPagesError,
    parse_pdf_pages,
)
from agentcore.workspace.pdf_raster import render_pdf_page_jpegs


def test_parse_pdf_pages_shapes():
    assert parse_pdf_pages(None) is None
    assert parse_pdf_pages("") is None
    assert parse_pdf_pages("  ") is None
    assert parse_pdf_pages("12") == (12, 12)
    assert parse_pdf_pages(12) == (12, 12)
    assert parse_pdf_pages("1-40") == (1, 40)
    assert parse_pdf_pages("41-80") == (41, 80)


def test_parse_pdf_pages_rejects_span_and_junk():
    with pytest.raises(PdfPagesError) as span:
        parse_pdf_pages("1-80")
    assert span.value.code == "pages_span"
    with pytest.raises(PdfPagesError) as junk:
        parse_pdf_pages("nope")
    assert junk.value.code == "pages"
    with pytest.raises(PdfPagesError):
        parse_pdf_pages("0-1")
    with pytest.raises(PdfPagesError):
        parse_pdf_pages("5-3")


def test_file_read_coalesce_key_includes_pages():
    same = {"file_path": "a.pdf", "pages": "1-40"}
    other = {"file_path": "a.pdf", "pages": "41-80"}
    omitted = {"file_path": "a.pdf"}
    assert _file_read_round_coalesce_key(same) != _file_read_round_coalesce_key(other)
    assert _file_read_round_coalesce_key(same) != _file_read_round_coalesce_key(omitted)
    assert _file_read_round_coalesce_key(same) == _file_read_round_coalesce_key(
        {"file_path": "a.pdf", "pages": "1-40", "offset": 1}
    )


def _one_page_pdf() -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=14)
    pdf.cell(40, 10, "Hello page")
    out = pdf.output()
    return bytes(out) if not isinstance(out, (bytes, bytearray)) else bytes(out)


def test_render_pdf_page_jpegs_from_bytes():
    data = _one_page_pdf()
    jpegs = render_pdf_page_jpegs(data=data, start=1, end=1)
    assert len(jpegs) == 1
    assert jpegs[0][:2] == b"\xff\xd8"


def test_render_pdf_page_jpegs_from_path(tmp_path: Path):
    path = tmp_path / "one.pdf"
    path.write_bytes(_one_page_pdf())
    jpegs = render_pdf_page_jpegs(path=path, start=1, end=1)
    assert len(jpegs) == 1
    assert jpegs[0][:2] == b"\xff\xd8"


def test_office_extract_pdf_max_pages_unchanged():
    assert OFFICE_EXTRACT_PDF_MAX_PAGES == 40
