"""Convention-dir workspace markdown injects from the file, not an index."""

from __future__ import annotations

import agentcore.db.base as db_base
from agentcore.db.repositories import FolderRepository, UserRepository
from agentcore.memory.rules_injection import load_on_demand_user_rules, load_turn_rule_view
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


async def test_turn_injects_workspace_rule_file(session_factory, monkeypatch):
    monkeypatch.setattr(db_base, "async_session_factory", session_factory)
    async with session_factory() as session:
        user = await UserRepository(session).create(username="ws_rules", display_name="ws")
        folder = await FolderRepository(session).create(user_id=user.user_id, name="桌")
        uid = user.user_id
        folder_id = folder.id

    files = {
        "AgentCore/rules/guide.md": "---\napply: always\n---\n常驻正文",
        "AgentCore/rules/later.md": (
            "---\napply: on_demand\ndescription: 需要时翻\n---\n按需正文"
        ),
    }
    view = await load_turn_rule_view(
        uid,
        folder_id=folder_id,
        reader=_Reader(files),
    )
    assert "常驻正文" in view.settings
    assert "按需正文" not in view.settings

    again = await load_turn_rule_view(
        uid,
        folder_id=folder_id,
        reader=_Reader(
            {
                **files,
                "AgentCore/rules/guide.md": "---\napply: always\n---\n改过的正文",
            }
        ),
    )
    assert "改过的正文" in again.settings
    assert "常驻正文" not in again.settings

    catalog = await load_on_demand_user_rules(
        uid, folder_id=folder_id, reader=_Reader(files)
    )
    assert any(item.name == "later" and item.summary == "需要时翻" for item in catalog)
    assert all(item.name != "guide" for item in catalog)
