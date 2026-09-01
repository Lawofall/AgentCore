"""Resolve a conversation to its server-side workspace (cloud mode).

Path policy (双模式工作区 §5.4 · 容器统一为文件夹)::

    <data_dir>/workspaces/<user_id>/
        tree/<rel_path>/        ← 云文件夹, 用户可见名, 真嵌套 (folder:<id>)
        conv/<conversation_id>/ ← 裸聊 scratch (conv:<id>)
        deleted/<folder_id>/    ← 软删墓碑 (目录搬出用户树, 名字当场释放)
        internal/<kind>/<id>/   ← 隐藏 zone {index,trash,baselines}

**用户可见树只装用户看得见的东西**——scratch、墓碑、隐藏 zone 都在 ``tree/`` 之外,
所以它们既不会被上层文件夹的 AI 当成用户内容读到, 也不会和用户自己起的文件夹名
撞车 (用户完全可以把文件夹叫 ``conv``)。段名与 ``workspaces/`` 基路径由
:mod:`agentcore.workspace.layout` 统一持有; 反方向的「盘上现有哪些目录」也在那里
(运维扫盘专用)。

云文件夹的物理落点由 ``folders.rel_path`` **单一真相源**决定, 父子关系由路径前缀
表达 (``设计/图标`` 就在 ``设计`` 里面)。因此本模块里凡是产出 ``Path`` 的函数都收
``folder_rel_path``, 而不是 ``folder_id``——id 说明「是哪个文件夹」, 只有 rel_path
说明「它现在在哪」。解析 id → rel_path 的唯一入口是
:func:`agentcore.folders.placement.resolve_folder_placement`; 这里保持纯函数, 好让
路径策略的单测不用起数据库。

``workspace_storage_key`` (= ``workspace_lock`` 键 + 快照存储前缀) 走另一条路: 它
**保持 id 派生**, 不再镜像盘上布局。改名不得打断任何引用——路径派生的快照前缀会
让改名孤儿化整段快照历史, 路径派生的锁键会让改名事务和并发回合各拿一把不同的锁。

User-scoped top segment keeps tenants isolated by directory; the traversal guard
inside ``ServerWorkspace`` then prevents escaping the resolved root. IDs are
server-generated UUIDs (not user input), so they are safe path segments; visible
folder names are sanitized by ``cloud_tree.sanitize_folder_name`` before they ever
reach ``rel_path``.

This is the single place that maps "which conversation" → "which directory" (for
cloud) and "which conversation" → "which desktop root" (for local);
``conversation/service.py`` calls :func:`build_workspace` and injects the chosen
backend into the pipeline. The server-vs-local fork (双模式工作区 §七, "模式跟着
文件在哪自动走") lives here so tools and the engine stay backend-agnostic.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

from agentcore.config import settings
from agentcore.fulfill.local_roots import declare_local_root
from agentcore.runtime.events import EventSink
from agentcore.runtime.interaction import default_interaction_registry
from agentcore.runtime.ports import ClientRequestBridge
from agentcore.tools.sandbox import create_sandbox
from agentcore.tools.sandbox.protocol import SandboxProvider
from agentcore.workspace._paths import path_has_non_internal_entries
from agentcore.workspace.channel import WorkspaceChannel
from agentcore.workspace.cloud_tree import normalize_rel_path
from agentcore.workspace.layout import (
    CONV_SEGMENT,
    DELETED_SEGMENT,
    IM_SEGMENT,
    INTERNAL_SEGMENT,
    TREE_SEGMENT,
    WORKSPACES_SEGMENT,
    workspaces_base_path,
)
from agentcore.workspace.local import LocalWorkspace
from agentcore.workspace.protocol import WorkspaceBackend
from agentcore.workspace.server import ServerWorkspace
from agentcore.workspace.shared_paths import (
    resolve_shared_workspace_root,
    shared_workspace_storage_key,
)


class WorkspaceCoords(TypedDict):
    """The four coordinates that address one cloud workspace.

    Every ``workspace.files`` / ``snapshots`` / ``git`` entry point takes exactly
    these, so the API layer resolves them once and splats them in. A ``TypedDict``
    rather than a plain mapping because the keys do **not** share a type: only the
    folder pair is optional (``None`` = a bare-chat scratch), while ``user_id`` and
    ``conversation_id`` are always strings. Widening them to a common
    ``str | None`` would hand the services a nullable owner / conversation that
    they are typed to refuse — and the ``**`` splat would stop checking anything.
    """

    user_id: str
    folder_id: str | None
    folder_rel_path: str | None
    conversation_id: str


def _storage_relpath(*, user_id: str, folder_id: str | None, conversation_id: str) -> str:
    """Stable id-derived key suffix — **not** a claim about the on-disk location.

    Kept byte-identical to the pre-tree layout on purpose: it is the snapshot
    storage prefix and the ``workspace_lock`` key, both of which must survive a
    folder rename untouched.
    """
    if folder_id:
        return f"{user_id}/{folder_id}"
    return f"{user_id}/{CONV_SEGMENT}/{conversation_id}"


def _physical_relpath(
    *, user_id: str, folder_rel_path: str | None, conversation_id: str
) -> str:
    """Where the workspace actually sits, relative to the workspaces base (POSIX).

    A folder lives at its ``rel_path`` under ``tree/``; a 裸聊 keeps its own flat
    ``conv/<id>`` scratch outside the visible tree.

    Refuses to produce a path when it has neither coordinate: callers that pass
    ``conversation_id=""`` for folder-scoped work (retention purge) would otherwise
    resolve to the whole ``conv/`` namespace and ``rmtree`` every scratch on disk.
    """
    rel = normalize_rel_path(folder_rel_path)
    if rel:
        return f"{user_id}/{TREE_SEGMENT}/{rel}"
    if not conversation_id:
        raise ValueError("无法定位工作区：既没有文件夹相对路径，也没有会话 id")
    return f"{user_id}/{CONV_SEGMENT}/{conversation_id}"


# A workspace's stable public id — the addressing token for the first-class
# ``/v1/workspaces/{ws_id}`` API (文件中枢统一 Step 1). It encodes the same
# folder-vs-ungrouped fork as ``_workspace_relpath`` (the folder *is* the project
# space; an ungrouped conversation gets its own), so a id round-trips to exactly
# one workspace directory. ``:`` separates kind from the UUID — a valid single
# URL path segment that UUIDs never contain, so it needs no escaping.
_WORKSPACE_ID_SEP = ":"


@dataclass(frozen=True)
class WorkspaceId:
    """A parsed workspace id: folder project, bare-chat scratch, or shared space."""

    kind: Literal["folder", "conv", "shared"]
    ident: str


def format_workspace_id(*, folder_id: str | None, conversation_id: str) -> str:
    """Public workspace id: ``folder:<id>`` for a project, else ``conv:<id>``."""
    if folder_id:
        return f"folder{_WORKSPACE_ID_SEP}{folder_id}"
    return f"conv{_WORKSPACE_ID_SEP}{conversation_id}"


def format_shared_workspace_id(space_id: str) -> str:
    """Public workspace id for a shared space: ``shared:<space_id>``."""
    return f"shared{_WORKSPACE_ID_SEP}{space_id}"


def parse_workspace_id(ws_id: str) -> WorkspaceId:
    """Parse a public workspace id, or raise ``ValueError`` if malformed.

    Pure (no DB / owner check): the API layer resolves the ``ident`` against the
    user's folders/conversations (or shared-space membership) for authorization.
    Rejects unknown kinds and empty / slash-bearing idents so a id can address
    only one path segment.
    """
    kind, sep, ident = ws_id.partition(_WORKSPACE_ID_SEP)
    if not sep or not ident or "/" in ident or kind not in ("folder", "conv", "shared"):
        raise ValueError(f"非法工作区 id：{ws_id!r}")
    return WorkspaceId(kind=kind, ident=ident)  # type: ignore[arg-type]


def workspace_has_entries(
    *, user_id: str, folder_rel_path: str | None, conversation_id: str
) -> bool:
    """Whether the workspace dir has non-internal content — *without* creating it.

    Backs the hub enumeration's F1 filter (未分组空间只在真有文件时才列出, 文件中枢
    统一 §四). Uses the no-create path helper on purpose: resolving via the backend
    would ``mkdir`` an empty dir for every ungrouped conversation we probe.
    """
    try:
        root = workspace_root_path(
            user_id=user_id, folder_rel_path=folder_rel_path, conversation_id=conversation_id
        )
    except ValueError:
        # No placement yet (folder row predates the tree migration) — there is no
        # directory, so "has files" is honestly False rather than a 500.
        return False
    return path_has_non_internal_entries(root)


def workspace_root_path(
    *, user_id: str, folder_rel_path: str | None, conversation_id: str
) -> Path:
    """The workspace directory path for a conversation — without creating it.

    The pure path helper behind :func:`resolve_workspace_root`; retention cleanup
    (决策⑦) needs the location to delete it, where creating-on-resolve would be
    wrong (and would resurrect a dir we are about to purge).
    """
    relpath = _physical_relpath(
        user_id=user_id, folder_rel_path=folder_rel_path, conversation_id=conversation_id
    )
    return workspaces_base_path() / relpath


def resolve_workspace_root(
    *, user_id: str, folder_rel_path: str | None, conversation_id: str
) -> Path:
    """Return (creating if needed) the workspace directory for a conversation."""
    root = workspace_root_path(
        user_id=user_id, folder_rel_path=folder_rel_path, conversation_id=conversation_id
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def workspace_storage_key(*, user_id: str, folder_id: str | None, conversation_id: str) -> str:
    """The snapshot storage key **and** ``workspace_lock`` key for a workspace.

    Deliberately **id-derived and stable across renames / moves** — it is not a
    mirror of the on-disk layout (see the module docstring). A folder's visible
    path changes; its snapshot history and its mutation lock must not. The
    StorageProvider adds its own top-level prefix (``snapshots/``).
    """
    relpath = _storage_relpath(
        user_id=user_id, folder_id=folder_id, conversation_id=conversation_id
    )
    return f"{WORKSPACES_SEGMENT}/{relpath}"


def workspace_internal_root(
    *, user_id: str, folder_id: str | None, conversation_id: str
) -> Path:
    """Where a cloud workspace's hidden zones live — **outside** the visible tree.

    ``{index,trash,baselines}`` are per-workspace derived state. Once folders nest
    for real, keeping them inside the tree would hand a parent folder's AI the
    child's deleted files, baseline zips and index DB as ordinary content (and a
    parent's turn baseline would recursively swallow the child's baselines). So
    they move out and are keyed by the **stable id**, which also means they survive
    a rename untouched.

    Local / sidecar backends keep their zones in-tree: there the root *is* the
    user's own directory, nothing nests inside our container, and desktop restore
    reads ``AgentCore/trash`` under that root.
    """
    kind, ident = ("folder", folder_id) if folder_id else (CONV_SEGMENT, conversation_id)
    return workspaces_base_path() / user_id / INTERNAL_SEGMENT / kind / ident


def folder_tombstone_path(*, user_id: str, folder_id: str) -> Path:
    """Where a soft-deleted folder's directory is parked until retention purges it.

    Soft-delete moves the tree here so the visible name is released immediately —
    otherwise recreating a folder with the same name would land on the deleted
    folder's files, and the later retention sweep would ``rmtree`` the new one.
    Keyed by id (names are not unique among tombstones).
    """
    return workspaces_base_path() / user_id / DELETED_SEGMENT / folder_id


def _default_server_sandbox() -> SandboxProvider:
    """Cloud worker sandbox — gVisor when enabled, else subprocess."""
    return create_sandbox(
        location="server",
        gvisor_enabled=settings.gvisor_enabled,
        runsc_path=settings.gvisor_runsc_path,
        runtime_root=settings.gvisor_runtime_root,
    )


def build_server_workspace(
    *,
    user_id: str,
    folder_id: str | None,
    folder_rel_path: str | None,
    conversation_id: str,
    sandbox: SandboxProvider | None = None,
) -> ServerWorkspace:
    """Construct the ``ServerWorkspace`` for a conversation's resolved root.

    Both folder coordinates are needed and they are not interchangeable:
    ``folder_rel_path`` says where the directory is (and moves on rename), while
    ``folder_id`` keys the lock / snapshot / hidden-zone namespaces (and does not).
    """
    root = resolve_workspace_root(
        user_id=user_id, folder_rel_path=folder_rel_path, conversation_id=conversation_id
    )
    return ServerWorkspace(
        root=root,
        sandbox=sandbox or _default_server_sandbox(),
        lock_key=workspace_storage_key(
            user_id=user_id, folder_id=folder_id, conversation_id=conversation_id
        ),
        internal_root=workspace_internal_root(
            user_id=user_id, folder_id=folder_id, conversation_id=conversation_id
        ),
    )


# IM chat attachments live in their own top-level space, separate from the
# per-user conversation workspaces above: a chat is shared by many users, so it
# is keyed by ``chat_id`` (a server-minted UUID, safe as a single path segment)
# rather than nested under any one member's ``user_id``. Reusing ``ServerWorkspace``
# gives the same traversal guard and atomic writes for free (Stage 4 富消息).
#
# Being a *sibling* of the user dirs is what makes ``layout``'s allowlist load-bearing:
# ``im/<chat_id>/`` is UUID-named one level deeper than a folder dir, so any sweep that
# mistakes ``im`` for a user reads every chat's attachments as orphan folder dirs.


def chat_workspace_root_path(chat_id: str) -> Path:
    """The on-disk root for a chat's attachments — without creating it."""
    return workspaces_base_path() / IM_SEGMENT / chat_id


