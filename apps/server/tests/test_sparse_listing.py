"""Tests for sparse workspace listing helpers + two-tier ignore rules."""

from pathlib import Path

from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace._paths import (
    AI_NOISE_FILE_SUFFIXES,
    IGNORED_DIRS,
    IGNORED_FILE_SUFFIXES,
    SYSTEM_IGNORED_FILE_SUFFIXES,
    is_access_denied_oserror,
    is_ai_noise_file_name,
    is_ignored_dir_entry,
    is_ignored_dir_name,
    is_ignored_file_name,
    is_ignored_relpath,
    is_internal_zone_relpath,
    is_system_ignored_file_name,
)
from agentcore.workspace.server import ServerWorkspace
from agentcore.workspace.sparse_listing import (
    collect_turn_material_paths,
    format_remaining_summary,
    is_ai_list_hidden_file,
    is_attachment_path,
    partition_sparse_paths,
    should_hide_ai_noise_from_list,
)


def test_ignored_dirs_include_git_and_ide_caches_not_bare_internal():
    assert ".agentcore" not in IGNORED_DIRS
    assert "index" not in IGNORED_DIRS
    assert "trash" not in IGNORED_DIRS
    assert "baselines" not in IGNORED_DIRS
    assert ".git" in IGNORED_DIRS
    assert "node_modules" in IGNORED_DIRS
    assert "vendor" in IGNORED_DIRS
    assert ".turbo" in IGNORED_DIRS
    assert "coverage" in IGNORED_DIRS
    assert "htmlcov" in IGNORED_DIRS
    assert "logs" in IGNORED_DIRS
    assert "tmp" in IGNORED_DIRS
    assert "temp" in IGNORED_DIRS
    assert ".tmp" in IGNORED_DIRS
    assert ".pytest_cache" in IGNORED_DIRS
    assert ".pytest_tmp" in IGNORED_DIRS
    assert ".idea" in IGNORED_DIRS
    assert ".vscode" in IGNORED_DIRS
    assert is_ignored_dir_name(".git")
    assert is_ignored_dir_name(".pytest_tmp")
    assert is_ignored_dir_name("logs")
    assert not is_ignored_dir_name("src")
    assert not is_ignored_dir_name("index")
    assert is_access_denied_oserror(PermissionError(13, "Permission denied"))
    assert is_access_denied_oserror(OSError(5, "Access is denied"))
    assert not is_access_denied_oserror(OSError(2, "No such file"))

def test_internal_zone_relpath_is_path_aware():
    assert is_internal_zone_relpath("AgentCore/index")
    assert is_internal_zone_relpath("AgentCore/index/code_search.db")
    assert is_internal_zone_relpath("AgentCore/trash/x")
    assert is_internal_zone_relpath("AgentCore/baselines/m.zip")
    assert not is_internal_zone_relpath("AgentCore")
    assert not is_internal_zone_relpath("AgentCore/规则/x.md")
    assert not is_internal_zone_relpath("AgentCore/记忆/y.md")
    assert not is_internal_zone_relpath("AgentCore/文档/z.md")
    assert not is_internal_zone_relpath("index")
    assert not is_internal_zone_relpath("trash/foo")
    assert not is_internal_zone_relpath("foo/AgentCore/index")


def test_ignored_dir_entry_path_aware_and_ancestor_noise():
    assert is_ignored_dir_entry(parent_rel="AgentCore", name="index")
    assert is_ignored_dir_entry(parent_rel="AgentCore", name="trash")
    assert is_ignored_dir_entry(parent_rel="AgentCore", name="baselines")
    assert not is_ignored_dir_entry(parent_rel="", name="index")
    assert not is_ignored_dir_entry(parent_rel="AgentCore", name="规则")
    # Recursive glob under .git: parent carries the noise segment.
    assert is_ignored_dir_entry(parent_rel=".git", name="config")
    assert is_ignored_dir_entry(parent_rel="node_modules/pkg", name="index.js")


