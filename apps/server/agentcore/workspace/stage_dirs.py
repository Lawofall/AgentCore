"""Workspace layout constants (``.agentcore`` on disk is ``AgentCore/``).

产品活柜只认 ``AgentCore/`` 根（``rules/`` / 内部区）。``文档/`` 仅存量盘路径，
不再有工作稿 / research / debate / reviews 活子柜。存量 ``记忆/`` 当普通子树，不进模型。

同树旁路（系统噪音，对 AI 与用户文件 UI 都隐藏；**不**注入）::

    AgentCore/{index,trash,baselines,versions}/

勿与容器路径 ``~/Documents/AgentCore/`` 混淆。禁止把裸名 ``index``/``trash``/
``baselines``/``versions`` 放进全局忽略集（误伤用户项目）——须路径感知（见
``_paths.is_internal_zone_relpath``）。

区名集与 ``*_REL`` 在桌面端另有两份手抄（``main/fs/workspaceIgnore.ts`` 与渲染层
``services/sources/workspaceSource.ts`` 内联副本）。新增区名须三处同改，门禁
（漏改任一处必红）::

    uv run python scripts/check_workspace_ignore_parity.py
"""

from __future__ import annotations

from pathlib import Path

AGENTCORE_ROOT = "GAgent"
# One-shot rename source. Not a second live root.
LEGACY_AGENTCORE_ROOT = "AgentCore"
# Visible leftover leaf under AgentCore/. Keep in sync with documents.RULES_DIR_NAME
# (db layer stays db-only — no import from this module).
RULES_DIR_NAME = "rules"
LEGACY_RULES_DIR_NAME = "规则"
# 存量盘路径 / 迁记忆脚本；不是活的产物子柜。
DOCS_DIR_NAME = "文档"

# Machine-readable bypass under the same AgentCore/ root (system noise).
INDEX_ZONE_NAME = "index"
TRASH_ZONE_NAME = "trash"
BASELINES_ZONE_NAME = "baselines"
# User-named local versions (``versions/<version_id>/{meta.json,content.zip}``) —
# the local twin of cloud labeled snapshots. Internal for the same reason as
# ``baselines``: the zips are product plumbing, not user files, so grep / index /
# the next turn baseline must not see them.
VERSIONS_ZONE_NAME = "versions"
INTERNAL_ZONE_NAMES: frozenset[str] = frozenset(
    {INDEX_ZONE_NAME, TRASH_ZONE_NAME, BASELINES_ZONE_NAME, VERSIONS_ZONE_NAME}
)
# In-tree relative form. Still the layout for local / sidecar roots and shared
# spaces, and still what the desktop mirror (``fs/workspaceIgnore.ts``) hides.
# Cloud conversation workspaces keep these zones OUT of the tree — see
# ``internal_zone_base`` and ``locate.workspace_internal_root``.
INDEX_REL = f"{AGENTCORE_ROOT}/{INDEX_ZONE_NAME}"
TRASH_REL = f"{AGENTCORE_ROOT}/{TRASH_ZONE_NAME}"
BASELINES_REL = f"{AGENTCORE_ROOT}/{BASELINES_ZONE_NAME}"
VERSIONS_REL = f"{AGENTCORE_ROOT}/{VERSIONS_ZONE_NAME}"

# Retired 四格活柜 names — ``_paths`` still flattens nested writes under these prefixes.
DRAFTS_PREFIX = "工作稿/"
RESEARCH_PREFIX = "research/"
REVIEWS_PREFIX = "reviews/"
DEBATE_PREFIX = "debate/"


def adopt_legacy_convention_root(workspace_root: Path) -> bool:
    """Rename ``AgentCore/`` → ``GAgent/`` when the new root is absent.

    Both present → leave the old directory alone. Not a merge.
    """
    new = workspace_root / AGENTCORE_ROOT
    old = workspace_root / LEGACY_AGENTCORE_ROOT
    if new.exists() or not old.is_dir():
        return False
    try:
        old.rename(new)
    except OSError:
        return False
    return True


def internal_zone_base(*, root: Path, internal_root: Path | None) -> Path:
    """Directory holding ``{index,trash,baselines}`` for one workspace root.

    ``internal_root=None`` means **in-tree** (``<root>/AgentCore/``): correct for
    backends whose root cannot have another folder nested inside it — local /
    sidecar (the root *is* the user's own directory, and desktop restore reads
    ``AgentCore/trash`` there) and shared spaces (flat namespace). Cloud
    conversation workspaces pass an explicit out-of-tree path because cloud
    folders nest for real (双模式工作区 §5.4).
    """
    if internal_root is not None:
        return internal_root
    return root / AGENTCORE_ROOT


def internal_zone_path(
    zone_name: str, *, root: Path, internal_root: Path | None
) -> Path:
    """One zone directory (``index`` / ``trash`` / ``baselines``) for a root."""
    return internal_zone_base(root=root, internal_root=internal_root) / zone_name


def rename_legacy_rules_leaf(workspace_root: Path) -> bool:
    """One-shot disk rename ``GAgent/规则`` → ``GAgent/rules``.

    Adopts a leftover ``AgentCore/`` root first. Not a dual-path overlay: after
    this, the old leaf name is just gone. If ``rules/`` already exists, leftover
    children that do not clash are moved across and an empty ``规则/`` is removed.
    """
    adopt_legacy_convention_root(workspace_root)
    src = workspace_root / AGENTCORE_ROOT / LEGACY_RULES_DIR_NAME
    dst = workspace_root / AGENTCORE_ROOT / RULES_DIR_NAME
    if not src.is_dir():
        return False
    if not dst.exists():
        src.rename(dst)
        return True
    try:
        for child in src.iterdir():
            dest_child = dst / child.name
            if dest_child.exists():
                continue
            child.rename(dest_child)
        if not any(src.iterdir()):
            src.rmdir()
    except OSError:
        return False
    return True


__all__ = [
    "AGENTCORE_ROOT",
    "RULES_DIR_NAME",
    "LEGACY_RULES_DIR_NAME",
    "DOCS_DIR_NAME",
    "INTERNAL_ZONE_NAMES",
    "INDEX_ZONE_NAME",
    "TRASH_ZONE_NAME",
    "BASELINES_ZONE_NAME",
    "VERSIONS_ZONE_NAME",
    "INDEX_REL",
    "TRASH_REL",
    "BASELINES_REL",
    "VERSIONS_REL",
    "DRAFTS_PREFIX",
    "RESEARCH_PREFIX",
    "REVIEWS_PREFIX",
    "DEBATE_PREFIX",
    "internal_zone_base",
    "internal_zone_path",
    "rename_legacy_rules_leaf",
]
