"""Turn action inventory — files / commands / searches harvested from turn_journal.

Feeds episodic summarization (so session digests can retain verified folder ops
knowledge) and the semantic navigation anti-hallucination gate. Not a retrieval
index: compact, deterministic extraction only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from agentcore.core.secrets import redact_secrets

# Tool names that contribute paths / commands / searches.
_READ_TOOLS = frozenset({"file_read"})
_WRITE_TOOLS = frozenset({"file_write", "file_append", "str_replace"})
_COMMAND_TOOLS = frozenset({"run", "host", "terminal", "test_run"})
_SEARCH_TOOLS = frozenset({"grep", "code_search"})

# Grep / code_search hit line → leading path.
_GREP_HIT_RE = re.compile(r"^([^:\n]+):\d+")
_CODE_SEARCH_HIT_RE = re.compile(r"^([^:\n]+):\d+-\d+")

_MAX_PATHS = 40
_MAX_COMMANDS = 24
_MAX_SEARCHES = 20
_MAX_HITS_PER_SEARCH = 8
_MAX_RESULT_SCAN_CHARS = 4_000


@dataclass
class SearchAction:
    """One search tool call: query/pattern + hit paths (when result present)."""

    query: str
    hits: list[str] = field(default_factory=list)
    tool: str = "grep"


@dataclass
class TurnActionInventory:
    """Deduped, secret-redacted action list for one conversation window."""

    files_read: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    searches: list[SearchAction] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.files_read or self.files_written or self.commands or self.searches
        )

    def all_paths(self) -> set[str]:
        out: set[str] = set(self.files_read)
        out.update(self.files_written)
        for s in self.searches:
            out.update(s.hits)
        return {p for p in (_norm_path(x) for x in out) if p}

    def all_commands(self) -> set[str]:
        return {c for c in (c.strip() for c in self.commands) if c}

    def to_json_obj(self) -> dict[str, Any]:
        return {
            "files_read": list(self.files_read),
            "files_written": list(self.files_written),
            "commands": list(self.commands),
            "searches": [
                {"query": s.query, "hits": list(s.hits), "tool": s.tool}
                for s in self.searches
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_json_obj(), ensure_ascii=False, separators=(",", ":"))


def inventory_from_json(raw: str | None) -> TurnActionInventory:
    """Parse a stored actions JSON blob; empty/invalid → empty inventory."""
    if not raw or not str(raw).strip():
        return TurnActionInventory()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return TurnActionInventory()
    if not isinstance(data, dict):
        return TurnActionInventory()
    searches: list[SearchAction] = []
    for item in data.get("searches") or []:
        if not isinstance(item, dict):
            continue
        q = str(item.get("query") or "").strip()
        if not q:
            continue
        hits = [
            _norm_path(h)
            for h in (item.get("hits") or [])
            if isinstance(h, str) and _norm_path(h)
        ]
        searches.append(
            SearchAction(
                query=q,
                hits=hits[:_MAX_HITS_PER_SEARCH],
                tool=str(item.get("tool") or "grep"),
            )
        )
    return TurnActionInventory(
        files_read=_dedupe_paths(data.get("files_read") or []),
        files_written=_dedupe_paths(data.get("files_written") or []),
        commands=_dedupe_cmds(data.get("commands") or []),
        searches=searches[:_MAX_SEARCHES],
    )


def merge_inventories(parts: Sequence[TurnActionInventory]) -> TurnActionInventory:
    """Union several turn inventories (conversation window)."""
    acc = TurnActionInventory()
    for part in parts:
        acc.files_read = _dedupe_paths([*acc.files_read, *part.files_read])
        acc.files_written = _dedupe_paths([*acc.files_written, *part.files_written])
        acc.commands = _dedupe_cmds([*acc.commands, *part.commands])
        # Searches: keep order, dedupe by (tool, query).
        seen = {(s.tool, s.query) for s in acc.searches}
        for s in part.searches:
            key = (s.tool, s.query)
            if key in seen:
                # Merge hits into existing.
                for existing in acc.searches:
                    if (existing.tool, existing.query) == key:
                        existing.hits = _dedupe_paths([*existing.hits, *s.hits])[
                            :_MAX_HITS_PER_SEARCH
                        ]
                        break
                continue
            seen.add(key)
            acc.searches.append(s)
    # Re-apply caps: the union of several turns can exceed any single turn's budget.
    acc.files_read = acc.files_read[:_MAX_PATHS]
    acc.files_written = acc.files_written[:_MAX_PATHS]
    acc.commands = acc.commands[:_MAX_COMMANDS]
    acc.searches = acc.searches[:_MAX_SEARCHES]
    return acc


def inventory_from_journal_entries(
    entries: Sequence[Mapping[str, Any]] | None,
) -> TurnActionInventory:
    """Harvest actions from one turn's journal facts (process + tool_use_start)."""
    if not entries:
        return TurnActionInventory()
    files_read: list[str] = []
    files_written: list[str] = []
    commands: list[str] = []
    searches: list[SearchAction] = []

    for entry in entries:
        kind = str(entry.get("kind") or entry.get("type") or "")
        payload = entry.get("payload")
        if not isinstance(payload, Mapping):
            continue
        tool_name, arguments, result, status = _tool_fields(kind, payload)
        if not tool_name:
            continue
        _absorb_tool(
            tool_name=tool_name,
            arguments=arguments,
            result=result,
            status=status,
            files_read=files_read,
            files_written=files_written,
            commands=commands,
            searches=searches,
        )

    return TurnActionInventory(
        files_read=_dedupe_paths(files_read)[:_MAX_PATHS],
        files_written=_dedupe_paths(files_written)[:_MAX_PATHS],
        commands=_dedupe_cmds(commands)[:_MAX_COMMANDS],
        searches=searches[:_MAX_SEARCHES],
    )


