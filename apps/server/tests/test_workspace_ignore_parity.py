"""Workspace hide-rule parity ratchet (Python ↔ desktop TypeScript).

Covers the ignore lists and the internal-zone names / ``AgentCore/<zone>`` path
forms, which are hand-copied into five files (``stage_dirs.py``, ``_paths.py``,
main ``workspaceIgnore.ts``, renderer ``workspaceSource.ts``, renderer
``folderUpload.ts``). The drift cases below are the real check: each one edits a
single side in memory and asserts the gate goes red, so forgetting one file when
a zone is added cannot stay green.
"""

from __future__ import annotations

import pytest

from agentcore.workspace import ignore_parity as ip

ZONE_NAMES = frozenset({"index", "trash", "baselines", "versions"})
ZONE_RELS = frozenset(f"GAgent/{zone}" for zone in ZONE_NAMES)


def _sources() -> dict[str, str]:
    return {
        "py_src": ip.py_paths_file().read_text(encoding="utf-8"),
        "ts_src": ip.ts_ignore_file().read_text(encoding="utf-8"),
        "stage_src": ip.stage_dirs_file().read_text(encoding="utf-8"),
        "renderer_src": ip.renderer_zones_file().read_text(encoding="utf-8"),
        "upload_src": ip.renderer_upload_file().read_text(encoding="utf-8"),
    }


def _compare(src: dict[str, str]) -> list[str]:
    return ip.compare_sources(
        src["py_src"],
        src["ts_src"],
        stage_src=src["stage_src"],
        renderer_src=src["renderer_src"],
        upload_src=src["upload_src"],
    )


def _drift(src: dict[str, str], key: str, old: str, new: str) -> None:
    """Edit one side in place; fail loudly if the anchor text moved."""
    mutated = src[key].replace(old, new)
    assert mutated != src[key], f"drift anchor gone from {key}: {old!r}"
    src[key] = mutated


def test_ignore_lists_aligned():
    result = ip.run_ignore_parity()
    assert result.ok, result.errors


def test_simulate_drift_fails():
    result = ip.run_ignore_parity(simulate_drift=True)
    assert not result.ok
    assert any("__parity_drift_probe__" in e for e in result.errors)


def test_extract_round_trip_nonempty():
    py = ip.py_paths_file().read_text(encoding="utf-8")
    ts = ip.ts_ignore_file().read_text(encoding="utf-8")
    dirs_py = ip.extract_python_set(py, "IGNORED_DIRS")
    dirs_ts = ip.extract_typescript_set(ts, "LIST_FILES_SKIP_DIRS", kind="set")
    assert ".git" in dirs_py
    assert dirs_py == dirs_ts
    sys_py = ip.extract_python_set(py, "SYSTEM_IGNORED_FILE_SUFFIXES")
    assert ".db" in sys_py
    ai_py = ip.extract_python_set(py, "AI_NOISE_FILE_SUFFIXES")
    assert ".png" in ai_py


def test_all_three_zone_copies_parse_to_the_same_thing():
    src = _sources()
    copies = {
        "stage_dirs.py": ip.extract_python_zone_copy(src["stage_src"]),
        "workspaceIgnore.ts": ip.extract_typescript_zone_copy(src["ts_src"]),
        "workspaceSource.ts": ip.extract_renderer_zone_copy(src["renderer_src"]),
    }
    for where, copy in copies.items():
        assert copy.zone_names == ZONE_NAMES, where
        assert copy.rel_paths == ZONE_RELS, where


@pytest.mark.parametrize(
    ("key", "old", "new", "needle"),
    [
        pytest.param(
            "stage_src",
            'VERSIONS_ZONE_NAME = "versions"',
            'VERSIONS_ZONE_NAME = "snapshots"',
            "zone_names",
            id="python-renames-a-zone",
        ),
        pytest.param(
            "ts_src",
            '\n  "versions",\n]);',
            "\n]);",
            "zone_names (python ↔ desktop main)",
            id="main-process-misses-a-zone",
        ),
        pytest.param(
            "renderer_src",
            '"index", "trash", "baselines", "versions"',
            '"index", "trash", "baselines"',
            "renderer inline copy",
            id="renderer-inline-copy-misses-a-zone",
        ),
        pytest.param(
            "stage_src",
            'VERSIONS_REL = f"{AGENTCORE_ROOT}/{VERSIONS_ZONE_NAME}"\n',
            "",
            "zone_rel_paths (python)",
            id="python-forgets-the-rel-constant",
        ),
        pytest.param(
            "ts_src",
            "export const VERSIONS_REL = `${AGENTCORE_ROOT}/versions`;",
            "export const VERSIONS_REL = `${AGENTCORE_ROOT}/version`;",
            "zone_rel_paths (desktop main)",
            id="main-process-typos-the-path-form",
        ),
        pytest.param(
            "renderer_src",
            "const prefix = `GAgent/${zone}`;",
            "const prefix = `${zone}`;",
            "zone_rel_paths (renderer inline copy)",
            id="renderer-drops-the-agentcore-prefix",
        ),
    ],
)
def test_zone_drift_on_any_single_side_is_red(key: str, old: str, new: str, needle: str):
    src = _sources()
    _drift(src, key, old, new)
    errors = _compare(src)
    assert any(needle in e for e in errors), errors


@pytest.mark.parametrize(
    ("old", "new", "needle"),
    [
        pytest.param(
            '  "node_modules",\n',
            "",
            "dirs (python ↔ renderer upload)",
            id="upload-copy-drops-a-noise-dir",
        ),
        pytest.param(
            '  ".pyc",\n',
            "",
            "system_suffixes (python ↔ renderer upload)",
            id="upload-copy-drops-a-system-suffix",
        ),
    ],
)
def test_renderer_upload_copy_drift_is_red(old: str, new: str, needle: str):
    """Folder upload filters before any request, so its copy is a real hide rule."""
    src = _sources()
    _drift(src, "upload_src", old, new)
    errors = _compare(src)
    assert any(needle in e for e in errors), errors


def test_renderer_upload_copy_omits_ai_noise_suffixes():
    """A user uploading their own png / zip must keep it — AI noise is an AI-view rule."""
    upload = ip.renderer_upload_file().read_text(encoding="utf-8")
    system = ip.extract_typescript_set(
        upload, "SYSTEM_IGNORED_FILE_SUFFIXES", kind="array"
    )
    ai_noise = ip.extract_python_set(
        ip.py_paths_file().read_text(encoding="utf-8"), "AI_NOISE_FILE_SUFFIXES"
    )
    assert ".png" in ai_noise
    assert not (system & ai_noise)


def test_bare_zone_names_in_the_global_dir_set_are_red():
    """Path-aware zones must not be "fixed" by adding bare names to both sides."""
    src = _sources()
    _drift(src, "py_src", '        ".git",\n', '        ".git",\n        "index",\n')
    _drift(src, "ts_src", '  ".git",\n', '  ".git",\n  "index",\n')
    _drift(src, "upload_src", '  ".git",\n', '  ".git",\n  "index",\n')
    errors = _compare(src)
    # All three dir sets still agree, so only the leak check can catch this.
    assert not any(e.startswith("dirs") for e in errors), errors
    assert any("leaked into" in e for e in errors), errors
