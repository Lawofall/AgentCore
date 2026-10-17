"""Workspace markdown 带法: convention dir + file frontmatter."""

from __future__ import annotations

from agentcore.memory.workspace_files import (
    discover_workspace_rules,
    matching_workspace_rule,
    refuse_always_rule_write,
)
from agentcore.workspace.protocol import GlobFilesResult, PathNotFound


class _Reader:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    async def read(self, path: str) -> str:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def glob_files(self, query):  # noqa: ANN001
        directory = query.directory.replace("\\", "/").rstrip("/")
        prefix = f"{directory}/"
        hits = [
            path
            for path in self.files
            if path == directory or path.startswith(prefix)
        ]
        if not hits:
            raise PathNotFound(query.directory)
        return GlobFilesResult(paths=hits)


async def test_file_frontmatter_is_the_channel():
    lives = await discover_workspace_rules(
        _Reader(
            {
                "AgentCore/rules/guide.md": (
                    "---\napply: always\n---\n用中文"
                )
            }
        )
    )
    assert len(lives) == 1
    assert lives[0].apply == "always"
    assert lives[0].name == "guide"
    assert "用中文" in lives[0].content


async def test_reread_picks_up_a_model_edit():
    first = await discover_workspace_rules(
        _Reader({"AgentCore/rules/guide.md": "---\napply: always\n---\n第一版"})
    )
    second = await discover_workspace_rules(
        _Reader({"AgentCore/rules/guide.md": "---\napply: always\n---\n第二版"})
    )
    assert "第一版" in first[0].content
    assert "第二版" in second[0].content


async def test_missing_file_is_skipped():
    lives = await discover_workspace_rules(_Reader({}))
    assert lives == []


async def test_on_demand_without_description_stays_out():
    lives = await discover_workspace_rules(
        _Reader({"AgentCore/rules/guide.md": "---\napply: on_demand\n---\n正文"})
    )
    assert lives == []


async def test_missing_apply_defaults_to_on_demand():
    lives = await discover_workspace_rules(
        _Reader(
            {
                "AgentCore/rules/guide.md": (
                    "---\ndescription: 需要时翻\n---\n按需正文"
                )
            }
        )
    )
    assert len(lives) == 1
    assert lives[0].apply == "on_demand"
    assert lives[0].description == "需要时翻"


async def test_nested_rule_consult_name():
    lives = await discover_workspace_rules(
        _Reader(
            {
                "AgentCore/rules/team/约定.md": (
                    "---\napply: on_demand\ndescription: 组约定\n---\n正文"
                )
            }
        )
    )
    assert lives[0].name == "team/约定"
    assert matching_workspace_rule(lives, "team/约定") is lives[0]


async def test_always_quota_refuses_growth(monkeypatch):
    from agentcore.config import settings

    monkeypatch.setattr(settings, "memory_always_max_chars", 20)
    reader = _Reader(
        {"AgentCore/rules/old.md": "---\napply: always\n---\n1234567890"}
    )
    denied = await refuse_always_rule_write(
        reader,
        "AgentCore/rules/new.md",
        "---\napply: always\n---\nABCDEFGHIJKLMNOPQRSTUVWXYZ",
        old_content=None,
    )
    assert denied is not None
    allowed = await refuse_always_rule_write(
        reader,
        "AgentCore/rules/later.md",
        "---\napply: on_demand\ndescription: 翻\n---\n按需",
        old_content=None,
    )
    assert allowed is None
