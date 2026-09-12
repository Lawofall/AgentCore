"""Freeze a 文档 body and render the public read-only HTML page.

The snapshot is the sanitized block list (not pre-baked HTML). The public page
escapes every text node — block text is untrusted user/model content.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from agentcore.doc.body import sanitize_body

_PAGE_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0;
  background: #f7f7f8;
  color: #1f2328;
  font: 16px/1.7 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
    "Microsoft YaHei", Roboto, Helvetica, Arial, sans-serif;
}
.wrap { max-width: 768px; margin: 0 auto; padding: 32px 20px 64px; }
header { margin-bottom: 28px; }
h1.title { font-size: 24px; line-height: 1.3; margin: 0 0 8px; word-break: break-word; }
.meta { font-size: 13px; color: #6b7280; }
.article h1 { font-size: 22px; line-height: 1.3; margin: 24px 0 10px; }
.article h2 { font-size: 18px; line-height: 1.35; margin: 20px 0 8px; }
.article h3 { font-size: 16px; line-height: 1.4; margin: 16px 0 8px; }
.article p { margin: 0 0 12px; overflow-wrap: anywhere; }
.article ul, .article ol { margin: 0 0 12px; padding-left: 1.4em; }
.article li { margin: 0 0 4px; overflow-wrap: anywhere; }
.article table.doc-table {
  width: 100%; border-collapse: collapse; margin: 0 0 16px; font-size: 14px;
}
.article table.doc-table th,
.article table.doc-table td {
  border: 1px solid #e5e7eb; padding: 8px 10px; text-align: left;
  vertical-align: top; overflow-wrap: anywhere;
}
.article table.doc-table th { background: #f3f4f6; font-weight: 600; }
.article figure.doc-chart { margin: 0 0 16px; }
.article figure.doc-chart figcaption {
  font-size: 14px; font-weight: 600; margin-bottom: 8px; overflow-wrap: anywhere;
}
.article figure.doc-chart svg.doc-chart-svg {
  display: block; width: 100%; max-width: 640px; height: auto;
}
footer {
  margin-top: 40px; padding-top: 20px; border-top: 1px solid #e5e7eb;
  font-size: 12px; color: #9ca3af; text-align: center;
}
"""


def freeze_share_snapshot(body: Any) -> dict[str, Any]:
    """Sanitize then freeze. Unknown block types are dropped."""
    return sanitize_body(body)


