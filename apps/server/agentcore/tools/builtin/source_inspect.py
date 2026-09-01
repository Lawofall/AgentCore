"""Short-exec dump/grep → ``file_read`` / ``grep`` when the snippet is browsing source.

Same shape as :mod:`project_verify` (slow CLI → ``run``) and :mod:`long_running`
(dev servers → ``run`` + ``background``): the file tools already exist for this
job. Patterns are snippet-shaped so table parsing, AST analysis, and
read-then-write transforms do not false-positive.
"""

from __future__ import annotations

import re
from typing import Literal, NamedTuple

SourceInspectKind = Literal["dump", "grep"]


class SourceInspectHit(NamedTuple):
    kind: SourceInspectKind
    matched: str


_SOURCE_PATH = re.compile(
    r"""['"][^'"]+\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|go|rs|java|kt|c|h|cpp|cc|hpp|"""
    r"""cs|rb|php|swift|md|mdc|toml|ya?ml|css|scss|html|vue|svelte)['"]""",
    re.IGNORECASE,
)

_READ_CALL = re.compile(
    r"\bopen\s*\(|(?:pathlib\.)?Path\s*\([^)]*\)\.read_text\s*\(|\.read_bytes\s*\(",
    re.IGNORECASE,
)

_RE_SCAN = re.compile(r"\bre\.(?:findall|finditer|search|match|compile)\s*\(")

# Mode is a *second* argument (`open(path, 'w')`), not the first letter of the path
# (`open('apps/foo.py')` used to false-positive as append).
_WRITE = re.compile(
    r"""open\s*\([^)]*,\s*['"](?:w|a|x|r\+)['"]"""
    r"""|open\s*\([^)]*mode\s*=\s*['"](?:w|a|x|r\+)"""
    r"""|\.write_text\s*\(|\.write_bytes\s*\(|\.write\s*\("""
    r"""|\bto_csv\s*\(|\bto_excel\s*\(|\bto_parquet\s*\(""",
    re.IGNORECASE,
)

_DATA_LIBS = re.compile(r"\b(?:pandas|openpyxl|xlrd|csv\.reader|load_workbook)\b")

_DUMP_DIRECT = re.compile(
    r"(?:print|sys\.stdout\.write)\s*\(\s*"
    r"(?:"
    r"open\s*\([^)]+\)\.read\s*\(\s*\)(?:\s*\[\s*:\s*\d+\s*\])?"
    r"|"
    r"(?:pathlib\.)?Path\s*\([^)]+\)\.read_text\s*\([^)]*\)(?:\s*\[\s*:\s*\d+\s*\])?"
    r")",
    re.IGNORECASE,
)

_DUMP_HANDLE_READ = re.compile(
    r"(?:print|sys\.stdout\.write)\s*\(\s*\w+\.read\s*\(\s*\)(?:\s*\[\s*:\s*\d+\s*\])?",
    re.IGNORECASE,
)

_ASSIGN_READ = re.compile(
    r"(?P<name>[A-Za-z_]\w*)\s*=\s*"
    r"(?:"
    r"open\s*\([^)]+\)\.read\s*\(\s*\)"
    r"|"
    r"(?:pathlib\.)?Path\s*\([^)]+\)\.read_text\s*\([^)]*\)"
    r")",
    re.IGNORECASE,
)

_WITH_OPEN = re.compile(r"with\s+open\s*\([^)]*\)\s+as\s+(?P<fh>\w+)", re.IGNORECASE)

_ASSIGN_FROM_HANDLE = re.compile(
    r"(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<fh>\w+)\.read\s*\(\s*\)",
    re.IGNORECASE,
)


def _clip(snippet: str, limit: int = 80) -> str:
    compact = " ".join(snippet.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _has_workspace_write(code: str) -> bool:
    return _WRITE.search(code) is not None


def _assigned_read_names(code: str) -> list[str]:
    names = [m.group("name") for m in _ASSIGN_READ.finditer(code)]
    handles = {m.group("fh") for m in _WITH_OPEN.finditer(code)}
    for m in _ASSIGN_FROM_HANDLE.finditer(code):
        if m.group("fh") in handles:
            names.append(m.group("name"))
    return names


def _dump_via_assigned_name(code: str) -> str | None:
    for name in _assigned_read_names(code):
        found = re.search(
            rf"\bprint\s*\(\s*{re.escape(name)}\s*(?:\[\s*:\s*\d+\s*\])?\s*\)",
            code,
        )
        if found is not None:
            return found.group(0)
    return None


def _dump_match(code: str) -> str | None:
    """Return the dump snippet, or None. Skips transforms and table-parse scripts."""
    if _has_workspace_write(code) or _DATA_LIBS.search(code) is not None:
        return None
    direct = _DUMP_DIRECT.search(code)
    if direct is not None:
        return direct.group(0)
    if _READ_CALL.search(code) is not None:
        handle = _DUMP_HANDLE_READ.search(code)
        if handle is not None:
            return handle.group(0)
    return _dump_via_assigned_name(code)


def _grep_match(code: str) -> str | None:
    """Source-file open + regex scan, without write / table libs."""
    if _has_workspace_write(code) or _DATA_LIBS.search(code) is not None:
        return None
    if _SOURCE_PATH.search(code) is None:
        return None
    if _READ_CALL.search(code) is None and _WITH_OPEN.search(code) is None:
        return None
    found = _RE_SCAN.search(code)
    if found is None:
        return None
    return found.group(0)


def source_inspect_match(code: str) -> SourceInspectHit | None:
    """Return dump/grep hit, or None if the snippet looks like real computation."""
    dump = _dump_match(code)
    if dump is not None:
        return SourceInspectHit("dump", _clip(dump))
    grep = _grep_match(code)
    if grep is not None:
        return SourceInspectHit("grep", _clip(grep))
    return None


def source_inspect_redirect_message(hit: SourceInspectHit) -> str:
    """Short-path refusal: tip the file tool without running the snippet."""
    if hit.kind == "dump":
        return (
            f"把工作区文件 dump 到 stdout 请用 file_read（检测到：{hit.matched}）。"
            "可分页；定位或计数请用 grep / code_search。"
            "解析表格、改文件、跑计算仍用 run。"
        )
    return (
        f"打开源码再正则扫描请用 grep（检测到：{hit.matched}）。"
        "在工作区搜符号、字符串或计数请用 grep；概念定位用 code_search；"
        "看命中正文用 file_read。"
        "解析表格、改文件、对内存数据跑计算仍用 run。"
    )
