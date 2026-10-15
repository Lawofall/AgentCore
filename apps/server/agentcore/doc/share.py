"""Freeze a 文档 body and render the public read-only HTML page.

The snapshot is sanitized markdown (not pre-baked HTML). The public page runs
markdown-it with ``html=False`` — raw HTML in the source is escaped, never
passed through. Same discipline as conversation sharing. Layout is a document
site (title + optional TOC), not the conversation-share bubble page.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token

from agentcore.doc.body import sanitize_body

# html=False escapes raw HTML; linkify stays off; image is off until 附图.
# table is GFM. javascript:/data: URLs are rejected by markdown-it's validateLink.
_MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "breaks": True})
_MD.enable("table")
_MD.disable("image")

_SLUG_KEEP = re.compile(r"[^\w\-]+", re.UNICODE)
_SLUG_MAX = 80
_TOC_MIN_HEADINGS = 2
_TOC_MAX_HEADINGS = 40

_PAGE_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  background: #f4f1ea;
  color: #1c1917;
  font: 16px/1.7 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
    "Microsoft YaHei", Roboto, Helvetica, Arial, sans-serif;
}
.site { min-height: 100vh; display: flex; flex-direction: column; }
.hero {
  background: #1c1917;
  color: #fafaf9;
  padding: 36px 24px 28px;
}
.hero-inner { max-width: 1080px; margin: 0 auto; }
.hero .kicker {
  font-size: 12px; letter-spacing: .08em; text-transform: uppercase;
  color: #a8a29e; margin: 0 0 10px;
}
h1.title {
  font-size: clamp(28px, 4vw, 40px); line-height: 1.2; font-weight: 650;
  margin: 0 0 10px; word-break: break-word;
}
.hero .meta { font-size: 13px; color: #d6d3d1; margin: 0; }
.layout {
  flex: 1;
  max-width: 1080px; width: 100%;
  margin: 0 auto; padding: 28px 20px 48px;
  display: grid; gap: 32px; align-items: start;
}
@media (min-width: 880px) {
  .layout.has-toc { grid-template-columns: 220px minmax(0, 1fr); }
}
.toc {
  background: #fff; border: 1px solid #e7e5e4; border-radius: 12px;
  padding: 16px 16px 12px;
}
@media (min-width: 880px) {
  .toc { position: sticky; top: 16px; }
}
.toc-title {
  font-size: 12px; font-weight: 650; letter-spacing: .06em;
  text-transform: uppercase; color: #78716c; margin: 0 0 10px;
}
.toc ol { list-style: none; margin: 0; padding: 0; }
.toc li { margin: 0 0 6px; }
.toc a {
  color: #44403c; text-decoration: none; font-size: 13px; line-height: 1.4;
  display: block;
}
.toc a:hover { color: #1c1917; text-decoration: underline; }
.toc .toc-h2 { padding-left: 0; }
.toc .toc-h3 { padding-left: 12px; }
.toc .toc-h4, .toc .toc-h5, .toc .toc-h6 { padding-left: 20px; }
.article {
  background: #fff; border: 1px solid #e7e5e4; border-radius: 12px;
  padding: 32px 28px 40px; min-width: 0;
}
.article > :first-child { margin-top: 0; }
.article > :last-child { margin-bottom: 0; }
.article h1, .article h2, .article h3, .article h4 {
  line-height: 1.3; margin: 1.6em 0 0.6em; scroll-margin-top: 16px;
  word-break: break-word;
}
.article h1 { font-size: 26px; }
.article h2 { font-size: 20px; }
.article h3 { font-size: 17px; }
.article p { margin: 0 0 14px; overflow-wrap: anywhere; }
.article ul, .article ol { margin: 0 0 14px; padding-left: 1.4em; }
.article li { margin: 0 0 4px; overflow-wrap: anywhere; }
.article hr { border: 0; border-top: 1px solid #e7e5e4; margin: 28px 0; }
.article pre {
  background: #1c1917; color: #f5f5f4; border-radius: 10px;
  padding: 14px 16px; overflow-x: auto; font-size: 13.5px; line-height: 1.6;
}
.article code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13.5px;
}
.article :not(pre) > code {
  background: #f5f5f4; padding: 1px 6px; border-radius: 6px;
}
.article a { color: #1d4ed8; }
.article blockquote {
  margin: 0 0 14px; padding: 2px 16px; border-left: 3px solid #d6d3d1;
  color: #57534e;
}
.article table {
  width: 100%; border-collapse: collapse; margin: 0 0 16px; font-size: 14px;
}
.article th, .article td {
  border: 1px solid #e7e5e4; padding: 8px 10px; text-align: left;
  vertical-align: top; overflow-wrap: anywhere;
}
.article th { background: #fafaf9; font-weight: 600; }
.article .meta { font-size: 13px; color: #78716c; }
footer.site-foot {
  padding: 20px 24px 32px; font-size: 12px; color: #78716c; text-align: center;
}
"""