def _fmt_share_date(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d %H:%M UTC")


def _text(value: Any) -> str:
    return html.escape(str(value or ""))


def _text_br(value: Any) -> str:
    return _text(value).replace("\n", "<br>\n")


def _chart_number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0.0
    if num != num or num in (float("inf"), float("-inf")):  # NaN / inf
        return 0.0
    if num < 0:
        return 0.0
    return min(num, 1e12)


def _render_bar_chart_svg(aria_title: str, items: Sequence[dict[str, Any]]) -> str:
    """Static vertical bar chart. All labels are escaped — no raw user SVG."""
    width = 640
    height = 300
    pad_l, pad_r, pad_t, pad_b = 48, 24, 16, 72
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    rows: list[tuple[str, float]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        label = _text(str(row.get("label") or ""))
        rows.append((label, _chart_number(row.get("value"))))

    max_val = max((v for _, v in rows), default=0.0)
    scale = max_val if max_val > 0 else 1.0
    n = len(rows)
    gap = 8
    bar_w = (plot_w - gap * (n + 1)) / n if n else 0

    parts: list[str] = [
        f'<svg class="doc-chart-svg" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="{_text(aria_title or "柱状图")}">'
    ]
    chart_y0 = pad_t
    axis_y = chart_y0 + plot_h
    parts.append(
        f'<line x1="{pad_l}" y1="{axis_y}" x2="{width - pad_r}" y2="{axis_y}" '
        f'stroke="#e5e7eb" stroke-width="1"/>'
    )
    for i, (label, val) in enumerate(rows):
        bar_h = (val / scale) * plot_h if n else 0
        x = pad_l + gap + i * (bar_w + gap)
        y = axis_y - bar_h
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" '
            f'fill="#3b82f6" rx="2"/>'
        )
        lx = x + bar_w / 2
        ly = axis_y + 14
        parts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="11" fill="#4b5563" '
            f'text-anchor="middle">{label}</text>'
        )
        if val > 0:
            parts.append(
                f'<text x="{lx:.1f}" y="{y - 4:.1f}" font-size="10" fill="#6b7280" '
                f'text-anchor="middle">{_text(_format_chart_value(val))}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def _format_chart_value(val: float) -> str:
    if val == int(val) and val < 1e9:
        return str(int(val))
    if val >= 1e9:
        return f"{val:.2e}"
    return f"{val:g}"


def _render_chart_block(block: dict[str, Any]) -> str:
    title = block.get("title")
    title = "" if title is None else str(title)
    raw_items = block.get("items")
    if not isinstance(raw_items, list):
        raw_items = []
    svg = _render_bar_chart_svg(title or "柱状图", raw_items)
    cap = f"<figcaption>{_text(title)}</figcaption>" if title else ""
    return f'<figure class="doc-chart">{cap}{svg}</figure>'


def _render_blocks(blocks: Sequence[Any]) -> str:
    parts: list[str] = []
    for item in blocks:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "heading":
            level = item.get("level", 1)
            try:
                level_i = int(level)
            except (TypeError, ValueError):
                level_i = 1
            if level_i not in (1, 2, 3):
                level_i = 1
            parts.append(f"<h{level_i}>{_text(item.get('text'))}</h{level_i}>")
            continue
        if kind == "paragraph":
            parts.append(f"<p>{_text_br(item.get('text'))}</p>")
            continue
        if kind == "list":
            tag = "ol" if item.get("ordered") else "ul"
            raw_items = item.get("items")
            if not isinstance(raw_items, list):
                raw_items = []
            lis = "".join(f"<li>{_text_br(row)}</li>" for row in raw_items)
            parts.append(f"<{tag}>{lis}</{tag}>")
            continue
        if kind == "table":
            raw_cols = item.get("columns")
            if not isinstance(raw_cols, list) or not raw_cols:
                raw_cols = [""]
            col_count = len(raw_cols)
            thead = (
                "<tr>"
                + "".join(f"<th>{_text_br(c)}</th>" for c in raw_cols)
                + "</tr>"
            )
            raw_rows = item.get("rows")
            if not isinstance(raw_rows, list):
                raw_rows = []
            trs = []
            for row in raw_rows:
                row_in = row if isinstance(row, list) else []
                cells: list[Any] = []
                for i in range(col_count):
                    cells.append(row_in[i] if i < len(row_in) else "")
                tds = "".join(f"<td>{_text_br(cell)}</td>" for cell in cells)
                trs.append(f"<tr>{tds}</tr>")
            tbody = "".join(trs)
            parts.append(
                f'<table class="doc-table"><thead>{thead}</thead><tbody>{tbody}</tbody></table>'
            )
            continue
        if kind == "chart":
            block_kind = item.get("kind") or "bar"
            if block_kind == "bar":
                parts.append(_render_chart_block(item))
    return "\n".join(parts)


def render_doc_share_html(
    *, title: str, snapshot: Any, created_at: datetime | None
) -> str:
    """Self-contained public page. ``noindex``. Every block text is escaped."""
    safe_title = _text((title or "").strip() or "未命名文档")
    date_str = _fmt_share_date(created_at)
    src: dict[str, Any] = snapshot if isinstance(snapshot, dict) else {}
    blocks = src.get("blocks")
    if not isinstance(blocks, list):
        blocks = []
    body = _render_blocks(blocks)
    if not body:
        body = '<p class="meta">（空文档）</p>'
    meta_line = f"由 AgentCore 分享 · {date_str}" if date_str else "由 AgentCore 分享"
    return (
        "<!doctype html>\n"
        '<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        f"<title>{safe_title}</title>\n"
        f"<style>{_PAGE_CSS}</style>\n"
        "</head>\n<body>\n"
        '<div class="wrap">\n'
        f'<header><h1 class="title">{safe_title}</h1>'
        f'<div class="meta">{_text(meta_line)}</div></header>\n'
        f'<div class="article">\n{body}\n</div>\n'
        "<footer>本页面是 AgentCore 的只读分享，内容为分享时的快照。</footer>\n"
        "</div>\n</body>\n</html>\n"
    )
