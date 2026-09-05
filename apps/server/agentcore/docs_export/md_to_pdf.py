"""Markdown → PDF deterministic converter (markdown-it-py + fpdf2).

Why fpdf2 (not reportlab / Playwright): pure-Python, ships in both cloud
``dependencies`` and sidecar optional group, no Chromium/HTML engine — same
capability matrix as ``md_to_docx``. CJK via system / Noto TTF·TTC discovery;
missing fonts produce an explicit warning (never silent tofu).

MVP coverage: headings #–####, paragraphs, ordered/unordered lists, tables,
fenced code. Images are rendered as alt-text placeholders with a warning
(embedding is out of scope for this MVP).

段落几何按 ``layout`` 档位走，与 ``md_to_docx`` 同口径（见 ``docs_export.layout``）：
一级标题两档都居中；首行缩进两字只在 ``official`` 档开。PDF 不做公文页边距 /
两端对齐 / 页码——行高 6mm ≈ 11pt × 1.5，与 Word 正文倍数对齐即可。
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token

from agentcore.docs_export.layout import (
    FIRST_LINE_INDENT_CHARS,
    LAYOUT_OFFICIAL,
    LAYOUT_STANDARD,
    DocLayout,
)

# CommonMark + GFM tables. html=False keeps raw HTML out of the tree.
_MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "breaks": False}).enable(
    "table"
)

_HEADING_PT = {1: 20, 2: 16, 3: 14, 4: 12}
_BODY_PT = 11
_CODE_PT = 9
# 6mm ≈ 11pt × 1.5（与 Word LINE_SPACING_MULTIPLE 对齐），不要另做一套视觉。
_LINE = 6.0

# fpdf2 is loaded on first convert (not at import) — same hygiene as python-docx.
_FPDF: Any = None

# Preferred CJK faces (regular). Order = preference.
_CJK_CANDIDATES: tuple[Path, ...] = (
    # Windows
    Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc",
    Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "simhei.ttf",
    Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "simsun.ttc",
    Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyhbd.ttc",
    # Linux Noto / WenQuanYi
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansSC-Regular.otf"),
    Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
    # macOS
    Path("/System/Library/Fonts/PingFang.ttc"),
    Path("/System/Library/Fonts/STHeiti Light.ttc"),
    Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
)


@dataclass(frozen=True)
class MdToPdfResult:
    """Bytes of a .pdf plus non-fatal export warnings (e.g. missing CJK font)."""

    pdf_bytes: bytes
    warnings: list[str] = field(default_factory=list)


@dataclass
class _FontBundle:
    family: str
    path: Path | None
    cjk_ok: bool


def pdf_path_for_markdown(md_path: str) -> str:
    """``报告.md`` → ``报告.pdf`` (same directory)."""
    md_path = md_path.replace("\\", "/").strip()
    lower = md_path.lower()
    if lower.endswith(".markdown"):
        return md_path[: -len(".markdown")] + ".pdf"
    if lower.endswith(".md"):
        return md_path[: -len(".md")] + ".pdf"
    return f"{md_path}.pdf"


def discover_cjk_font(candidates: Sequence[Path] | None = None) -> Path | None:
    """Return the first existing CJK font path, or ``None``."""
    for path in candidates if candidates is not None else _CJK_CANDIDATES:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def convert_markdown_to_pdf(
    markdown: str,
    *,
    layout: DocLayout = LAYOUT_STANDARD,
) -> MdToPdfResult:
    """Convert Markdown text to a .pdf (deterministic; no LLM / code_execute).

    ``layout`` 同 ``md_to_docx``：``standard``（默认）= 技术文档/报告；``official`` =
    中文正式文书，正文首行缩进两字。
    """
    _ensure_fpdf()
    warnings: list[str] = []
    indent_body = layout == LAYOUT_OFFICIAL
    fonts = _resolve_fonts(warnings)

    pdf = _FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(18, 18, 18)
    _register_fonts_on_pdf(pdf, fonts, warnings)
    pdf.add_page()

    tokens = _MD.parse(markdown or "")
    i = 0
    while i < len(tokens):
        token = tokens[i]
        t = token.type

        if t == "heading_open":
            level = int(token.tag[1]) if token.tag and token.tag.startswith("h") else 1
            level = max(1, min(level, 4))
            inline = tokens[i + 1] if i + 1 < len(tokens) else None
            text = _inline_text(inline) if inline is not None and inline.type == "inline" else ""
            _write_heading(pdf, fonts, text, level)
            i += 3
            continue

        if t == "paragraph_open":
            inline = tokens[i + 1] if i + 1 < len(tokens) else None
            text, img_warns = _inline_text_with_images(inline) if inline is not None else ("", [])
            for w in img_warns:
                if w not in warnings:
                    warnings.append(w)
            _write_paragraph(pdf, fonts, text, first_line_indent=indent_body)
            i += 3
            continue

        if t == "bullet_list_open":
            i = _render_list(pdf, fonts, tokens, i, ordered=False, warnings=warnings)
            continue

        if t == "ordered_list_open":
            i = _render_list(pdf, fonts, tokens, i, ordered=True, warnings=warnings)
            continue

        if t in ("fence", "code_block"):
            _write_code(pdf, fonts, token.content or "", info=(token.info or "").strip())
            i += 1
            continue

        if t == "table_open":
            i = _render_table(pdf, fonts, tokens, i, warnings=warnings)
            continue

        if t == "hr":
            _set_body_font(pdf, fonts)
            pdf.ln(2)
            y = pdf.get_y()
            pdf.set_draw_color(180, 180, 180)
            pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
            pdf.ln(4)
            i += 1
            continue

        if t == "blockquote_open":
            i = _render_blockquote(pdf, fonts, tokens, i, warnings=warnings)
            continue

        i += 1

    raw = pdf.output()
    pdf_bytes = bytes(raw) if isinstance(raw, (bytes, bytearray)) else bytes(raw)
    return MdToPdfResult(pdf_bytes=pdf_bytes, warnings=warnings)


# ---------------------------------------------------------------------------
# Fonts / fpdf bootstrap
# ---------------------------------------------------------------------------


def _ensure_fpdf() -> None:
    global _FPDF
    if _FPDF is not None:
        return
    from fpdf import FPDF

    _FPDF = FPDF


def _resolve_fonts(warnings: list[str]) -> _FontBundle:
    """Pick a CJK face, or Helvetica with an explicit warning."""
    path = discover_cjk_font()
    if path is None:
        warnings.append(
            "未找到可用的 CJK 字体（已探测系统 Windows/Fonts、Noto CJK、"
            "WenQuanYi、PingFang 等路径）。中文可能显示为方框；"
            "请安装 Noto Sans CJK 或系统中文字体后重试。"
        )
        return _FontBundle(family="Helvetica", path=None, cjk_ok=False)
    return _FontBundle(family="AgentCJK", path=path, cjk_ok=True)


def _register_fonts_on_pdf(pdf: Any, fonts: _FontBundle, warnings: list[str]) -> None:
    if not fonts.cjk_ok or fonts.path is None:
        return
    try:
        pdf.add_font(fonts.family, "", str(fonts.path))
        pdf.add_font(fonts.family, "B", str(fonts.path))
    except Exception as exc:  # noqa: BLE001
        msg = f"CJK 字体加载失败（{fonts.path}）：{exc}。中文可能显示为方框。"
        if msg not in warnings:
            warnings.append(msg)
        fonts.family = "Helvetica"
        fonts.path = None
        fonts.cjk_ok = False


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def _set_body_font(
    pdf: Any,
    fonts: _FontBundle,
    *,
    size: int | None = None,
    bold: bool = False,
) -> None:
    style = "B" if bold and fonts.cjk_ok else ""
    try:
        pdf.set_font(fonts.family, style=style, size=size or _BODY_PT)
    except RuntimeError:
        pdf.set_font("Helvetica", size=size or _BODY_PT)


def _safe_text(text: str, fonts: _FontBundle) -> str:
    """When no CJK face is loaded, replace non-latin-1 chars so Helvetica can emit.

    Callers must already have appended an explicit missing-font warning — we never
    silently tofu; this only keeps convert from crashing.
    """
    if fonts.cjk_ok or not text:
        return text
    out: list[str] = []
    for ch in text:
        try:
            ch.encode("latin-1")
            out.append(ch)
        except UnicodeEncodeError:
            out.append("?")
    return "".join(out)


def _write_heading(pdf: Any, fonts: _FontBundle, text: str, level: int) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.ln(3 if level <= 2 else 2)
    _set_body_font(pdf, fonts, size=_HEADING_PT[level], bold=True)
    # 文档大标题居中是通例（同 md_to_docx），两个档位都开。
    align = "C" if level == 1 else "L"
    pdf.multi_cell(0, _LINE + 1, _safe_text(text or "", fonts), align=align)
    pdf.ln(1)


def _write_paragraph(
    pdf: Any,
    fonts: _FontBundle,
    text: str,
    *,
    first_line_indent: bool = False,
) -> None:
    pdf.set_x(pdf.l_margin)
    if not text.strip():
        pdf.ln(2)
        return
    _set_body_font(pdf, fonts)
    if first_line_indent:
        _write_indented_paragraph(pdf, _safe_text(text, fonts))
    else:
        pdf.multi_cell(0, _LINE, _safe_text(text, fonts))
    pdf.ln(1)


def _write_indented_paragraph(pdf: Any, text: str) -> None:
    """首行缩进两字：走 fpdf2 文本区（``multi_cell`` 没有首行缩进这一档）。

    行高换算成与 ``multi_cell`` 相同的 ``_LINE``，两条路径的行距与换页流一致；缩进宽度
    取 2 em——中日韩字形是全角，2 em 就是整两个字。
    """
    line_height = _LINE / pdf.font_size if pdf.font_size else 1.0
    with pdf.text_columns() as columns:
        paragraph = columns.paragraph(
            line_height=line_height,
            first_line_indent=FIRST_LINE_INDENT_CHARS * pdf.font_size,
        )
        paragraph.write(text)
        columns.end_paragraph()
    pdf.set_x(pdf.l_margin)


def _write_code(pdf: Any, fonts: _FontBundle, content: str, *, info: str) -> None:
    text = content[:-1] if content.endswith("\n") else content
    pdf.set_x(pdf.l_margin)
    if info:
        _set_body_font(pdf, fonts, size=8)
        pdf.set_text_color(100, 100, 100)
        pdf.multi_cell(0, 4, _safe_text(info, fonts))
        pdf.set_text_color(0, 0, 0)
    _set_body_font(pdf, fonts, size=_CODE_PT)
    x0 = pdf.l_margin
    y0 = pdf.get_y()
    usable = pdf.w - pdf.l_margin - pdf.r_margin
    lines = text.split("\n") or [""]
    h = max(len(lines), 1) * (_LINE - 0.5) + 3
    pdf.set_fill_color(244, 244, 245)
    pdf.rect(x0, y0, usable, h, style="F")
    pdf.set_xy(x0 + 1.5, y0 + 1.5)
    pdf.multi_cell(usable - 3, _LINE - 0.5, _safe_text(text or " ", fonts))
    pdf.set_x(pdf.l_margin)
    pdf.ln(2)


def _render_list(
    pdf: Any,
    fonts: _FontBundle,
    tokens: list[Token],
    start: int,
    *,
    ordered: bool,
    warnings: list[str],
    level: int = 0,
) -> int:
    i = start + 1
    close_type = "ordered_list_close" if ordered else "bullet_list_close"
    index = 0
    while i < len(tokens):
        token = tokens[i]
        if token.type == close_type:
            return i + 1
        if token.type == "list_item_open":
            index += 1
            i += 1
            while i < len(tokens) and tokens[i].type != "list_item_close":
                if tokens[i].type == "paragraph_open":
                    inline = tokens[i + 1] if i + 1 < len(tokens) else None
                    body, img_warns = (
                        _inline_text_with_images(inline) if inline is not None else ("", [])
                    )
                    for w in img_warns:
                        if w not in warnings:
                            warnings.append(w)
                    bullet = f"{index}. " if ordered else "- "
                    indent = "  " * level
                    _set_body_font(pdf, fonts)
                    pdf.set_x(pdf.l_margin)
                    pdf.multi_cell(0, _LINE, _safe_text(f"{indent}{bullet}{body}", fonts))
                    i += 3
                    continue
                if tokens[i].type in ("bullet_list_open", "ordered_list_open"):
                    nested = tokens[i].type == "ordered_list_open"
                    i = _render_list(
                        pdf,
                        fonts,
                        tokens,
                        i,
                        ordered=nested,
                        warnings=warnings,
                        level=level + 1,
                    )
                    continue
                i += 1
            if i < len(tokens) and tokens[i].type == "list_item_close":
                i += 1
            continue
        i += 1
    return i


def _render_table(
    pdf: Any,
    fonts: _FontBundle,
    tokens: list[Token],
    start: int,
    *,
    warnings: list[str],
) -> int:
    rows: list[list[str]] = []
    i = start + 1
    current: list[str] = []
    while i < len(tokens):
        t = tokens[i]
        if t.type == "table_close":
            i += 1
            break
        if t.type == "tr_open":
            current = []
            i += 1
            continue
        if t.type == "tr_close":
            rows.append(current)
            current = []
            i += 1
            continue
        if t.type in ("th_open", "td_open"):
            inline = tokens[i + 1] if i + 1 < len(tokens) else None
            text, img_warns = _inline_text_with_images(inline) if inline is not None else ("", [])
            for w in img_warns:
                if w not in warnings:
                    warnings.append(w)
            current.append(text)
            i += 3
            continue
        i += 1

    if not rows:
        return i
    cols = max(len(r) for r in rows)
    usable = pdf.w - pdf.l_margin - pdf.r_margin
    col_w = usable / cols
    _set_body_font(pdf, fonts, size=10)
    for r_idx, row in enumerate(rows):
        if pdf.get_y() + _LINE + 2 > pdf.page_break_trigger:
            pdf.add_page()
            _set_body_font(pdf, fonts, size=10)
        y0 = pdf.get_y()
        x0 = pdf.l_margin
        # Rough height from widest cell string.
        heights: list[float] = []
        for c_idx in range(cols):
            cell = row[c_idx] if c_idx < len(row) else ""
            safe = _safe_text(cell, fonts) if cell else ""
            width = pdf.get_string_width(safe) if safe else 0
            approx = max(1, int(width // max(col_w - 2, 1)) + 1)
            heights.append(approx * (_LINE - 0.5) + 2)
        row_h = max(heights) if heights else _LINE + 2
        for c_idx in range(cols):
            cell = row[c_idx] if c_idx < len(row) else ""
            x = x0 + c_idx * col_w
            pdf.set_xy(x, y0)
            pdf.set_draw_color(200, 200, 200)
            if r_idx == 0:
                pdf.set_fill_color(245, 245, 246)
                pdf.rect(x, y0, col_w, row_h, style="DF")
                _set_body_font(pdf, fonts, size=10, bold=True)
            else:
                pdf.rect(x, y0, col_w, row_h, style="D")
                _set_body_font(pdf, fonts, size=10)
            pdf.set_xy(x + 1, y0 + 1)
            pdf.multi_cell(col_w - 2, _LINE - 0.5, _safe_text(cell or " ", fonts))
        pdf.set_y(y0 + row_h)
    pdf.set_x(pdf.l_margin)
    pdf.ln(2)
    return i


def _render_blockquote(
    pdf: Any,
    fonts: _FontBundle,
    tokens: list[Token],
    start: int,
    *,
    warnings: list[str],
) -> int:
    i = start + 1
    while i < len(tokens):
        if tokens[i].type == "blockquote_close":
            return i + 1
        if tokens[i].type == "paragraph_open":
            inline = tokens[i + 1] if i + 1 < len(tokens) else None
            text, img_warns = _inline_text_with_images(inline) if inline is not None else ("", [])
            for w in img_warns:
                if w not in warnings:
                    warnings.append(w)
            _set_body_font(pdf, fonts)
            pdf.set_text_color(80, 80, 80)
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, _LINE, _safe_text(f"| {text}", fonts))
            pdf.set_text_color(0, 0, 0)
            i += 3
            continue
        i += 1
    return i


# ---------------------------------------------------------------------------
# Inline text extraction
# ---------------------------------------------------------------------------


def _inline_text(inline: Token | None) -> str:
    text, _ = _inline_text_with_images(inline)
    return text


def _inline_text_with_images(inline: Token | None) -> tuple[str, list[str]]:
    if inline is None or inline.type != "inline":
        return "", []
    parts: list[str] = []
    warnings: list[str] = []
    children = inline.children or []
    link_stack: list[str] = []
    for child in children:
        ct = child.type
        if ct in ("text", "code_inline"):
            parts.append(child.content or "")
        elif ct in ("softbreak", "hardbreak"):
            parts.append("\n")
        elif ct == "link_open":
            href = str(child.attrGet("href") or "").strip()
            link_stack.append(href)
        elif ct == "link_close":
            if link_stack:
                href = link_stack.pop()
                if href:
                    parts.append(f" ({href})")
        elif ct == "image":
            src = str(child.attrGet("src") or "").strip()
            alt = (child.content or "").strip() or str(child.attrGet("alt") or "").strip()
            msg = f"PDF 导出不嵌入图片：{src or alt or '(空)'}"
            if msg not in warnings:
                warnings.append(msg)
            parts.append(f"[图片：{alt or src}]")
    return "".join(parts), warnings
