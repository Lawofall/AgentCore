"""ToolRegistry: registration and query of available tools.

Manages all registered tools and provides lookup by name or face.
Also converts tool schemas to LLM function calling format.
"""

from __future__ import annotations

import difflib

from agentcore.core.errors import ToolNotFoundError
from agentcore.core.types import ToolFace
from agentcore.tools.protocol import Tool, ToolSchema, tool_schema_to_openai_format

# Common model hallucinations → canonical tool name. Only surface when the target
# is actually registered (did-you-mean message only — never auto-rewrite / execute).
_KNOWN_TOOL_ALIASES: dict[str, str] = {
    # Unix fetch/wget/curl → run (not HTML deep-read).
    "fetch": "run",
    "wget": "run",
    "curl": "run",
    "file_append": "edit",
    "search": "web_search",
    "websearch": "web_search",
    "google": "web_search",
    "content": "read",
    "replace": "edit",
    "str_replace_editor": "edit",
    "bash": "run",
    "shell": "run",
    "git": "run",
    "ls": "file_list",
    "list_dir": "file_list",
    "glob_file_search": "glob",
    "find": "glob",
    "delete": "file_delete",
    "rm": "file_delete",
    "mv": "file_batch",
    "cp": "file_batch",
}


class ToolRegistry:
    """Central registry for all available tools."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool. Raises ValueError if name already registered."""
        name = tool.schema.name
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered")
        self._tools[name] = tool

    def unregister(self, name: str) -> None:
        """Remove a tool by name. No-op when not registered."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool:
        """Get a tool by name. Raises ToolNotFoundError if not found."""
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(f"工具 '{name}' 不存在")
        return tool

    def get_optional(self, name: str) -> Tool | None:
        """Get a tool by name, returning None if not found."""
        return self._tools.get(name)

    def suggest_names(self, name: str, *, n: int = 3, cutoff: float = 0.5) -> list[str]:
        """Did-you-mean candidates for a missing tool name (alias map + close matches).

        Returns at most ``n`` registered names. Empty when nothing is close enough.
        User-side export names never suggest a replacement. Never rewrites or
        executes — callers only embed these in error feedback.
        """
        if not name or n < 1:
            return []
        key = name.strip().lower().replace("-", "_")
        from agentcore.tools.retired import USER_SIDE_EXPORT_NAMES

        if key in USER_SIDE_EXPORT_NAMES or name in USER_SIDE_EXPORT_NAMES:
            return []
        out: list[str] = []
        alias = _KNOWN_TOOL_ALIASES.get(key)
        if alias and alias in self._tools:
            # Exact known hallucination → surface only the canonical name (avoid
            # noisy near-misses like web_read also matching web_search).
            return [alias]
        for match in difflib.get_close_matches(name, self.names, n=n, cutoff=cutoff):
            if match not in out:
                out.append(match)
            if len(out) >= n:
                break
        return out[:n]

    def list_all(self) -> list[ToolSchema]:
        """Return schemas of all registered tools."""
        return [tool.schema for tool in self._tools.values()]

    def list_by_face(self, face: ToolFace) -> list[ToolSchema]:
        """Return schemas of tools with a given capability face."""
        return [tool.schema for tool in self._tools.values() if tool.schema.face == face]

    def get_openai_definitions(self, tool_names: list[str] | None = None) -> list[dict]:
        """Return tool definitions in OpenAI function calling format.

        Every registered tool is on the opening table. ``tool_names`` still
        filters opening allow-lists. ``list_all`` / ``names`` include the
        full registry (catalog, execute, skill gates).
        """
        if tool_names is None:
            names = list(self._tools)
        else:
            names = [n for n in tool_names if n in self._tools]
        return [tool_schema_to_openai_format(self._tools[n].schema) for n in names]

    @property
    def count(self) -> int:
        return len(self._tools)

    @property
    def names(self) -> list[str]:
        """Registered tool names (registration order)."""
        return list(self._tools.keys())
