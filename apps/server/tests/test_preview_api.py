"""POST /v1/preview/token — access session → short preview URL."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentcore.api.routes import preview as preview_mod
from agentcore.api.routes.preview import PreviewTokenRequest, mint_preview_token
from agentcore.core.errors import ConflictError, NotFoundError, PreviewUnavailableError

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _stub_workspace(monkeypatch):
    async def _coords(user_id, conv, session):
        del session
        return {
            "user_id": user_id,
            "folder_id": conv.folder_id,
            "folder_rel_path": conv.folder_id or "scratch",
            "conversation_id": conv.id,
        }

    monkeypatch.setattr(preview_mod, "_workspace_coords", _coords)
    monkeypatch.setattr(
        preview_mod,
        "build_server_workspace",
        lambda **_kw: SimpleNamespace(location="server", root=Path("/tmp/preview-ws")),
    )
    monkeypatch.setattr(
        preview_mod.desk_process_mod,
        "ensure_cloud_preview",
        AsyncMock(return_value=None),
    )


def _user() -> SimpleNamespace:
    return SimpleNamespace(user_id="u1")


def _owned_repo(conv_id: str = "c1") -> SimpleNamespace:
    conv = SimpleNamespace(id=conv_id, folder_id="f1")
    return SimpleNamespace(get_by_id=AsyncMock(return_value=conv))


def _missing_repo() -> SimpleNamespace:
    return SimpleNamespace(get_by_id=AsyncMock(return_value=None))


def _running(*ports: int) -> SimpleNamespace:
    return SimpleNamespace(status="running", http_ports=ports)


async def _mint(
    *,
    conv_repo,
    port: int | None = None,
    conversation_id: str = "c1",
    process_id: str = "p1",
):
    return await mint_preview_token(
        PreviewTokenRequest(
            conversation_id=conversation_id,
            process_id=process_id,
            port=port,
        ),
        _user(),
        conv_repo=conv_repo,
        session=SimpleNamespace(),
    )


async def test_mint_preview_foreign_or_missing_conversation_404():
    with pytest.raises(NotFoundError) as exc:
        await _mint(conv_repo=_missing_repo())
    assert exc.value.status_code == 404


@pytest.mark.parametrize(
    "found",
    [
        None,
        SimpleNamespace(status="exited", http_ports=(5173,)),
        SimpleNamespace(status="running", http_ports=()),
    ],
)
async def test_mint_preview_no_port_409(monkeypatch, found):
    monkeypatch.setattr(
        preview_mod.desk_process_mod,
        "lookup_cloud_preview",
        lambda *_a, **_k: found,
    )
    with pytest.raises(ConflictError) as exc:
        await _mint(conv_repo=_owned_repo())
    assert exc.value.status_code == 409
    assert preview_mod.desk_process_mod.ensure_cloud_preview.await_count == 0


async def test_mint_preview_multiple_ports_without_choice_409(monkeypatch):
    monkeypatch.setattr(
        preview_mod.desk_process_mod,
        "lookup_cloud_preview",
        lambda *_a, **_k: _running(5173, 4173),
    )
    with pytest.raises(ConflictError) as exc:
        await _mint(conv_repo=_owned_repo())
    assert exc.value.status_code == 409
    assert "5173" in exc.value.message
    assert "4173" in exc.value.message
    assert preview_mod.desk_process_mod.ensure_cloud_preview.await_count == 0


async def test_mint_preview_origin_unset_503(monkeypatch):
    monkeypatch.setattr(
        preview_mod.desk_process_mod,
        "lookup_cloud_preview",
        lambda *_a, **_k: _running(5173),
    )
    monkeypatch.setattr(preview_mod.settings, "preview_public_base_url", "")
    with pytest.raises(PreviewUnavailableError) as exc:
        await _mint(conv_repo=_owned_repo())
    assert exc.value.status_code == 503
    assert exc.value.code == "PREVIEW_UNAVAILABLE"
    assert preview_mod.desk_process_mod.ensure_cloud_preview.await_count == 0


async def test_mint_preview_url_uses_origin_and_resolved_port(monkeypatch):
    monkeypatch.setattr(
        preview_mod.desk_process_mod,
        "lookup_cloud_preview",
        lambda *_a, **_k: _running(5173),
    )
    monkeypatch.setattr(
        preview_mod.settings,
        "preview_public_base_url",
        "https://preview.example/",
    )
    monkeypatch.setattr(preview_mod, "create_preview_token", lambda *_a, **_k: "ticket")
    resp = await _mint(conv_repo=_owned_repo())
    dumped = resp.model_dump()
    assert dumped == {
        "url": "https://preview.example/enter?t=ticket",
        "expires_in_sec": preview_mod.settings.preview_token_expire_minutes * 60,
        "port": 5173,
    }
    assert "token" not in dumped
    preview_mod.desk_process_mod.ensure_cloud_preview.assert_awaited_once()
    ws_path = preview_mod.desk_process_mod.ensure_cloud_preview.await_args.args[0]
    assert isinstance(ws_path, str)
    assert ws_path.endswith("preview-ws")


async def test_mint_preview_desk_failure_503(monkeypatch):
    monkeypatch.setattr(
        preview_mod.desk_process_mod,
        "lookup_cloud_preview",
        lambda *_a, **_k: _running(5173),
    )
    monkeypatch.setattr(
        preview_mod.settings,
        "preview_public_base_url",
        "https://preview.example",
    )
    monkeypatch.setattr(
        preview_mod.desk_process_mod,
        "ensure_cloud_preview",
        AsyncMock(side_effect=RuntimeError("sandboxd down")),
    )
    with pytest.raises(PreviewUnavailableError) as exc:
        await _mint(conv_repo=_owned_repo())
    assert exc.value.status_code == 503
    assert exc.value.code == "PREVIEW_UNAVAILABLE"
