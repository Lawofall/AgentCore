"""阶段产物（约定文档）目录约定 —— 后端单一权威源。

工作区相对路径：``AgentCore/文档/{工作稿,research,debate,reviews}/``。
``工作稿`` 是过程稿抽屉：裸文件名 join 到这里，不知放哪的产物也落这里；
空 ``artifacts`` 不钉此目录。运行时不再从 role/task 自由文猜「像调研还是像审查」
（见双模式工作区 §四）。``research`` 有机器语义（辩手读它取证），只接
playbook 常量或显式声明的调研产物。
仅工作区盘；**永不**进 documents / ``<设定>`` 注入（见记忆 §5.0）。
``文档/`` 是**纯产物目录**：厚约定文档不再落盘，改写 documents 条目
（挂原文件夹、生效档按需），存量由 ``memory/migrate_project_docs.py`` 一次性读入。
开发期直切，无根级旧路径兼容。

同树旁路（系统噪音，对 AI 与用户文件 UI 都隐藏；**不**注入）::

    AgentCore/{index,trash,baselines,versions}/

与可见 ``规则/`` · ``记忆/`` · ``文档/`` 同根；勿与容器路径
``~/Documents/AgentCore/`` 混淆。禁止把裸名 ``index``/``trash``/``baselines``/
``versions`` 放进全局忽略集（误伤用户项目）——须路径感知（见
``_paths.is_internal_zone_relpath``）。

区名集与 ``*_REL`` 在桌面端另有两份手抄（``main/fs/workspaceIgnore.ts`` 与渲染层
``services/sources/workspaceSource.ts`` 内联副本）。新增区名须三处同改，门禁
（漏改任一处必红）::

    uv run python scripts/check_workspace_ignore_parity.py
"""

from __future__ import annotations

from pathlib import Path

AGENTCORE_ROOT = "AgentCore"
DOCS_DIR_NAME = "文档"
DOCS_PREFIX = f"{AGENTCORE_ROOT}/{DOCS_DIR_NAME}"

DRAFTS_DIR = f"{DOCS_PREFIX}/工作稿"
RESEARCH_DIR = f"{DOCS_PREFIX}/research"
DEBATE_DIR = f"{DOCS_PREFIX}/debate"
REVIEWS_DIR = f"{DOCS_PREFIX}/reviews"

DRAFTS_PREFIX = f"{DRAFTS_DIR}/"
RESEARCH_PREFIX = f"{RESEARCH_DIR}/"
DEBATE_PREFIX = f"{DEBATE_DIR}/"
REVIEWS_PREFIX = f"{REVIEWS_DIR}/"

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


def internal_zone_base(*, root: Path, internal_root: Path | None) -> Path:
    """Directory holding ``{index,trash,baselines}`` for one workspace root.

    ``internal_root=None`` means **in-tree** (``<root>/AgentCore/``): correct for
    backends whose root cannot have another folder nested inside it — local /
    sidecar (the root *is* the user's own directory, and desktop restore reads
    ``AgentCore/trash`` there) and shared spaces (flat namespace). Cloud
    conversation workspaces pass an explicit out-of-tree path because cloud
    folders nest for real (双模式工作区 §5.4).
    """
    return internal_root if internal_root is not None else root / AGENTCORE_ROOT


def internal_zone_path(
    zone_name: str, *, root: Path, internal_root: Path | None
) -> Path:
    """One zone directory (``index`` / ``trash`` / ``baselines``) for a root."""
    return internal_zone_base(root=root, internal_root=internal_root) / zone_name


__all__ = [
    "AGENTCORE_ROOT",
    "DOCS_DIR_NAME",
    "DOCS_PREFIX",
    "DRAFTS_DIR",
    "RESEARCH_DIR",
    "DEBATE_DIR",
    "REVIEWS_DIR",
    "DRAFTS_PREFIX",
    "RESEARCH_PREFIX",
    "DEBATE_PREFIX",
    "REVIEWS_PREFIX",
    "INTERNAL_ZONE_NAMES",
    "INDEX_ZONE_NAME",
    "TRASH_ZONE_NAME",
    "BASELINES_ZONE_NAME",
    "VERSIONS_ZONE_NAME",
    "INDEX_REL",
    "TRASH_REL",
    "BASELINES_REL",
    "VERSIONS_REL",
    "internal_zone_base",
    "internal_zone_path",
]
