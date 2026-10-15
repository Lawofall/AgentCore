"""Conversation sharing: frozen snapshot + public read-only HTML page (分享对话).

分享 is an explicit, opt-in action (隐私承诺: 分享 = 显式操作、操作前明示). At share
time the transcript is FROZEN into a content-only snapshot (``build_share_snapshot``)
stored on the share row; the public page (``render_share_html``) renders that copy,
so later edits / deletes to the live messages never leak into a shared link and no
future turns are exposed (所见即所享). Content-only by design — never reasoning /
cost / team graph / files.

**Security (public, unauthenticated surface):** message bodies are untrusted user /
model text, so the Markdown renderer runs with ``html=False`` — raw HTML in the
source is escaped, never passed through — and markdown-it-py's default ``validateLink``
rejects dangerous URL schemes (``javascript:`` / ``vbscript:`` / ``file:`` / most
``data:``). The page title is HTML-escaped. This is the documented safe configuration;
do NOT flip ``html=True`` here.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import datetime

from markdown_it import MarkdownIt

from agentcore.db.models import Message

_SHARED_ROLES = ("user", "assistant")
_ROLE_LABELS = {"user": "用户", "assistant": "GAgent"}

# Safe-by-default Markdown renderer for untrusted content (see module security note).
# html=False escapes raw HTML; linkify stays off (no auto-linking, no extra dep).
_MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "breaks": True})


def build_share_snapshot(messages: Sequence[Message]) -> list[dict]:
    """Freeze a conversation's user/assistant turns into a content-only snapshot.

    Each entry is ``{role, content, created_at(iso)}`` — the minimum to render the
    public transcript, and nothing private (no reasoning / cost / tools / files).
    Empty-content rows are dropped so a shared page has no blank turns.
    """
    snapshot: list[dict] = []
    for m in messages:
        if m.role not in _SHARED_ROLES:
            continue
        content = (m.content or "").strip()
        if not content:
            continue
        snapshot.append(
            {
                "role": m.role,
                "content": content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
        )
    return snapshot


def _fmt_share_date(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d %H:%M UTC")


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
.msg { margin: 18px 0; }
.role {
  font-size: 12px; font-weight: 600; letter-spacing: .02em;
  text-transform: uppercase; color: #6b7280; margin-bottom: 6px;
}
.bubble {
  background: #fff; border: 1px solid #e5e7eb; border-radius: 12px;
  padding: 14px 18px; overflow-wrap: anywhere;
}
.msg.user .bubble { background: #eef2ff; border-color: #dfe4ff; }
.content > :first-child { margin-top: 0; }
.content > :last-child { margin-bottom: 0; }
.content p { margin: 0 0 12px; }
.content pre {
  background: #0f172a; color: #e2e8f0; border-radius: 10px;
  padding: 14px 16px; overflow-x: auto; font-size: 13.5px; line-height: 1.6;
}
.content code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13.5px;
}
.content :not(pre) > code {
  background: #f1f5f9; padding: 1px 6px; border-radius: 6px;
}
.content a { color: #4f46e5; }
.content blockquote {
  margin: 0 0 12px; padding: 2px 16px; border-left: 3px solid #d1d5db;
  color: #4b5563;
}
.content table { border-collapse: collapse; width: 100%; margin: 0 0 12px; }
.content th, .content td { border: 1px solid #e5e7eb; padding: 6px 10px; text-align: left; }
.content img { max-width: 100%; height: auto; }
footer {
  margin-top: 40px; padding-top: 20px; border-top: 1px solid #e5e7eb;
  font-size: 12px; color: #9ca3af; text-align: center;
}
"""


def render_share_html(*, title: str, snapshot: Sequence[dict], created_at: datetime | None) -> str:
    """Render the public read-only HTML page for a shared conversation.

    Self-contained (inline CSS, no external assets) so it renders for anyone with the
    link. ``noindex`` keeps a private share out of search engines. Message bodies go
    through the safe Markdown renderer; the title is HTML-escaped.
    """
    safe_title = html.escape((title or "").strip() or "AgentCore 对话")
    date_str = _fmt_share_date(created_at)

    parts: list[str] = []
    for entry in snapshot:
        role = entry.get("role", "assistant")
        label = html.escape(_ROLE_LABELS.get(role, role))
        content_html = _MD.render(str(entry.get("content") or ""))
        role_class = "user" if role == "user" else "assistant"
        parts.append(
            f'<div class="msg {role_class}">'
            f'<div class="role">{label}</div>'
            f'<div class="bubble"><div class="content">{content_html}</div></div>'
            f"</div>"
        )
    body = "\n".join(parts) if parts else '<p class="meta">（空对话）</p>'

    meta_line = f"由 GAgent 分享 · {date_str}" if date_str else "由 GAgent 分享"

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
        f'<div class="meta">{html.escape(meta_line)}</div></header>\n'
        f"{body}\n"
        "<footer>本页面是 AgentCore 的只读分享，内容为分享时的快照。</footer>\n"
        "</div>\n</body>\n</html>\n"
    )