def build_chat_workspace(
    chat_id: str, *, sandbox: SandboxProvider | None = None
) -> ServerWorkspace:
    """Construct the ``ServerWorkspace`` rooted at a chat's attachment space.

    Callers must authorize membership *before* building this (the directory is
    created on resolve), so a non-member never materializes a chat's space.
    """
    root = chat_workspace_root_path(chat_id)
    root.mkdir(parents=True, exist_ok=True)
    return ServerWorkspace(
        root=root,
        sandbox=sandbox or _default_server_sandbox(),
        lock_key=f"{WORKSPACES_SEGMENT}/{IM_SEGMENT}/{chat_id}",
    )


def build_shared_workspace(
    space_id: str, *, sandbox: SandboxProvider | None = None
) -> ServerWorkspace:
    """Construct a ``ServerWorkspace`` rooted at ``workspaces/shared/<space_id>/``.

    Callers must authorize membership *before* building (mkdir on resolve).
    """
    root = resolve_shared_workspace_root(space_id)
    return ServerWorkspace(
        root=root,
        sandbox=sandbox or _default_server_sandbox(),
        lock_key=shared_workspace_storage_key(space_id),
    )


@dataclass(frozen=True)
class LocalBinding:
    """A conversation's binding to a desktop FS root (the local-mode marker).

    ``root_id`` is the desktop-generated handle for an authorized local directory
    (registered in ``apps/desktop/src/main/fs-service.ts``); ``root_label`` is its
    human-readable name, used for relative-path rendering so absolute local paths
    never leak into prompts. The *presence* of a binding is exactly what flips a
    conversation to local mode (§七); its absence means cloud.

    ``subpath`` (工作区对称化 D1a) is the workspace's sub-directory *within* the
    root. Empty = the folder is the root itself (an explicitly-added project). A
    non-empty single segment scopes a per-conversation workspace under a shared
    container root; ``LocalWorkspace`` prefixes it onto every op path so the engine
    and the user only ever see workspace-relative paths.
    """

    root_id: str
    root_label: str = "workspace"
    subpath: str = ""


