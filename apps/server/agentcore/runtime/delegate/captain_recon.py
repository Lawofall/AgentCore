"""B2 · 主管探路下传：把 CEO 本回合已看过的路径/短摘要注入 worker 开局。

产品目标：派人后工人不要从零再 list 根目录 / 通读主管刚读过的文件。
收 ``file_list`` / ``file_read`` / ``grep`` / ``code_search``；指针 + 短截断，
不转发全文 transcript。保活按路径/工具类型筛选：优先检索命中，丢掉生成物、
``release/``、工作区噪音目录、``.env*``，避免低信号条目挤掉定位。
仅在根 CEO 委派（depth=0）时启用——嵌套 lead 的 transcript 不在
``captain_transcript``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agentcore.llm.provider.protocol import LLMMessage, llm_content_text
from agentcore.workspace._paths import is_ignored_dir_name, is_ignored_relpath

_RECON_TOOLS = frozenset({"file_list", "file_read", "grep", "code_search"})
_SEARCH_TOOLS = frozenset({"grep", "code_search"})
_MAX_ENTRIES = 8
_PER_SNIPPET_CHARS = 360
_TOTAL_CHARS = 2400
# ``release`` is a publish/artifact tree, not in workspace IGNORED_DIRS (those
# stay hidden from AI listings). Harvest still treats it as low-signal.
_LOW_SIGNAL_DIR_NAMES = frozenset({"release"})
_SOURCE_SUFFIXES = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".rb",
        ".php",
        ".swift",
        ".vue",
        ".svelte",
    }
)

_HEADING_HINT = (
    "主管探路已知（本回合已看过——请直接执行任务；"
    "勿再无增量地 list 根目录或通读上列文件；缺细节再定点读）"
)


@dataclass(frozen=True, slots=True)
class _ReconEntry:
    name: str
    label: str
    path: str
    snippet: str
    seq: int
    rank: int

    def line(self) -> str:
        return f"- `{self.name}` `{self.label}` →\n{self.snippet}"


def harvest_captain_recon(
    messages: list[LLMMessage] | None,
    *,
    max_entries: int = _MAX_ENTRIES,
    per_snippet_chars: int = _PER_SNIPPET_CHARS,
    total_chars: int = _TOTAL_CHARS,
) -> str:
    """Build a short recon brief from the live CEO transcript, or ``\"\"`` if none."""
    if not messages:
        return ""
    pending: dict[str, tuple[str, str, str]] = {}
    entries: list[_ReconEntry] = []
    for msg in messages:
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                name = (tc.function.name or "").strip()
                if name not in _RECON_TOOLS or not tc.id:
                    continue
                label, path = _target_info(name, tc.function.arguments or "")
                pending[tc.id] = (name, label, path)
            continue
        if msg.role != "tool" or not msg.tool_call_id:
            continue
        meta = pending.pop(msg.tool_call_id, None)
        if meta is None:
            continue
        name, label, path = meta
        body = llm_content_text(msg.content).strip()
        if not body:
            continue
        # Skip hard failures — no useful recon to hand down.
        if "<!--agentcore:tool_failed-->" in body or body.startswith("错误"):
            continue
        rank = _entry_rank(name, path)
        if rank < 0:
            continue
        snippet = _clip(body, per_snippet_chars)
        entries.append(
            _ReconEntry(
                name=name,
                label=label,
                path=path,
                snippet=snippet,
                seq=len(entries),
                rank=rank,
            )
        )
    if not entries:
        return ""
    picked = _select_entries(entries, max_entries=max_entries, total_chars=total_chars)
    if not picked:
        return ""
    text = "\n".join(e.line() for e in picked)
    if len(text) > total_chars:
        text = text[: total_chars - 1].rstrip() + "…"
    return text


def resolve_captain_recon_for_delegate(*, depth: int) -> str:
    """Read live CEO transcript when this is a root delegate; else empty."""
    if int(depth or 0) > 0:
        return ""
    try:
        from agentcore.runtime.suspension import captain_transcript

        return harvest_captain_recon(captain_transcript.get())
    except Exception:  # noqa: BLE001 — recon is best-effort
        return ""


def _target_info(tool_name: str, raw_args: str) -> tuple[str, str]:
    try:
        args: Any = json.loads(raw_args) if raw_args else {}
    except (TypeError, ValueError):
        args = {}
    if not isinstance(args, dict):
        return "?", ""
    path = _target_path(tool_name, args)
    if tool_name == "file_read":
        return (path or "?"), path
    if tool_name == "file_list":
        directory = path or "."
        pattern = str(args.get("pattern") or "*").strip() or "*"
        return f"{directory} ({pattern})", directory
    query = str(args.get("pattern") or args.get("query") or "").strip()
    label = f"{path} ~ {query}" if query else path
    return label, path


def _target_path(tool_name: str, args: dict[str, Any]) -> str:
    if tool_name == "file_list":
        return str(args.get("directory") or ".").strip() or "."
    if tool_name == "code_search":
        return str(args.get("path_prefix") or ".").strip() or "."
    return str(args.get("path") or args.get("directory") or ".").strip() or "."


def _entry_rank(tool_name: str, path: str) -> int:
    if _is_low_signal_path(path):
        return -1
    if tool_name in _SEARCH_TOOLS:
        return 3
    if tool_name == "file_read" and _is_source_path(path):
        return 2
    return 1


def _path_parts(path: str) -> list[str]:
    return [p for p in path.replace("\\", "/").split("/") if p and p != "."]


def _is_generated_filename(name: str) -> bool:
    lower = name.lower()
    return ".generated." in lower or lower.endswith(".generated")


def _is_env_filename(name: str) -> bool:
    return name == ".env" or name.startswith(".env.")


def _is_low_signal_path(path: str) -> bool:
    """True for generated / release / workspace-noise / ``.env*`` targets."""
    normalized = path.replace("\\", "/").strip()
    if not normalized or normalized == ".":
        return False
    if is_ignored_relpath(normalized):
        return True
    parts = _path_parts(normalized)
    if any(is_ignored_dir_name(p) or p.lower() in _LOW_SIGNAL_DIR_NAMES for p in parts):
        return True
    name = parts[-1] if parts else ""
    return _is_generated_filename(name) or _is_env_filename(name)


def _is_source_path(path: str) -> bool:
    parts = _path_parts(path)
    if not parts:
        return False
    lower = parts[-1].lower()
    return any(lower.endswith(suf) for suf in _SOURCE_SUFFIXES)


def _joined_len(entries: list[_ReconEntry]) -> int:
    if not entries:
        return 0
    return sum(len(e.line()) for e in entries) + (len(entries) - 1)


def _select_entries(
    entries: list[_ReconEntry],
    *,
    max_entries: int,
    total_chars: int,
) -> list[_ReconEntry]:
    ranked = sorted(entries, key=lambda e: (e.rank, e.seq), reverse=True)
    picked = ranked[: max(0, max_entries)]
    while len(picked) > 1 and _joined_len(picked) > total_chars:
        non_search = [e for e in picked if e.name not in _SEARCH_TOOLS]
        pool = non_search or picked
        victim = min(pool, key=lambda e: (e.rank, e.seq))
        picked.remove(victim)
    picked.sort(key=lambda e: e.seq)
    return picked


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def captain_recon_heading() -> str:
    """Opening-block heading (stable for tests / UI)."""
    return _HEADING_HINT