def test_system_suffixes_hide_from_ui_and_ai():
    assert ".db" in SYSTEM_IGNORED_FILE_SUFFIXES
    assert ".sqlite" in SYSTEM_IGNORED_FILE_SUFFIXES
    assert is_system_ignored_file_name("code_search.db")
    assert is_system_ignored_file_name("CODE_SEARCH.DB")
    assert is_system_ignored_file_name("x.pyc")
    assert not is_system_ignored_file_name("photo.png")
    assert not is_system_ignored_file_name("readme.md")


def test_ai_noise_suffixes_are_media_archives_binaries():
    assert ".png" in AI_NOISE_FILE_SUFFIXES
    assert ".zip" in AI_NOISE_FILE_SUFFIXES
    assert ".pack" in AI_NOISE_FILE_SUFFIXES
    assert ".log" in AI_NOISE_FILE_SUFFIXES
    assert ".parquet" in AI_NOISE_FILE_SUFFIXES
    assert ".feather" in AI_NOISE_FILE_SUFFIXES
    assert ".arrow" in AI_NOISE_FILE_SUFFIXES
    assert ".npy" in AI_NOISE_FILE_SUFFIXES
    assert ".h5" in AI_NOISE_FILE_SUFFIXES
    assert ".hdf5" in AI_NOISE_FILE_SUFFIXES
    assert ".pkl" in AI_NOISE_FILE_SUFFIXES
    assert ".pickle" in AI_NOISE_FILE_SUFFIXES
    assert is_ai_noise_file_name("photo.PNG")
    assert is_ai_noise_file_name("out.zip")
    assert is_ai_noise_file_name("app.LOG")
    assert is_ai_noise_file_name("data.PARQUET")
    assert is_ai_noise_file_name("model.pkl")
    assert not is_ai_noise_file_name("code_search.db")  # system tier
    assert not is_ai_noise_file_name("report.pdf")  # office docs stay listable


def test_ignored_file_suffixes_combine_both_tiers():
    assert ".db" in IGNORED_FILE_SUFFIXES
    assert ".png" in IGNORED_FILE_SUFFIXES
    assert is_ignored_file_name("code_search.db")
    assert is_ignored_file_name("photo.PNG")
    assert not is_ignored_file_name("readme.md")
    assert not is_ignored_file_name("report.pdf")


def test_ignored_relpath_prunes_nested_noise():
    assert is_ignored_relpath("AgentCore/index/code_search.db")
    assert is_ignored_relpath("node_modules/pkg/index.js")
    assert is_ignored_relpath("vendor/github.com/foo/bar.go")
    assert is_ignored_relpath("logs/dev.jsonl")
    assert is_ignored_relpath("apps/logs/trace.json")
    assert is_ignored_relpath("apps/server/tmp/scratch.txt")
    assert is_ignored_relpath("src/cache.db")
    assert is_ignored_relpath("out/hero.png")
    assert is_ignored_relpath("debug.log")  # AI noise suffix (combined ignore)
    assert not is_ignored_relpath("src/app.ts")
    assert not is_ignored_relpath("index/app.ts")  # bare user index/
    assert not is_ignored_relpath("AgentCore/规则/x.md")


def test_partition_bare_lists_all_with_labels():
    rows, remaining = partition_sparse_paths(
        ["attachments/a.txt", "out.md", "data.csv"],
        shared_workspace=False,
    )
    assert remaining == 0
    assert rows == [
        ("attachments/a.txt", "附件·含历轮"),
        ("out.md", "工作区已有"),
        ("data.csv", "工作区已有"),
    ]


def test_partition_project_keeps_attachments_and_collapses_rest():
    others = [f"f{i}.py" for i in range(8)]
    rows, remaining = partition_sparse_paths(
        ["attachments/x.md", *others],
        shared_workspace=True,
    )
    assert rows == [("attachments/x.md", "附件·含历轮")]
    assert remaining == 8
    assert "file_list" in format_remaining_summary(remaining)


def test_is_attachment_path():
    assert is_attachment_path("attachments/a.txt")
    assert is_attachment_path("attachments")
    assert not is_attachment_path("src/attachments/x.txt")