def freeze_share_snapshot(body: Any) -> dict[str, str]:
    """Sanitize then freeze. Non-markdown payloads become empty."""
    return sanitize_body(body)


def _fmt_share_date(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d %H:%M UTC")


def _slug(text: str, used: dict[str, int]) -> str:
    compact = _SLUG_KEEP.sub("-", (text or "").strip()).strip("-").lower()
    if not compact:
        compact = "section"
    compact = compact[:_SLUG_MAX]
    n = used.get(compact, 0) + 1
    used[compact] = n
    return compact if n == 1 else f"{compact}-{n}"


def _heading_level(tag: str) -> int:
    if len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
        return int(tag[1])
    return 1


def _render_markdown(markdown: str) -> tuple[str, list[tuple[int, str, str]]]:
    tokens: list[Token] = _MD.parse(markdown)
    headings: list[tuple[int, str, str]] = []
    used: dict[str, int] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.type == "heading_open":
            level = _heading_level(tok.tag)
            text = ""
            if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
                text = tokens[i + 1].content or ""
            slug = _slug(text, used)
            tok.attrSet("id", slug)
            if len(headings) < _TOC_MAX_HEADINGS:
                headings.append((level, slug, text))
        i += 1
    body = _MD.renderer.render(tokens, _MD.options, {})
    return body, headings


def _render_toc(headings: list[tuple[int, str, str]]) -> str:
    if len(headings) < _TOC_MIN_HEADINGS:
        return ""
    items: list[str] = []
    for level, slug, text in headings:
        label = html.escape(text.strip() or slug)
        safe_slug = html.escape(slug, quote=True)
        cls = f"toc-h{min(level, 6)}"
        items.append(
            f'<li class="{cls}"><a href="#{safe_slug}">{label}</a></li>'
        )
    joined = "\n".join(items)
    return (
        '<nav class="toc" aria-label="目录">\n'
        '<p class="toc-title">目录</p>\n'
        f"<ol>\n{joined}\n</ol>\n"
        "</nav>\n"
    )


def render_doc_share_html(
    *, title: str, snapshot: Any, created_at: datetime | None
) -> str:
    """Self-contained public document site. ``noindex``. Markdown is untrusted."""
    safe_title = html.escape((title or "").strip() or "未命名文档")
    date_str = _fmt_share_date(created_at)
    markdown = sanitize_body(snapshot)["markdown"]
    toc_html = ""
    layout_cls = "layout"
    if markdown.strip():
        body, headings = _render_markdown(markdown)
        toc_html = _render_toc(headings)
        if toc_html:
            layout_cls = "layout has-toc"
    else:
        body = '<p class="meta">（空文档）</p>'
    meta_line = f"由 GAgent 发布 · {date_str}" if date_str else "由 GAgent 发布"
    return (
        "<!doctype html>\n"
        '<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        f"<title>{safe_title}</title>\n"
        f"<style>{_PAGE_CSS}</style>\n"
        "</head>\n<body>\n"
        '<div class="site">\n'
        '<header class="hero"><div class="hero-inner">'
        '<p class="kicker">文档</p>'
        f'<h1 class="title">{safe_title}</h1>'
        f'<p class="meta">{html.escape(meta_line)}</p>'
        "</div></header>\n"
        f'<div class="{layout_cls}">\n'
        f"{toc_html}"
        f'<main class="article">\n{body}\n</main>\n'
        "</div>\n"
        '<footer class="site-foot">'
        "本页面是 AgentCore 的只读发布页，内容为上次发布时的快照。"
        "</footer>\n"
        "</div>\n</body>\n</html>\n"
    )
