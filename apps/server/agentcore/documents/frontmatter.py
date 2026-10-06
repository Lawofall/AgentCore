"""Strict md-entry frontmatter: ``apply`` + ``description`` + optional ``paths``.

Known keys are parsed as ``key: value`` lines. Unknown keys / comment lines / blank
lines stay opaque text. Write-back is **text-level minimal edit** — never
parse-then-serialize — so unknown keys, comments, and key order survive a round-trip.

See docs/03-AI核心/Agent记忆与知识系统.md「frontmatter 是唯一可写真源」/「具体 schema」。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from agentcore.documents.path_rules import parse_path_patterns

ApplyMode = Literal["always", "on_demand", "paths"]

_VALID_APPLY: frozenset[str] = frozenset({"always", "on_demand", "paths"})
_FENCE = "---"
_KNOWN_LINE = re.compile(
    r"^(\s*)(apply|description|paths)(\s*:\s*)(.*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedFrontmatter:
    """Successful structural parse (missing known keys are defined defaults, not errors)."""

    apply: ApplyMode
    description: str
    body: str
    has_frontmatter: bool
    apply_present: bool
    description_present: bool
    paths: tuple[str, ...] = ()
    paths_present: bool = False


@dataclass(frozen=True)
class FrontmatterError:
    """Parse failure: unclosed opening fence, or a stated ``apply`` we cannot read.

    No guessing / auto-repair — the caller must not inject and must surface this.
    """

    message: str


class FrontmatterEditError(ValueError):
    """Raised when text-level edit cannot proceed (unclosed fence)."""


@dataclass(frozen=True)
class _SplitOk:
    bom: str
    newline: str
    fm_lines: list[str]  # keepends
    close_line: str  # keepends
    body: str


@dataclass(frozen=True)
class _SplitAbsent:
    bom: str
    text: str  # content without BOM


@dataclass(frozen=True)
class _SplitUnclosed:
    pass


def frontmatter_error_message(content: str) -> str | None:
    """UI-facing error string, or None when content is structurally fine."""
    result = parse_entry_frontmatter(content)
    if isinstance(result, FrontmatterError):
        return result.message
    return None


def parse_entry_frontmatter(content: str) -> ParsedFrontmatter | FrontmatterError:
    """Parse entry frontmatter.

    - No opening ``---`` → defined defaults (``apply=on_demand``, empty description).
    - Opening ``---`` without a closing fence line → :class:`FrontmatterError`.
    - ``apply`` present with an unrecognized value → :class:`FrontmatterError`. Absence is a
      defined state (``on_demand``); a *stated* intent we cannot read must not be silently
      downgraded to 按需 — that would stop an always-on entry from applying, unnoticed.
      Case is normalized first (``Always`` is not a typo).
    """
    split = _split_frontmatter(content)
    if isinstance(split, _SplitUnclosed):
        return FrontmatterError(message="unclosed frontmatter")
    if isinstance(split, _SplitAbsent):
        return ParsedFrontmatter(
            apply="on_demand",
            description="",
            body=content,
            has_frontmatter=False,
            apply_present=False,
            description_present=False,
        )

    apply: ApplyMode = "on_demand"
    description = ""
    paths: tuple[str, ...] = ()
    apply_present = False
    description_present = False
    paths_present = False
    for line in split.fm_lines:
        raw = line.rstrip("\r\n")
        m = _KNOWN_LINE.match(raw)
        if m is None:
            continue
        key = m.group(2).lower()
        value, _comment = _split_inline_comment(m.group(4))
        if key == "apply":
            apply_present = True
            normalized = value.lower()
            if normalized not in _VALID_APPLY:
                return FrontmatterError(message=f"unrecognized apply value: {value!r}")
            apply = normalized  # type: ignore[assignment]
        elif key == "description":
            description_present = True
            description = value
        elif key == "paths":
            paths_present = True
            paths = parse_path_patterns(value)

    if apply == "paths" and not paths:
        return FrontmatterError(message="paths apply requires a paths pattern")

    return ParsedFrontmatter(
        apply=apply,
        description=description,
        body=split.body,
        has_frontmatter=True,
        apply_present=apply_present,
        description_present=description_present,
        paths=paths,
        paths_present=paths_present,
    )


def set_entry_frontmatter(
    content: str,
    *,
    apply: ApplyMode | None = None,
    description: str | None = None,
    paths: Sequence[str] | None = None,
) -> str:
    """Text-level minimal edit of known keys. ``None`` = leave that key untouched.

    Preserves unknown keys, ``#`` comment lines, blank lines, and existing key order.
    New keys are appended just before the closing fence (or in a new block when absent).
    ``paths=()`` removes that key. Raises :class:`FrontmatterEditError` on unclosed
    frontmatter (no auto-repair).
    """
    if apply is None and description is None and paths is None:
        return content

    split = _split_frontmatter(content)
    if isinstance(split, _SplitUnclosed):
        raise FrontmatterEditError("unclosed frontmatter")
    if isinstance(split, _SplitAbsent):
        return (
            split.bom
            + _render_new_block(
                apply=apply,
                description=description,
                paths=paths,
            )
            + split.text
        )

    paths_value = None if paths is None else _format_paths(paths)
    updated = list(split.fm_lines)
    seen_apply = False
    seen_description = False
    seen_paths = False
    drop: set[int] = set()
    for i, line in enumerate(updated):
        raw = line.rstrip("\r\n")
        ending = line[len(raw) :]
        m = _KNOWN_LINE.match(raw)
        if m is None:
            continue
        key = m.group(2).lower()
        indent, colon = m.group(1), m.group(3)
        _val, comment = _split_inline_comment(m.group(4))
        if key == "apply" and apply is not None:
            seen_apply = True
            # Keep original key spelling/indent/colon spacing; only replace the value.
            key_token = m.group(2)
            updated[i] = f"{indent}{key_token}{colon}{apply}{comment}{ending}"
        elif key == "description" and description is not None:
            seen_description = True
            key_token = m.group(2)
            updated[i] = f"{indent}{key_token}{colon}{description}{comment}{ending}"
        elif key == "paths" and paths is not None:
            seen_paths = True
            if paths_value is None:
                drop.add(i)
            else:
                key_token = m.group(2)
                updated[i] = f"{indent}{key_token}{colon}{paths_value}{comment}{ending}"

    if drop:
        updated = [line for i, line in enumerate(updated) if i not in drop]

    to_append: list[str] = []
    nl = split.newline
    if apply is not None and not seen_apply:
        to_append.append(f"apply: {apply}{nl}")
    if description is not None and not seen_description:
        to_append.append(f"description: {description}{nl}")
    if paths_value is not None and not seen_paths:
        to_append.append(f"paths: {paths_value}{nl}")

    return (
        split.bom
        + _FENCE
        + nl
        + "".join(updated)
        + "".join(to_append)
        + split.close_line
        + split.body
    )


def strip_entry_frontmatter(content: str) -> str | None:
    """Remove a well-formed frontmatter block.

    - No opening fence → ``content`` unchanged.
    - Unclosed fence → ``None`` (caller must not inject; no auto-repair).
    - Well-formed → body after the closing fence (exact bytes).
    """
    if not content:
        return ""
    result = parse_entry_frontmatter(content)
    if isinstance(result, FrontmatterError):
        return None
    if not result.has_frontmatter:
        return content
    return result.body


def ensure_apply_key(content: str, apply: ApplyMode) -> str:
    """If ``apply`` is absent (or there is no FM), set it; never overwrite a present key."""
    parsed = parse_entry_frontmatter(content)
    if isinstance(parsed, FrontmatterError):
        raise FrontmatterEditError(parsed.message)
    if parsed.apply_present:
        return content
    return set_entry_frontmatter(content, apply=apply)


def set_entry_frontmatter_total(content: str, *, apply: ApplyMode) -> tuple[str, bool]:
    """Always succeed: return text that parses cleanly and carries ``apply``.

    Prefer text-level minimal edit via :func:`set_entry_frontmatter` (identical
    output for inputs that function already accepts). When that raises
    :class:`FrontmatterEditError` (unclosed opening fence), prepend a well-formed
    block and demote the entire original text — byte-identical, including a
    leading BOM if present — to the body. Column truth is the sole source; this
    does not guess user intent.

    Returns ``(new_content, prepended)``. ``prepended`` is True only on the
    demote-to-body path (not when absent FM already causes a normal new block).

    Runtime write paths must keep using :func:`set_entry_frontmatter` so broken
    fences still surface as 400; this entry is for must-succeed callers (e.g.
    one-shot migrations).
    """
    try:
        return set_entry_frontmatter(content, apply=apply), False
    except FrontmatterEditError:
        bom = ""
        text = content
        if text.startswith("\ufeff"):
            bom = "\ufeff"
            text = text[1:]
        return (
            bom + _render_new_block(apply=apply, description=None) + text,
            True,
        )


def _split_frontmatter(
    content: str,
) -> _SplitOk | _SplitAbsent | _SplitUnclosed:
    bom = ""
    text = content
    if text.startswith("\ufeff"):
        bom = "\ufeff"
        text = text[1:]

    if not text.startswith(_FENCE):
        return _SplitAbsent(bom=bom, text=text)

    after = text[len(_FENCE) :]
    # Exact ``---`` / ``---``+WS only → unclosed.
    if after.lstrip(" \t") == "":
        return _SplitUnclosed()
    # ``---foo`` (no newline after fence) → not a frontmatter opening.
    if after[0] not in "\r\n":
        return _SplitAbsent(bom=bom, text=text)

    newline = "\n"
    if after.startswith("\r\n"):
        rest = after[2:]
        newline = "\r\n"
    elif after.startswith("\n"):
        rest = after[1:]
        newline = "\n"
    else:
        rest = after[1:]
        newline = "\r"

    lines = rest.splitlines(keepends=True)
    close_at: int | None = None
    for i, line in enumerate(lines):
        if line.rstrip("\r\n").strip() == _FENCE:
            close_at = i
            break
    if close_at is None:
        return _SplitUnclosed()

    return _SplitOk(
        bom=bom,
        newline=newline,
        fm_lines=lines[:close_at],
        close_line=lines[close_at],
        body="".join(lines[close_at + 1 :]),
    )


def _split_inline_comment(value_raw: str) -> tuple[str, str]:
    """Split ``value`` / ``value # comment`` → (value, comment_suffix with leading space)."""
    idx = value_raw.find(" #")
    if idx < 0:
        return value_raw.strip(), ""
    return value_raw[:idx].strip(), value_raw[idx:]


def _format_paths(patterns: Sequence[str]) -> str | None:
    tokens = parse_path_patterns(",".join(patterns))
    if not tokens:
        return None
    return ", ".join(tokens)


def _render_new_block(
    *,
    apply: ApplyMode | None,
    description: str | None,
    paths: Sequence[str] | None = None,
) -> str:
    lines = [_FENCE]
    if apply is not None:
        lines.append(f"apply: {apply}")
    if description is not None:
        lines.append(f"description: {description}")
    formatted_paths = None if paths is None else _format_paths(paths)
    if formatted_paths is not None:
        lines.append(f"paths: {formatted_paths}")
    lines.append(_FENCE)
    return "\n".join(lines) + "\n"