def test_attachments_exempt_ai_noise_from_list_helpers():
    """attachments/ zip/media stay listable; same suffixes elsewhere stay hidden."""
    assert should_hide_ai_noise_from_list("attachments/pack.zip") is False
    assert should_hide_ai_noise_from_list("attachments/photo.png") is False
    assert should_hide_ai_noise_from_list("out.zip") is True
    assert should_hide_ai_noise_from_list("src/out.zip") is True
    assert should_hide_ai_noise_from_list("notes.md") is False
    assert should_hide_ai_noise_from_list("src/attachments/x.zip") is True  # not root attachments/

    assert is_ai_list_hidden_file(parent_rel="attachments", name="pack.zip") is False
    assert is_ai_list_hidden_file(parent_rel="", name="out.zip") is True
    assert is_ai_list_hidden_file(parent_rel="src", name="out.zip") is True
    # System noise never exempt
    assert is_ai_list_hidden_file(parent_rel="attachments", name="x.db") is True


def test_external_ns_archives_visible_media_still_hidden():
    """区外 external/<alias>/ 压缩包可见；同路径媒体仍按 AI 噪音隐藏。"""
    from agentcore.workspace.sparse_listing import is_external_ns_path

    assert is_external_ns_path("external/desk/咨询.sy.zip") is True
    assert is_external_ns_path("external/desk") is True
    assert is_external_ns_path("src/external/x.zip") is False

    assert should_hide_ai_noise_from_list("external/desk/咨询.sy.zip") is False
    assert should_hide_ai_noise_from_list("external/desk/note.rar") is False
    assert should_hide_ai_noise_from_list("external/desk/photo.png") is True
    assert should_hide_ai_noise_from_list("out.zip") is True  # workspace root noise

    assert (
        is_ai_list_hidden_file(parent_rel="external/desk", name="咨询.sy.zip")
        is False
    )
    assert (
        is_ai_list_hidden_file(parent_rel="external/desk", name="shot.png") is True
    )
    # pattern 豁免：工作区根压缩包在 reveal_archives 时可见
    assert (
        should_hide_ai_noise_from_list("noise.zip", reveal_archives=True) is False
    )
    assert (
        should_hide_ai_noise_from_list("shot.png", reveal_archives=True) is True
    )
    assert (
        is_ai_list_hidden_file(
            parent_rel="", name="noise.zip", reveal_archives=True
        )
        is False
    )


def test_materials_exempt_ai_noise_outside_attachments():
    """Turn material paths reveal AI-noise even outside attachments/."""
    materials = frozenset({"src/shot.png"})
    assert should_hide_ai_noise_from_list("src/shot.png", materials=materials) is False
    assert should_hide_ai_noise_from_list("src/other.png", materials=materials) is True
    assert should_hide_ai_noise_from_list("src/shot.png") is True  # no materials → hide
    assert (
        is_ai_list_hidden_file(
            parent_rel="src", name="shot.png", materials=materials
        )
        is False
    )
    assert (
        is_ai_list_hidden_file(
            parent_rel="src", name="other.png", materials=materials
        )
        is True
    )
    # attachments/ still exempt without being in materials
    assert should_hide_ai_noise_from_list("attachments/pack.zip", materials=materials) is False
    # System noise never exempt via materials
    assert (
        is_ai_list_hidden_file(
            parent_rel="src", name="x.db", materials=frozenset({"src/x.db"})
        )
        is True
    )


def test_collect_turn_material_paths():
    paths = collect_turn_material_paths(
        [
            {
                "name": "shot.png",
                "path": "src/shot.png",
                "workspace_path": "src/shot.png",
            },
            {
                "name": "doc.docx",
                "workspace_path": "attachments/doc.docx",
                "parsed_workspace_path": "attachments/doc.docx.md",
            },
            {
                "name": "gone.zip",
                "resident_missing": True,
                "claimed_workspace_path": "attachments/gone.zip",
                "path": "C:/Users/x/gone.zip",
            },
            {
                "name": "abs.bin",
                "path": "C:\\Users\\x\\abs.bin",
            },
        ]
    )
    assert paths == frozenset(
        {"src/shot.png", "attachments/doc.docx", "attachments/doc.docx.md"}
    )
    assert collect_turn_material_paths(None) == frozenset()
    assert collect_turn_material_paths([]) == frozenset()


