"""Sitting-desk label for ``<工作区>``; CEO no longer injects ``<文件夹清单>``."""

import agentcore.runtime.pipeline.prepare as prepare_mod
from agentcore.runtime.pipeline.prepare import resolve_desk_folder_label
from agentcore.runtime.resolve.prompt import (
    assemble_system_prompt,
    compose_ceo_chat_prompt,
)
from agentcore.runtime.skills import build_system_skill_registry


def test_compose_ceo_omits_folder_catalog_tag():
    base = assemble_system_prompt(rules_markdown="## 偏好\n- 用中文\n")
    ceo = compose_ceo_chat_prompt(
        base,
        skill_registry=build_system_skill_registry(),
        ceo_tool_names={"delegate", "consult"},
        workspace_context=(
            "<工作区>\n工作台：本会话出生桌=`设计/图标`（folder_id=`f1`）。\n</工作区>"
        ),
    )
    assert "<文件夹清单>" not in ceo
    assert "</文件夹清单>" not in ceo
    assert "<工作区>" in ceo
    assert "设计/图标" in ceo
    assert "<设定>" in ceo
    assert "用中文" in ceo


async def test_resolve_desk_folder_label_reads_get_by_id(monkeypatch):
    class _Folder:
        def __init__(self, fid: str, name: str, rel_path: str | None) -> None:
            self.id = fid
            self.name = name
            self.rel_path = rel_path

    class _Repo:
        def __init__(self, session) -> None:  # noqa: ANN001
            self._session = session

        async def get_by_id(self, folder_id: str, *, user_id: str):
            assert user_id == "u1"
            if folder_id == "fid-pay":
                return _Folder("fid-pay", "支付", "工作/支付")
            if folder_id == "fid-local":
                return _Folder("fid-local", "博客", None)
            if folder_id == "fid-missing":
                return None
            raise AssertionError(f"unexpected folder_id={folder_id}")

    class _CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(prepare_mod, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(prepare_mod, "FolderRepository", _Repo)

    assert await resolve_desk_folder_label("u1", "fid-pay") == "工作/支付"
    assert await resolve_desk_folder_label("u1", "fid-local") == "博客"
    assert await resolve_desk_folder_label("u1", "fid-missing") is None
    assert await resolve_desk_folder_label("u1", None) is None
    assert await resolve_desk_folder_label("u1", "  ") is None


async def test_resolve_desk_folder_label_failure_returns_none(monkeypatch):
    class _Repo:
        def __init__(self, session) -> None:  # noqa: ANN001
            pass

        async def get_by_id(self, folder_id: str, *, user_id: str):
            raise RuntimeError("db down")

    class _CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(prepare_mod, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(prepare_mod, "FolderRepository", _Repo)

    assert await resolve_desk_folder_label("u1", "fid-pay") is None
