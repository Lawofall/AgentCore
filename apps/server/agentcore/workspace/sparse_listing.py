"""Sparse workspace file listing for CEO overview + worker manifests.

Default injection (双模式工作区 · 清单稀疏化):

- **附件·含历轮** — paths under ``attachments/`` (disk-resident across turns, not
  this-message-only)
- **本对话 scratch** — for 裸聊 the whole workspace *is* the scratch, so non-
  attachment files list normally (capped); project chats have no per-conv
  scratch under the shared folder, so scratch entries are empty here
- **同回合队友产出** — layered by the worker manifest (role-attributed); not
  handled in this module
- **项目共享其余文件** — never enumerated; one summary line with the count

Project mode never enumerates shared non-attachment paths — only a count
line — so a write does not restamp five mtime samples into the CEO prefix.
"""

from __future__ import annotations

from typing import Any

from agentcore.core.paths import is_absolute_os_path
from agentcore.workspace._paths import (
    is_ai_archive_file_name,
    is_ai_noise_file_name,
    is_system_ignored_file_name,
)
from agentcore.workspace.attachments import ATTACHMENTS_DIR
from agentcore.workspace.external_mounts import EXTERNAL_PREFIX

_MATERIAL_PATH_KEYS = ("workspace_path", "path", "parsed_workspace_path")


def is_attachment_path(path: str) -> bool:
    """Whether ``path`` lives under the resident ``attachments/`` directory."""
    p = path.replace("\\", "/").lstrip("./")
    return p == ATTACHMENTS_DIR or p.startswith(f"{ATTACHMENTS_DIR}/")


def is_external_ns_path(path: str) -> bool:
    """Whether ``path`` is under the model-facing ``external/<alias>/`` namespace."""
    p = path.replace("\\", "/").lstrip("./").strip("/")
    return p == EXTERNAL_PREFIX.rstrip("/") or p.startswith(EXTERNAL_PREFIX)


def _normalize_material_rel(raw: str) -> str | None:
    """Return a workspace-relative POSIX path, or None if unusable / absolute."""
    if is_absolute_os_path(raw):
        return None
    p = raw.replace("\\", "/").strip().lstrip("./")
    if not p or p == ".":
        return None
    if is_absolute_os_path(p):
        return None
    return p


def collect_turn_material_paths(attachments: list[dict[str, Any]] | None) -> frozenset[str]:
    """Workspace-relative paths from this turn's attachments (list AI-noise reveal).

    Collects ``workspace_path`` / relative ``path`` / ``parsed_workspace_path``.
    Skips ``resident_missing`` items and absolute OS paths. Empty when none.
    """
    if not attachments:
        return frozenset()
    out: set[str] = set()
    for att in attachments:
        if att.get("resident_missing"):
            continue
        for key in _MATERIAL_PATH_KEYS:
            raw = att.get(key)
            if not isinstance(raw, str) or not raw.strip():
                continue
            cleaned = _normalize_material_rel(raw)
            if cleaned is not None:
                out.add(cleaned)
    return frozenset(out)


def should_hide_ai_noise_from_list(
    path: str,
    *,
    materials: frozenset[str] | None = None,
    reveal_archives: bool = False,
) -> bool:
    """True when ``path`` is AI-noise and *not* under ``attachments/`` / materials.

    Used by ``file_list`` tool-layer filtering (``list`` is shared with UI and
    only strips system noise). Attachment zips/media and this-turn material
    paths stay visible to the agent; the same suffixes elsewhere remain hidden.
    Archives under ``external/<alias>/`` (区外用户目录) and when
    ``reveal_archives`` (pattern targets zip/rar/…) stay visible; workspace
    media/binaries stay hidden.
    """
    p = path.replace("\\", "/").lstrip("./")
    name = p.rsplit("/", 1)[-1] if p else ""
    if not name or not is_ai_noise_file_name(name):
        return False
    if is_attachment_path(p):
        return False
    if materials and p in materials:
        return False
    return not (
        is_ai_archive_file_name(name)
        and (reveal_archives or is_external_ns_path(p))
    )


def is_ai_list_hidden_file(
    *,
    parent_rel: str,
    name: str,
    materials: frozenset[str] | None = None,
    reveal_archives: bool = False,
) -> bool:
    """Whether a file child should be omitted from AI ``list_tree`` walks.

    System suffixes always hidden. AI-noise suffixes hidden unless the child
    path lives under ``attachments/``, is in ``materials``, or (archives only)
    under ``external/`` / ``reveal_archives``.
    """
    if is_system_ignored_file_name(name):
        return True
    if not is_ai_noise_file_name(name):
        return False
    parent = parent_rel.replace("\\", "/").strip("/")
    child = name if parent in ("", ".") else f"{parent}/{name}"
    if is_attachment_path(child):
        return False
    if materials and child in materials:
        return False
    return not (
        is_ai_archive_file_name(name)
        and (reveal_archives or is_external_ns_path(child))
    )


def partition_sparse_paths(
    index_paths: list[str],
    *,
    shared_workspace: bool,
) -> tuple[list[tuple[str, str]], int]:
    """Split an index into (labeled rows to list, remaining shared count).

    Each row is ``(path, label)``:

    - attachments → 「附件·含历轮」
    - bare-chat scratch → 「工作区已有」
    - ``remaining`` is the count of shared project files *not* listed (0 for 裸聊)
    """
    attachments: list[str] = []
    others: list[str] = []
    for path in index_paths:
        if is_attachment_path(path):
            attachments.append(path)
        else:
            others.append(path)

    rows: list[tuple[str, str]] = [(p, "附件·含历轮") for p in attachments]

    if not shared_workspace:
        rows.extend((p, "工作区已有") for p in others)
        return rows, 0

    # Project shared space: attachments only; the rest collapse into the summary.
    return rows, len(others)


def format_remaining_summary(remaining: int) -> str:
    """One-line elision for shared project files not listed individually."""
    return f"另有 {remaining} 个文件，需要时用 file_list / grep"
