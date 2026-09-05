"""Tests for the workspace git-clone service (决策⑤).

Hermetic: a throwaway local repo is created under ``tmp_path`` and cloned over a
``file://`` URL, so no network is touched. URL policy (http(s) only) and the
destination guard are tested without spawning git at all. Auto-skips if git is
not installed.
"""

import os
import subprocess
from pathlib import Path

import pytest

from agentcore.config import settings
from agentcore.workspace import git as gitmod
from agentcore.workspace.git import CloneError, _derive_dest_name, _git_clone, clone_repo

pytestmark = pytest.mark.skipif(not __import__("shutil").which("git"), reason="git not installed")


def _init_source_repo(path: Path) -> None:
    """Create a one-commit git repo at ``path`` (cloneable over file://)."""
    path.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, env=env)

    run("init")
    run("config", "user.email", "tester@example.com")
    run("config", "user.name", "Tester")
    (path / "README.md").write_text("hello clone\n", encoding="utf-8")
    run("add", "README.md")
    run("commit", "-m", "init")


# --- _git_clone mechanics (file:// source) ---


async def test_git_clone_copies_repo(tmp_path: Path):
    src = tmp_path / "src"
    _init_source_repo(src)
    dest = tmp_path / "out" / "cloned"

    await _git_clone(src.as_uri(), dest, depth=1, timeout=60)

    # Normalize EOL: git on Windows may apply autocrlf on checkout.
    text = (dest / "README.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert text == "hello clone\n"
    assert (dest / ".git").is_dir()


async def test_git_clone_failure_raises(tmp_path: Path):
    missing = (tmp_path / "does-not-exist").as_uri()
    with pytest.raises(CloneError):
        await _git_clone(missing, tmp_path / "out", depth=1, timeout=60)


# --- clone_repo URL + destination policy (no subprocess reached) ---


@pytest.mark.parametrize(
    "url",
    ["ssh://git@github.com/x/y.git", "file:///etc", "ftp://h/x", "git@github.com:x/y", "nonsense"],
)
async def test_clone_repo_rejects_non_http_urls(url: str, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    with pytest.raises(ValueError):
        await clone_repo(user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="c1", repo_url=url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/owner/repo.git",
        "http://127.0.0.1/owner/repo.git",
        "http://169.254.169.254/owner/repo.git",  # cloud metadata (link-local)
        "http://[::1]/owner/repo.git",
    ],
)
async def test_clone_repo_blocks_ssrf_private_targets(url: str, tmp_path: Path, monkeypatch):
    """SEC-006: an http(s) URL resolving to a local/internal/reserved address is
    refused by the shared SSRF guard (web_fetch / favicon parity), even though it
    passes the http(s) scheme check — so git clone can't reach internal services."""
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    with pytest.raises(ValueError):
        await clone_repo(user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="c1", repo_url=url)


async def test_clone_repo_rejects_existing_nonempty_dest(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    from agentcore.workspace.locate import resolve_workspace_root

    root = resolve_workspace_root(user_id="u1", folder_rel_path=None, conversation_id="c1")
    (root / "existing").mkdir()
    (root / "existing" / "f.txt").write_text("x", encoding="utf-8")

    with pytest.raises(ValueError):
        await clone_repo(
            user_id="u1",
            folder_id=None, folder_rel_path=None,
            conversation_id="c1",
            repo_url="https://example.com/owner/existing.git",
        )


async def test_clone_repo_blocks_traversal_dest(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    with pytest.raises(ValueError):
        await clone_repo(
            user_id="u1",
            folder_id=None, folder_rel_path=None,
            conversation_id="c1",
            repo_url="https://example.com/o/r.git",
            dest="../escape",
        )


# --- clone_repo end-to-end (file:// allowed via patched scheme list) ---


async def test_clone_repo_end_to_end(tmp_path: Path, monkeypatch):
    src = tmp_path / "myrepo"
    _init_source_repo(src)

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    # URL policy (http-only, netloc) is covered by the rejection test above; here we
    # stub it so a local file:// source can drive the real resolve/clone/return path.
    monkeypatch.setattr(gitmod, "_validate_url", lambda url: None)

    dest_rel = await clone_repo(
        user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", repo_url=src.as_uri()
    )
    assert dest_rel == "myrepo"

    from agentcore.workspace.locate import resolve_workspace_root

    root = resolve_workspace_root(user_id="u1", folder_rel_path="f1", conversation_id="c1")
    text = (root / "myrepo" / "README.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert text == "hello clone\n"


# --- dest-name derivation ---


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/owner/project.git", "project"),
        ("https://github.com/owner/project", "project"),
        ("https://example.com/a/b/", "b"),
        ("https://example.com/", "repo"),
    ],
)
def test_derive_dest_name(url: str, expected: str):
    assert _derive_dest_name(url) == expected


# --- G3 credential URL embedding ---


def test_embed_http_basic_auth_redacts_into_netloc():
    from agentcore.workspace.git_credentials import embed_http_basic_auth

    url = embed_http_basic_auth(
        "https://github.com/o/r.git",
        username="x-access-token",
        token="ghp_secret",
    )
    assert url.startswith("https://x-access-token:ghp_secret@github.com/")
    assert "o/r.git" in url


def test_sanitize_clone_error_strips_basic_auth():
    raw = "fatal: https://user:sekret@github.com/o/r.git/info/refs not valid"
    cleaned = gitmod._sanitize_clone_error(raw)
    assert "sekret" not in cleaned
    assert "***:***@" in cleaned


def test_looks_like_auth_failure():
    assert gitmod._looks_like_auth_failure("Authentication failed for 'https://…'")
    assert not gitmod._looks_like_auth_failure("fatal: repository path does not exist")