def build_local_workspace(
    *,
    binding: LocalBinding,
    user_id: str,
    conversation_id: str,
    registry: ClientRequestBridge | None = None,
    timeout_seconds: float | None = None,
) -> LocalWorkspace:
    """Construct the ``LocalWorkspace`` for a conversation bound to a desktop root.

    Builds the per-turn ``WorkspaceChannel`` — the generalized approval-gate
    transport — over the process-wide op registry (the same one the resolve
    endpoint settles) and the device-level fulfill hub. The channel carries
    ``binding.root_id`` so every op the engine issues runs against the right
    authorized directory on the user's machine. State (the suspended op Future)
    lives in the registry, so it must be the *shared* default unless a test injects
    its own. ``user_id`` selects which online device receives ``*_required`` frames.

    Building the workspace is also the moment this process commits to running ops
    against ``binding.root_id``, so the root is declared on the in-process
    fulfiller here (sidecar only — cloud installs no declarer). A cross-desk desk
    resolves its root from the target folder, which the turn's own ``localRootId``
    declaration never covers, and root-scoped frames only reach a session holding
    the root.
    """
    declare_local_root(binding.root_id)
    channel = WorkspaceChannel(
        user_id=user_id,
        conversation_id=conversation_id,
        registry=registry or default_interaction_registry(),
        timeout_seconds=(
            settings.workspace_op_timeout_seconds if timeout_seconds is None else timeout_seconds
        ),
        root_id=binding.root_id,
        max_inflight=settings.workspace_channel_max_inflight,
    )
    return LocalWorkspace(
        channel,
        root_label=binding.root_label,
        execute_timeout_slack=settings.workspace_execute_timeout_slack_seconds,
        base_subpath=binding.subpath,
    )