async def test_list_tree_shows_attachment_zip_hides_elsewhere(tmp_path: Path):
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "pack.zip").write_bytes(b"PK")
    (tmp_path / "noise.zip").write_bytes(b"PK")
    (tmp_path / "ok.txt").write_text("x", encoding="utf-8")

    ws = ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())
    root_tree = await ws.list_tree(".", max_depth=2)
    root_paths = {e.path for e in root_tree.entries}
    assert "ok.txt" in root_paths
    assert "noise.zip" not in root_paths
    assert "attachments" in root_paths
    assert "attachments/pack.zip" in root_paths

    att_tree = await ws.list_tree("attachments", max_depth=1)
    assert {e.path for e in att_tree.entries} == {"attachments/pack.zip"}


async def test_list_tree_reveals_material_png(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "shot.png").write_bytes(b"png")
    (tmp_path / "src" / "other.png").write_bytes(b"png")
    (tmp_path / "ok.txt").write_text("x", encoding="utf-8")

    ws = ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())
    ws.ai_list_materials = frozenset({"src/shot.png"})
    tree = await ws.list_tree(".", max_depth=2)
    paths = {e.path for e in tree.entries}
    assert "ok.txt" in paths
    assert "src/shot.png" in paths
    assert "src/other.png" not in paths


async def test_index_files_skips_internal_zone_db_and_media(tmp_path: Path):
    (tmp_path / "ok.txt").write_text("x", encoding="utf-8")
    (tmp_path / "hero.png").write_bytes(b"png")
    ac = tmp_path / "AgentCore" / "index"
    ac.mkdir(parents=True)
    (ac / "code_search.db").write_bytes(b"db")
    (tmp_path / "AgentCore" / "规则").mkdir(parents=True)
    (tmp_path / "AgentCore" / "规则" / "x.md").write_text("r", encoding="utf-8")
    (tmp_path / "index").mkdir()
    (tmp_path / "index" / "user.py").write_text("u", encoding="utf-8")
    (tmp_path / "noise.db").write_bytes(b"db")
    git = tmp_path / ".git"
    git.mkdir()
    (git / "config").write_text("g", encoding="utf-8")
    nm = tmp_path / "node_modules" / "dep"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("x", encoding="utf-8")

    paths, _ = await ServerWorkspace(
        root=tmp_path, sandbox=SubprocessSandbox()
    ).index_files()
    assert paths == ["AgentCore/规则/x.md", "index/user.py", "ok.txt"]


async def test_list_shows_media_hides_system_noise(tmp_path: Path):
    """User UI shares ``list`` — media visible; ``*.db`` / noise dirs hidden."""
    (tmp_path / "ok.txt").write_text("x", encoding="utf-8")
    (tmp_path / "hero.png").write_bytes(b"png")
    (tmp_path / "noise.db").write_bytes(b"db")
    (tmp_path / "AgentCore" / "index").mkdir(parents=True)
    (tmp_path / "AgentCore" / "规则").mkdir(parents=True)
    names = {
        e.path
        for e in await ServerWorkspace(
            root=tmp_path, sandbox=SubprocessSandbox()
        ).list(".", "*")
    }
    assert "ok.txt" in names
    assert "hero.png" in names
    assert "noise.db" not in names
    assert "AgentCore" in names
    ac_names = {
        e.path.rsplit("/", 1)[-1]
        for e in await ServerWorkspace(
            root=tmp_path, sandbox=SubprocessSandbox()
        ).list("AgentCore", "*")
    }
    assert "规则" in ac_names
    assert "index" not in ac_names


