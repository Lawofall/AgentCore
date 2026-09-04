"""Tests for workspace path policy (conversation → directory).

Pins 双模式工作区 §5.4: a cloud folder's directory follows ``folders.rel_path``
under the user-visible ``tree/`` segment, while scratch, tombstones and hidden
zones stay physically outside it. ``data_dir`` is redirected to ``tmp_path``.
"""

from pathlib import Path

import pytest

from agentcore.config import settings
from agentcore.fulfill.local_roots import (
    install_local_root_declarer,
    uninstall_local_root_declarer,
)
from agentcore.runtime.events import EventSink
from agentcore.runtime.interaction import (
    InteractionRegistry,
    default_interaction_registry,
)
from agentcore.workspace.local import LocalWorkspace
from agentcore.workspace.locate import (
    LocalBinding,
    WorkspaceId,
    build_local_workspace,
    build_server_workspace,
    build_workspace,
    folder_tombstone_path,
    format_workspace_id,
    parse_workspace_id,
    resolve_workspace_root,
    workspace_has_entries,
    workspace_internal_root,
    workspace_storage_key,
)
from agentcore.workspace.server import ServerWorkspace


def test_folder_root_lives_under_the_visible_tree(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    root = resolve_workspace_root(user_id="u1", folder_rel_path="设计", conversation_id="c1")
    assert root == tmp_path / "workspaces" / "u1" / "tree" / "设计"
    assert root.is_dir()


def test_nested_folder_root_follows_the_rel_path_prefix(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    root = resolve_workspace_root(
        user_id="u1", folder_rel_path="设计/图标", conversation_id="c1"
    )
    assert root == tmp_path / "workspaces" / "u1" / "tree" / "设计" / "图标"


def test_conversations_in_same_folder_share_root(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    r1 = resolve_workspace_root(user_id="u1", folder_rel_path="f1", conversation_id="c1")
    r2 = resolve_workspace_root(user_id="u1", folder_rel_path="f1", conversation_id="c2")
    assert r1 == r2


def test_ungrouped_conversations_get_independent_roots(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    r1 = resolve_workspace_root(user_id="u1", folder_rel_path=None, conversation_id="c1")
    r2 = resolve_workspace_root(user_id="u1", folder_rel_path=None, conversation_id="c2")
    assert r1 != r2
    assert r1 == tmp_path / "workspaces" / "u1" / "conv" / "c1"


def test_scratch_is_outside_the_visible_tree(tmp_path: Path, monkeypatch):
    """A user folder named ``conv`` must not collide with the scratch namespace."""
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    scratch = resolve_workspace_root(
        user_id="u1", folder_rel_path=None, conversation_id="c1"
    )
    folder = resolve_workspace_root(
        user_id="u1", folder_rel_path="conv", conversation_id="c1"
    )
    assert scratch != folder
    assert "tree" not in scratch.parts


def test_rootless_coordinates_refuse_to_resolve(tmp_path: Path, monkeypatch):
    """Folder-scoped callers pass ``conversation_id=""``; that must never mean ``conv/``."""
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    with pytest.raises(ValueError, match="无法定位工作区"):
        resolve_workspace_root(user_id="u1", folder_rel_path=None, conversation_id="")


def test_users_are_isolated_by_directory(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    a = resolve_workspace_root(user_id="alice", folder_rel_path="f1", conversation_id="c1")
    b = resolve_workspace_root(user_id="bob", folder_rel_path="f1", conversation_id="c1")
    assert a != b
    assert a.parts[-3] == "alice"


def test_build_server_workspace_targets_resolved_root(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    ws = build_server_workspace(
        user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="c9"
    )
    assert isinstance(ws, ServerWorkspace)
    assert ws.location == "server"


# --- cloud/local fork (双模式工作区 §七: 模式跟着文件在哪自动走) ----------------


def test_build_local_workspace_wires_channel_to_bound_root():
    """A binding yields a LocalWorkspace whose channel carries the desktop root_id."""
    registry = InteractionRegistry()
    ws = build_local_workspace(
        binding=LocalBinding(root_id="root-xyz", root_label="myproj"),
        user_id="u1",
        conversation_id="c1",
        registry=registry,
        timeout_seconds=12.5,
    )
    assert isinstance(ws, LocalWorkspace)
    assert ws.location == "local"
    assert ws.root_label == "myproj"
    chan = ws._channel  # noqa: SLF001 - test-only wiring inspection
    assert chan.root_id == "root-xyz"
    assert chan.conversation_id == "c1"
    assert chan.user_id == "u1"
    assert chan.registry is registry
    assert chan.timeout_seconds == 12.5


def test_build_local_workspace_defaults_to_shared_registry_and_timeout():
    """Omitted deps fall back to the process registry + configured op timeout."""
    ws = build_local_workspace(
        binding=LocalBinding(root_id="r1"),
        user_id="u-test",
        conversation_id="c1",
    )
    chan = ws._channel  # noqa: SLF001 - test-only wiring inspection
    assert chan.registry is default_interaction_registry()
    assert chan.timeout_seconds == settings.workspace_op_timeout_seconds
    assert ws.root_label == "workspace"


class _RecordingDeclarer:
    """Stand-in for the sidecar bridge's fulfiller-session root declaration."""

    def __init__(self) -> None:
        self.roots: list[str] = []

    def declare_root(self, root_id: str) -> None:
        self.roots.append(root_id)


def test_build_local_workspace_declares_the_root_on_the_local_fulfiller():
    """Sidecar: whoever builds the desk owns telling the in-process hub about it."""
    declarer = _RecordingDeclarer()
    install_local_root_declarer(declarer)
    try:
        build_local_workspace(
            binding=LocalBinding(root_id="root-target"),
            user_id="u1",
            conversation_id="c1",
        )
    finally:
        uninstall_local_root_declarer(declarer)
    assert declarer.roots == ["root-target"]


def test_build_local_workspace_declares_nothing_in_cloud_processes():
    """No declarer installed (cloud API): the desktop already declared its roots."""
    declarer = _RecordingDeclarer()
    install_local_root_declarer(declarer)
    uninstall_local_root_declarer(declarer)

    build_local_workspace(
        binding=LocalBinding(root_id="root-target"),
        user_id="u1",
        conversation_id="c1",
    )

    assert declarer.roots == []


def test_build_workspace_picks_local_when_bound():
    ws = build_workspace(
        user_id="u1",
        folder_id="f1",
        folder_rel_path="f1",
        conversation_id="c1",
        sink=EventSink(),
        local_binding=LocalBinding(root_id="root-1"),
    )
    assert isinstance(ws, LocalWorkspace)
    assert ws.location == "local"


def test_build_workspace_falls_back_to_cloud_when_unbound(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    ws = build_workspace(
        user_id="u1",
        folder_id="f1",
        folder_rel_path="f1",
        conversation_id="c1",
        sink=EventSink(),
        local_binding=None,
    )
    assert isinstance(ws, ServerWorkspace)
    assert ws.location == "server"


# --- storage key: id-derived, deliberately NOT the on-disk path ---


def test_storage_key_folder_project():
    key = workspace_storage_key(user_id="u1", folder_id="f1", conversation_id="c1")
    assert key == "workspaces/u1/f1"


def test_storage_key_ungrouped_space():
    key = workspace_storage_key(user_id="u1", folder_id=None, conversation_id="c1")
    assert key == "workspaces/u1/conv/c1"


def test_storage_key_survives_a_rename(tmp_path: Path, monkeypatch):
    """Snapshot history and the mutation lock must not move when a folder is renamed."""
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    before = resolve_workspace_root(
        user_id="u1", folder_rel_path="旧名", conversation_id="c1"
    )
    after = resolve_workspace_root(
        user_id="u1", folder_rel_path="新名", conversation_id="c1"
    )
    assert before != after
    assert workspace_storage_key(
        user_id="u1", folder_id="f1", conversation_id="c1"
    ) == workspace_storage_key(user_id="u1", folder_id="f1", conversation_id="c1")
    assert Path(settings.data_dir) / workspace_storage_key(
        user_id="u1", folder_id="f1", conversation_id="c1"
    ) not in (before, after)


# --- hidden zones + tombstone live outside the visible tree ---


def test_internal_root_is_outside_the_tree_and_id_keyed(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    internal = workspace_internal_root(user_id="u1", folder_id="f1", conversation_id="")
    assert internal == tmp_path / "workspaces" / "u1" / "internal" / "folder" / "f1"
    assert "tree" not in internal.parts


def test_internal_root_ignores_the_visible_name(tmp_path: Path, monkeypatch):
    """Renaming a folder must not orphan its index / trash / baselines."""
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    assert workspace_internal_root(
        user_id="u1", folder_id="f1", conversation_id=""
    ) == workspace_internal_root(user_id="u1", folder_id="f1", conversation_id="")


def test_scratch_internal_root_is_conversation_keyed(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    internal = workspace_internal_root(user_id="u1", folder_id=None, conversation_id="c1")
    assert internal == tmp_path / "workspaces" / "u1" / "internal" / "conv" / "c1"


def test_tombstone_is_id_named_and_outside_the_tree(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    grave = folder_tombstone_path(user_id="u1", folder_id="f1")
    assert grave == tmp_path / "workspaces" / "u1" / "deleted" / "f1"
    assert "tree" not in grave.parts


# --- public workspace id (the /v1/workspaces addressing token) ---


def test_format_workspace_id_folder_vs_conv():
    assert format_workspace_id(folder_id="f1", conversation_id="c1") == "folder:f1"
    assert format_workspace_id(folder_id=None, conversation_id="c1") == "conv:c1"


def test_parse_workspace_id_round_trips():
    assert parse_workspace_id("conv:c1") == WorkspaceId(kind="conv", ident="c1")
    assert parse_workspace_id("folder:f9") == WorkspaceId(kind="folder", ident="f9")
    parsed = parse_workspace_id(format_workspace_id(folder_id="f9", conversation_id="c9"))
    assert parsed == WorkspaceId(kind="folder", ident="f9")


def test_parse_workspace_id_accepts_uuid_idents():
    wid = "11111111-2222-3333-4444-555555555555"
    assert parse_workspace_id(f"folder:{wid}").ident == wid


def test_parse_workspace_id_rejects_malformed():
    for bad in ("", "folder", "folder:", ":f1", "team:f1", "shared:abc", "folder:a/b", "conv/c1"):
        with pytest.raises(ValueError, match="非法工作区"):
            parse_workspace_id(bad)


def test_workspace_has_entries_false_when_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    assert not workspace_has_entries(
        user_id="u1", folder_rel_path=None, conversation_id="c1"
    )
    assert not (tmp_path / "workspaces" / "u1" / "conv" / "c1").exists()


def test_workspace_has_entries_false_without_a_placement(tmp_path: Path, monkeypatch):
    """A folder row with no slot yet has no directory — False, not a crash."""
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    assert not workspace_has_entries(user_id="u1", folder_rel_path=None, conversation_id="")


def test_workspace_has_entries_false_when_empty(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    resolve_workspace_root(user_id="u1", folder_rel_path="f1", conversation_id="c1")
    assert not workspace_has_entries(
        user_id="u1", folder_rel_path="f1", conversation_id="c1"
    )


def test_workspace_has_entries_true_when_non_empty(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    root = resolve_workspace_root(user_id="u1", folder_rel_path="f1", conversation_id="c1")
    (root / "note.txt").write_text("hi", encoding="utf-8")
    assert workspace_has_entries(user_id="u1", folder_rel_path="f1", conversation_id="c1")


def test_workspace_has_entries_false_when_only_internal_index(
    tmp_path: Path, monkeypatch
):
    """AgentCore/index alone must not count as hub has_files (local-shaped trees)."""
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    root = resolve_workspace_root(user_id="u1", folder_rel_path=None, conversation_id="c1")
    (root / "AgentCore" / "index").mkdir(parents=True)
    (root / "AgentCore" / "index" / "code_search.db").write_bytes(b"")
    assert not workspace_has_entries(
        user_id="u1", folder_rel_path=None, conversation_id="c1"
    )


def test_workspace_has_entries_false_when_only_internal_zones(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    root = resolve_workspace_root(user_id="u1", folder_rel_path=None, conversation_id="c2")
    for zone in ("index", "trash", "baselines"):
        (root / "AgentCore" / zone).mkdir(parents=True)
    assert not workspace_has_entries(
        user_id="u1", folder_rel_path=None, conversation_id="c2"
    )


def test_workspace_has_entries_true_with_agentcore_docs(tmp_path: Path, monkeypatch):
    """Visible AgentCore content (文档等) still counts; bare top-level index too."""
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    root = resolve_workspace_root(user_id="u1", folder_rel_path=None, conversation_id="c3")
    (root / "AgentCore" / "index").mkdir(parents=True)
    (root / "AgentCore" / "文档").mkdir(parents=True)
    (root / "AgentCore" / "文档" / "note.md").write_text("x", encoding="utf-8")
    assert workspace_has_entries(
        user_id="u1", folder_rel_path=None, conversation_id="c3"
    )

    root2 = resolve_workspace_root(user_id="u1", folder_rel_path=None, conversation_id="c4")
    (root2 / "index").mkdir()  # bare name — not an internal zone
    assert workspace_has_entries(
        user_id="u1", folder_rel_path=None, conversation_id="c4"
    )


@pytest.mark.asyncio
async def test_empty_server_workspace_start_index_does_not_mkdir(tmp_path: Path):
    """Lazy B1: empty tree must not create AgentCore/index on maintenance kick."""
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.stage_dirs import INDEX_REL

    ws = ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())
    ws.start_code_index_maintenance()
    assert ws._index_maintainer is None  # noqa: SLF001
    assert not (tmp_path / Path(*INDEX_REL.split("/"))).exists()

    await ws.write("hello.py", "print(1)\n")
    assert ws._index_maintainer is not None  # noqa: SLF001
    await ws._index_maintainer.drain()  # noqa: SLF001
    assert (tmp_path / Path(*INDEX_REL.split("/"))).is_dir()


@pytest.mark.asyncio
async def test_cloud_index_dir_lands_outside_the_tree(tmp_path: Path):
    """A nested folder's index DB must not read as its parent's content."""
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox

    root = tmp_path / "tree" / "设计"
    root.mkdir(parents=True)
    internal = tmp_path / "internal" / "folder" / "f1"
    ws = ServerWorkspace(root=root, sandbox=SubprocessSandbox(), internal_root=internal)
    assert ws.index_dir == internal / "index"

    await ws.write("hello.py", "print(1)\n")
    await ws._index_maintainer.drain()  # noqa: SLF001
    assert (internal / "index").is_dir()
    assert not (root / "AgentCore").exists()


@pytest.mark.asyncio
async def test_build_turn_backend_does_not_kick_code_index(monkeypatch, tmp_path):
    """TTFT: turn entry must not schedule index maintenance."""
    from unittest.mock import AsyncMock, MagicMock

    from agentcore.config import settings
    from agentcore.conversation import turn_backend as tb
    from agentcore.runtime.events.sink import EventSink

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    backend = MagicMock()
    backend.location = "server"
    backend.start_code_index_maintenance = MagicMock()
    monkeypatch.setattr(tb, "build_workspace", lambda **_kwargs: backend)
    monkeypatch.setattr(
        tb.grant_store, "grants_as_dict", AsyncMock(return_value={})
    )
    monkeypatch.setattr(tb, "attach_grants_to_backend", AsyncMock())

    result = await tb.build_turn_backend(
        user_id="u1",
        conversation_id="00000000-0000-0000-0000-00000000nok1",
        folder_id=None,
        sink=EventSink(),
        local_binding=None,
    )
    assert result is backend
    backend.start_code_index_maintenance.assert_not_called()
