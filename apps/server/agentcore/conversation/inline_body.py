"""Inline attachment / mention markers in user ``content``.

Composer pills persist as U+FFFC-delimited tokens so body order is one sequence:
text, materials, and role mentions. Indices address ``attachments[]`` /
``agent_mentions[]`` in appearance order. No extra wire field.

Token: ``\\uFFFC`` + ``A``|``M`` + decimal index + ``\\uFFFC``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Literal

OBJECT = "\uFFFC"
TOKEN_RE = re.compile(OBJECT + r"([AM])(\d+)" + OBJECT)

SpanKind = Literal["text", "attachment", "mention"]


def token(kind: Literal["A", "M"], index: int) -> str:
    return f"{OBJECT}{kind}{index}{OBJECT}"


def has_inline_markers(content: str | None) -> bool:
    if not content:
        return False
    return OBJECT in content and TOKEN_RE.search(content) is not None


def parse_inline_body(content: str) -> list[tuple[SpanKind, str | int]]:
    """Return ``(kind, text|index)`` spans. Unknown / malformed tokens stay as text."""
    if not content:
        return []
    spans: list[tuple[SpanKind, str | int]] = []
    pos = 0
    for m in TOKEN_RE.finditer(content):
        if m.start() > pos:
            spans.append(("text", content[pos : m.start()]))
        kind: SpanKind = "attachment" if m.group(1) == "A" else "mention"
        spans.append((kind, int(m.group(2))))
        pos = m.end()
    if pos < len(content):
        spans.append(("text", content[pos:]))
    return spans


def serialize_inline_body(spans: Sequence[tuple[SpanKind, str | int]]) -> str:
    parts: list[str] = []
    for kind, payload in spans:
        if kind == "text":
            parts.append(str(payload).replace(OBJECT, ""))
        elif kind == "attachment":
            parts.append(token("A", int(payload)))
        else:
            parts.append(token("M", int(payload)))
    return "".join(parts)


def plain_text(content: str | None) -> str:
    """User-visible words only — markers (and unknown FFFC) dropped."""
    if not content:
        return ""
    return TOKEN_RE.sub("", content).replace(OBJECT, "")


def migrate_legacy_draft(
    content: str,
    attachment_count: int,
    mention_count: int,
) -> str:
    """Old drafts kept chips outside the body. Append tokens once, at the end."""
    if has_inline_markers(content):
        return content
    if attachment_count <= 0 and mention_count <= 0:
        return content
    extra = "".join(token("A", i) for i in range(attachment_count)) + "".join(
        token("M", i) for i in range(mention_count)
    )
    return content + extra


def _name_of(att: Mapping[str, object] | object, fallback: str) -> str:
    if isinstance(att, Mapping):
        raw = att.get("name") or att.get("path") or fallback
        return str(raw).strip() or fallback
    name = getattr(att, "name", None)
    path = getattr(att, "path", None)
    raw = name or path or fallback
    return str(raw).strip() or fallback


def _kind_of(att: Mapping[str, object] | object) -> str:
    if isinstance(att, Mapping):
        return str(att.get("kind") or "file")
    return str(getattr(att, "kind", None) or "file")


def _role_of(mention: Mapping[str, object] | object, fallback: str) -> str:
    if isinstance(mention, Mapping):
        raw = mention.get("role") or mention.get("agent_id") or fallback
        return str(raw).strip() or fallback
    role = getattr(mention, "role", None)
    agent_id = getattr(mention, "agent_id", None)
    raw = role or agent_id or fallback
    return str(raw).strip() or fallback


_KIND_LABEL = {"file": "文件", "dir": "文件夹", "conversation": "对话"}


def render_inline_labels(
    content: str,
    attachments: Sequence[Mapping[str, object] | object] | None,
    mentions: Sequence[Mapping[str, object] | object] | None,
) -> str:
    """History / title / preview: keep order, don't re-inject file bodies."""
    atts = list(attachments or [])
    ments = list(mentions or [])
    parts: list[str] = []
    for kind, payload in parse_inline_body(content):
        if kind == "text":
            parts.append(str(payload))
            continue
        if kind == "attachment":
            idx = int(payload)
            if 0 <= idx < len(atts):
                att = atts[idx]
                label = _KIND_LABEL.get(_kind_of(att), "文件")
                parts.append(f"[{label} {_name_of(att, str(idx))}]")
            continue
        idx = int(payload)
        if 0 <= idx < len(ments):
            parts.append(f"[点名 {_role_of(ments[idx], str(idx))}]")
    return "".join(parts)


def mention_inline_stub(mention: Mapping[str, object] | object) -> str:
    return f"（点名 {_role_of(mention, '?')}）"


def weave_inline_body(
    content: str,
    file_blocks: Sequence[str],
    mention_stubs: Sequence[str],
) -> str | None:
    """This-turn user message: file bodies sit where the user placed the pills.

    Returns None when there are no markers so callers keep the old envelope path.
    """
    if not has_inline_markers(content):
        return None
    parts: list[str] = []
    for kind, payload in parse_inline_body(content):
        if kind == "text":
            parts.append(str(payload))
            continue
        if kind == "attachment":
            idx = int(payload)
            if 0 <= idx < len(file_blocks):
                block = file_blocks[idx]
                if block:
                    parts.append(block if parts and parts[-1].endswith("\n") else f"\n{block}\n")
            continue
        idx = int(payload)
        if 0 <= idx < len(mention_stubs):
            parts.append(mention_stubs[idx])
    return "".join(parts)


def apply_inline_body(
    user_message: str,
    file_blocks: Sequence[str],
    mentions: Sequence[Mapping[str, object]] | None,
    full_context: str | None,
    slim_context: str | None,
) -> tuple[str, str | None]:
    """If the body has pills, weave materials into it and use the slim envelope."""
    stubs = [mention_inline_stub(m) for m in (mentions or [])]
    woven = weave_inline_body(user_message, file_blocks, stubs)
    if woven is None:
        return user_message, full_context
    return woven, slim_context