def render_action_inventory_for_prompt(inv: TurnActionInventory) -> str:
    """Compact markdown block for the episodic summarizer user prompt."""
    if inv.is_empty():
        return "(none — chat-only / no file·command·search tools this window)"
    lines: list[str] = []
    if inv.files_read:
        lines.append("files_read: " + ", ".join(inv.files_read))
    if inv.files_written:
        lines.append("files_written: " + ", ".join(inv.files_written))
    if inv.commands:
        lines.append("commands: " + " | ".join(inv.commands))
    for s in inv.searches:
        hits = ", ".join(s.hits) if s.hits else "(no hits recorded)"
        lines.append(f"search[{s.tool}] {s.query!r} → {hits}")
    return "\n".join(lines)


def _tool_fields(
    kind: str, payload: Mapping[str, Any]
) -> tuple[str, dict[str, Any], str | None, str | None]:
    """Normalize process_tool / run_process_tool / tool_use_start shapes."""

    def _args() -> dict[str, Any]:
        raw = payload.get("arguments")
        return raw if isinstance(raw, dict) else {}

    if kind in ("process_tool", "run_process_tool") or (
        kind.startswith("run_process_") and payload.get("kind") == "tool"
    ):
        name = str(payload.get("tool_name") or "").strip()
        result = payload.get("result")
        status = str(payload.get("status") or "") or None
        return name, _args(), str(result) if result is not None else None, status
    if kind == "tool_use_start":
        name = str(payload.get("tool_name") or "").strip()
        return name, _args(), None, None
    # Raw process step embedded without prefix (defensive).
    if payload.get("kind") == "tool":
        name = str(payload.get("tool_name") or "").strip()
        result = payload.get("result")
        status = str(payload.get("status") or "") or None
        return name, _args(), str(result) if result is not None else None, status
    return "", {}, None, None


def _absorb_tool(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    result: str | None,
    status: str | None,
    files_read: list[str],
    files_written: list[str],
    commands: list[str],
    searches: list[SearchAction],
) -> None:
    # Skip hard failures when we know status; still keep args-only starts.
    if status == "error":
        return
    if tool_name in _READ_TOOLS:
        path = _norm_path(arguments.get("path"))
        if path:
            files_read.append(path)
        return
    if tool_name in _WRITE_TOOLS:
        path = _norm_path(arguments.get("path"))
        if path:
            files_written.append(path)
        return
    if tool_name in _COMMAND_TOOLS:
        cmd = _extract_command(tool_name, arguments)
        if cmd:
            commands.append(redact_secrets(cmd))
        return
    if tool_name in _SEARCH_TOOLS:
        query = _extract_search_query(tool_name, arguments)
        if not query:
            return
        hits = _extract_search_hits(tool_name, result) if result else []
        searches.append(
            SearchAction(
                query=query[:200],
                hits=hits[:_MAX_HITS_PER_SEARCH],
                tool=tool_name,
            )
        )


def _extract_command(tool_name: str, arguments: dict[str, Any]) -> str:
    if tool_name == "run":
        action = str(arguments.get("action") or "").strip().lower()
        if action in {"read", "stop", "list"}:
            return ""
        return str(arguments.get("command") or "").strip()
    if tool_name == "terminal":
        sub = str(arguments.get("subcommand") or "").strip().lower()
        if sub and sub != "start":
            return ""
        return str(arguments.get("command") or "").strip()
    if tool_name == "host":
        if str(arguments.get("action") or "").strip().lower() != "shell":
            return ""
        return str(arguments.get("command") or "").strip()
    if tool_name == "test_run":
        check = str(arguments.get("check") or "test").strip()
        if check == "command":
            return str(arguments.get("command") or "").strip()
        # Record the check kind as a stable ops pointer (e.g. "test_run:build").
        wd = str(arguments.get("working_directory") or "").strip()
        label = f"test_run:{check}"
        return f"{label} cwd={wd}" if wd else label
    return ""


def _extract_search_query(tool_name: str, arguments: dict[str, Any]) -> str:
    if tool_name == "grep":
        return str(arguments.get("pattern") or "").strip()
    if tool_name == "code_search":
        return str(arguments.get("query") or "").strip()
    return ""


def _extract_search_hits(tool_name: str, result: str) -> list[str]:
    text = result[:_MAX_RESULT_SCAN_CHARS]
    hits: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = (
            _CODE_SEARCH_HIT_RE.match(line)
            if tool_name == "code_search"
            else _GREP_HIT_RE.match(line)
        )
        if not m:
            continue
        path = _norm_path(m.group(1))
        if path:
            hits.append(path)
        if len(hits) >= _MAX_HITS_PER_SEARCH:
            break
    return _dedupe_paths(hits)


def _norm_path(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    text = raw.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.strip()


def _dedupe_paths(items: Sequence[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        p = _norm_path(item)
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _dedupe_cmds(items: Sequence[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        c = redact_secrets(item.strip())
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out