async def test_recursive_list_hides_internal_zones_keeps_bare_index(tmp_path: Path):
    """Cloud UI expands AgentCore via recursive list — zones must not leak."""
    (tmp_path / "ok.txt").write_text("x", encoding="utf-8")
    (tmp_path / "AgentCore" / "index").mkdir(parents=True)
    (tmp_path / "AgentCore" / "index" / "code_search.db").write_bytes(b"db")
    (tmp_path / "AgentCore" / "trash").mkdir(parents=True)
    (tmp_path / "AgentCore" / "baselines").mkdir(parents=True)
    (tmp_path / "AgentCore" / "规则").mkdir(parents=True)
    (tmp_path / "AgentCore" / "规则" / "r.md").write_text("r", encoding="utf-8")
    (tmp_path / "index").mkdir()
    (tmp_path / "index" / "user.py").write_text("u", encoding="utf-8")
    git = tmp_path / ".git"
    git.mkdir()
    (git / "config").write_text("g", encoding="utf-8")

    paths = {
        e.path
        for e in await ServerWorkspace(
            root=tmp_path, sandbox=SubprocessSandbox()
        ).list(".", "**/*")
    }
    assert "ok.txt" in paths
    assert "AgentCore" in paths
    assert "AgentCore/规则" in paths
    assert "AgentCore/规则/r.md" in paths
    assert "index" in paths
    assert "index/user.py" in paths
    assert "AgentCore/index" not in paths
    assert "AgentCore/trash" not in paths
    assert "AgentCore/baselines" not in paths
    assert not any(p == "AgentCore/index" or p.startswith("AgentCore/index/") for p in paths)
    assert not any(p == ".git" or p.startswith(".git/") for p in paths)


async def test_list_and_tree_ignore_pytest_tmp(tmp_path: Path):
    """``.pytest_tmp`` is product noise (alongside ``.pytest_cache``)."""
    (tmp_path / "ok.txt").write_text("x", encoding="utf-8")
    poison = tmp_path / ".pytest_tmp" / "locked"
    poison.mkdir(parents=True)
    (poison / "secret.py").write_text("TODO poison", encoding="utf-8")

    ws = ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())
    names = {e.path for e in await ws.list(".", "*")}
    assert "ok.txt" in names
    assert ".pytest_tmp" not in names

    tree = await ws.list_tree(".", max_depth=3)
    paths = {e.path for e in tree.entries}
    assert "ok.txt" in paths
    assert not any(p == ".pytest_tmp" or p.startswith(".pytest_tmp/") for p in paths)


async def test_list_tree_skips_access_denied_child(tmp_path: Path, monkeypatch):
    """Per-dir PermissionError soft-skips with warning — not WorkspaceIOError."""
    (tmp_path / "ok.txt").write_text("hi", encoding="utf-8")
    locked = tmp_path / "locked_dir"
    locked.mkdir()
    (locked / "secret.txt").write_text("nope", encoding="utf-8")

    real_iterdir = Path.iterdir

    def fake_iterdir(self: Path):
        try:
            if self.resolve() == locked.resolve():
                raise PermissionError(13, "Permission denied", str(self))
        except OSError:
            # resolve itself can fail on some platforms; fall through to name match
            if self.name == "locked_dir" and self.parent.resolve() == tmp_path.resolve():
                raise PermissionError(13, "Permission denied", str(self)) from None
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", fake_iterdir)

    ws = ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())
    tree = await ws.list_tree(".", max_depth=3)
    paths = {e.path for e in tree.entries}
    assert "ok.txt" in paths
    assert not any(p.startswith("locked_dir/") for p in paths)
    assert any("locked_dir" in w for w in tree.warnings)


async def test_list_tree_name_filter_emits_matches_not_prefix_dirs(tmp_path: Path):
    """Name search must not spend max_entries on unmatched directories."""
    deep = tmp_path / "d0" / "d1" / "d2" / "d3"
    deep.mkdir(parents=True)
    (deep / "hit.py").write_text("x", encoding="utf-8")
    for i in range(8):
        (tmp_path / f"pad{i}").mkdir()

    ws = ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())
    tree = await ws.list_tree(".", pattern="*.py", max_depth=8, max_entries=5)
    paths = {e.path for e in tree.entries}
    assert "d0/d1/d2/d3/hit.py" in paths
    assert "pad0" not in paths
    assert all(e.path.endswith(".py") or e.path.endswith(".PY") for e in tree.entries)