def build_workspace(
    *,
    user_id: str,
    folder_id: str | None,
    folder_rel_path: str | None,
    conversation_id: str,
    sink: EventSink,
    local_binding: LocalBinding | None,
    sandbox: SandboxProvider | None = None,
) -> WorkspaceBackend:
    """Pick a turn's backend: local when bound to a desktop root, else cloud.

    The single fork behind 双模式工作区 §七 ("模式跟着文件在哪自动走"): a resolved
    ``local_binding`` yields a desktop-backed ``LocalWorkspace``; its absence falls
    back to the server-hosted ``ServerWorkspace``. Both satisfy ``WorkspaceBackend``
    (the P0 seam), so the file tools and the engine run unchanged on either — the
    caller only has to decide *which* here, never *how* downstream.

    ``sink`` remains on the cloud path for lock-wait / display signals; local
    CLIENT_TOOL delivery no longer uses it.
    """
    if local_binding is not None:
        return build_local_workspace(
            binding=local_binding,
            user_id=user_id,
            conversation_id=conversation_id,
        )
    return build_server_workspace(
        user_id=user_id,
        folder_id=folder_id,
        folder_rel_path=folder_rel_path,
        conversation_id=conversation_id,
        sandbox=sandbox,
    )


def workspace_channel_for_tools(
    backend: WorkspaceBackend,
    *,
    user_id: str,
    conversation_id: str,
    registry: ClientRequestBridge | None = None,
) -> WorkspaceChannel | None:
    """The ``workspace_op_required`` channel for desktop-held ops.

    LocalWorkspace already owns a channel (file / execute / diagnostics) — reuse
    it so process ops and the language service share root_id + registry. Sidecar
    uses ServerWorkspace(location=local) with direct Path I/O and no owned
    channel; build one so ``terminal`` and ``diagnostics`` still leave the
    short-lived sidecar for the desktop main process (双模式工作区 §四).
    Cloud server backends return ``None`` (those ops are not registered there).
    """
    if backend.location != "local":
        return None
    existing = getattr(backend, "_channel", None)
    if isinstance(existing, WorkspaceChannel):
        channel = existing
    else:
        channel = WorkspaceChannel(
            user_id=user_id,
            conversation_id=conversation_id,
            registry=registry or default_interaction_registry(),
            timeout_seconds=settings.workspace_op_timeout_seconds,
            root_id="",
            max_inflight=settings.workspace_channel_max_inflight,
        )
    attach = getattr(backend, "attach_desktop_channel", None)
    if callable(attach):
        attach(channel)
    return channel
