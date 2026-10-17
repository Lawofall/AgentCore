"""Workspace markdown 交代: ``AgentCore/rules/**/*.md`` is the source of truth.

Bounded discovery of the convention directory. Frontmatter ``apply`` /
``description`` / ``paths`` is the channel. Missing ``apply`` in that
directory is 按需. Empty ``description`` stays out of the catalog.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token

from agentcore.core.logging import get_logger
from agentcore.documents.frontmatter import FrontmatterError, parse_entry_frontmatter
from agentcore.memory.rule_resolve import LiveRule, live_rule_from_doc
from agentcore.workspace.protocol import GlobFilesQuery, NotADirectory, PathNotFound
from agentcore.workspace.stage_dirs import LEGACY_RULES_DIR_PATH, RULES_DIR_PATH

logger = get_logger(__name__)

_RULE_DISCOVERY_CAP = 200
_RULE_DIR_RELS = (RULES_DIR_PATH, LEGACY_RULES_DIR_PATH)

_reader: ContextVar[object | None] = ContextVar("workspace_markdown_reader", default=None)


def bind_workspace_markdown_reader(reader: object | None) -> Token[object | None]:
    return _reader.set(reader)


def reset_workspace_markdown_reader(token: Token[object | None]) -> None:
    _reader.reset(token)


def current_workspace_markdown_reader() -> object | None:
    return _reader.get()


@contextmanager
def workspace_markdown_reader_scope(reader: object | None) -> Iterator[None]:
    """Bind ``reader`` for the current task, if one was passed."""
    if reader is None:
        yield
        return
    token = bind_workspace_markdown_reader(reader)
    try:
        yield
    finally:
        reset_workspace_markdown_reader(token)


def normalize_markdown_path(raw: str) -> str | None:
    """Workspace-relative ``*.md`` path, or None when it is not a file path."""
    text = (raw or "").strip().replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    if not text or text.startswith("/") or text.lower().endswith("/"):
        return None
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    if not text.lower().endswith(".md"):
        return None
    return text


def is_workspace_rule_path(path: str) -> bool:
    """True for markdown under ``AgentCore/rules`` or leftover ``AgentCore/规则``."""
    text = normalize_markdown_path(path)
    if text is None:
        return False
    return any(text == rel or text.startswith(f"{rel}/") for rel in _RULE_DIR_RELS)


def consult_name_for_rule_path(path: str) -> str:
    """Catalog / consult name: path under the rules dir, without ``.md``."""
    text = (path or "").replace("\\", "/").strip()
    if text.lower().endswith(".md"):
        text = text[:-3]
    for rel in _RULE_DIR_RELS:
        prefix = f"{rel}/"
        if text.startswith(prefix):
            return text[len(prefix) :]
        if text == rel:
            return ""
    return text


def _join_under_dir(directory: str, path: str) -> str:
    text = path.replace("\\", "/").strip()
    if text.startswith("./"):
        text = text[2:]
    directory = directory.replace("\\", "/").rstrip("/")
    if text == directory or text.startswith(f"{directory}/"):
        return text
    if "/" not in text:
        return f"{directory}/{text}" if directory else text
    return text


async def _list_rule_paths(reader: object) -> list[str]:
    glob = getattr(reader, "glob_files", None)
    if glob is None:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for directory in _RULE_DIR_RELS:
        try:
            result = await glob(
                GlobFilesQuery(
                    directory=directory,
                    globs=("*.md", "**/*.md"),
                    max_entries=_RULE_DISCOVERY_CAP,
                )
            )
        except (PathNotFound, NotADirectory, FileNotFoundError):
            continue
        except OSError as exc:
            logger.info(
                "memory.workspace_rules_glob_failed",
                directory=directory,
                error=str(exc),
            )
            continue
        except Exception as exc:  # noqa: BLE001 - discovery must not break a turn
            logger.info(
                "memory.workspace_rules_glob_failed",
                directory=directory,
                error=str(exc),
            )
            continue
        if getattr(result, "truncated", False):
            logger.info(
                "memory.workspace_rules_truncated",
                directory=directory,
                cap=_RULE_DISCOVERY_CAP,
            )
        for item in getattr(result, "paths", []) or []:
            joined = _join_under_dir(directory, str(item))
            normalized = normalize_markdown_path(joined)
            if normalized is None or not is_workspace_rule_path(normalized):
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            found.append(normalized)
    return found


async def live_rule_from_workspace_file(
    reader: object, path: str
) -> LiveRule | None:
    """Read one convention-dir file. Frontmatter is the channel. Unreadable → None."""
    read = getattr(reader, "read", None)
    if read is None:
        return None
    name = consult_name_for_rule_path(path)
    if not name:
        return None
    try:
        text = await read(path)
    except Exception as exc:  # noqa: BLE001 - a missing file must not break the turn
        logger.info("memory.workspace_file_unreadable", path=path, error=str(exc))
        return None
    if not isinstance(text, str):
        return None
    parsed = parse_entry_frontmatter(text)
    if isinstance(parsed, FrontmatterError):
        return None
    apply = parsed.apply if parsed.apply_present else "on_demand"
    return live_rule_from_doc(
        {
            "name": f"{name}.md",
            "content": text,
            "description": parsed.description if parsed.description_present else "",
        },
        column_apply="",
        rank=1,
        apply_override=apply,
    )


async def discover_workspace_rules(reader: object | None) -> list[LiveRule]:
    """Live rules from the convention directory. No reader → empty."""
    if reader is None:
        return []
    out: list[LiveRule] = []
    for path in await _list_rule_paths(reader):
        live = await live_rule_from_workspace_file(reader, path)
        if live is not None:
            out.append(live)
    return out


def matching_workspace_rule(rules: Sequence[LiveRule], name: str) -> LiveRule | None:
    key = name.removesuffix(".md").strip()
    if not key:
        return None
    found = [row for row in rules if row.name == key]
    if not found:
        return None
    return found[-1]


async def refuse_always_rule_write(
    reader: object | None,
    path: str,
    new_content: str,
    *,
    old_content: str | None,
) -> str | None:
    """AI write refusal when ``AgentCore/rules`` 常驻 would grow past the cap.

    Meters files in the convention directory only. Toolbox documents keep
    their own write-side gate.
    """
    if reader is None or not is_workspace_rule_path(path):
        return None
    from agentcore.memory.always_quota import (
        AlwaysUsage,
        always_entry_chars,
        always_max_chars,
        evaluate_always_write,
    )
    from agentcore.memory.rule_resolve import counts_as_always_content

    cap = always_max_chars()
    if cap <= 0:
        return None
    new_is_always = counts_as_always_content(new_content)
    old_is_always = counts_as_always_content(old_content) if old_content else False
    if not new_is_always and not old_is_always:
        return None
    lives = await discover_workspace_rules(reader)
    this_name = consult_name_for_rule_path(path)
    current_used = 0
    projected_used = 0
    for live in lives:
        chars = (
            always_entry_chars(live.content)
            if counts_as_always_content(live.content)
            else 0
        )
        current_used += chars
        if live.name == this_name:
            continue
        projected_used += chars
    if new_is_always:
        projected_used += always_entry_chars(new_content)
    decision = evaluate_always_write(
        writer="ai",
        editing_existing_always=old_is_always,
        current_used=current_used,
        projected=AlwaysUsage(used_chars=projected_used, max_chars=cap),
    )
    if decision.allowed:
        return None
    return decision.message


