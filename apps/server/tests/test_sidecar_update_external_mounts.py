"""Sidecar ``updateExternalMounts`` RPC — mid-turn abs snapshot onto live backends."""

from __future__ import annotations

import json
from typing import Any

import pytest

from agentcore.sidecar import protocol
from agentcore.sidecar.server import SidecarServer
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def _recorder() -> tuple[list[dict[str, Any]], Any]:
    sent: list[dict[str, Any]] = []

    async def write_line(line: str) -> None:
        sent.append(json.loads(line))

    return sent, write_line


def _response(sent: list[dict[str, Any]], request_id: Any) -> dict[str, Any]:
    return next(m for m in sent if m.get("id") == request_id)


async def _call_update(
    server: SidecarServer,
    request_id: int,
    params: dict[str, Any],
) -> None:
    await server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "updateExternalMounts",
                "params": params,
            }
        )
    )


@pytest.mark.asyncio
async def test_update_external_mounts_attaches_to_live_backend(tmp_path):
    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    backend = ServerWorkspace(
        root=tmp_path,
        sandbox=SubprocessSandbox(),
        root_label="ws",
        location="local",
    )
    server._register_live_backend("c1", backend)
    ext = tmp_path / "ext"
    ext.mkdir()
    await _call_update(
        server,
        1,
        {
            "conversationId": "c1",
            "externalMounts": [
                {
                    "alias": "desk",
                    "absPath": str(ext),
                    "rootId": "r1",
                    "label": "桌面",
                    "mode": "organize",
                }
            ],
        },
    )
    resp = _response(sent, 1)
    assert resp["result"] == {"ok": True, "attached": True, "count": 1}
    mount = backend._mounts["desk"]  # noqa: SLF001
    assert mount.abs_path == str(ext)
    assert mount.mode == "organize"
    assert mount.root_id == "r1"


@pytest.mark.asyncio
async def test_update_external_mounts_no_live_turn_ok():
    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    await _call_update(
        server,
        1,
        {
            "conversationId": "c1",
            "externalMounts": [
                {
                    "alias": "desk",
                    "absPath": "/tmp/x",
                    "rootId": "r1",
                    "label": "桌面",
                    "mode": "readonly",
                }
            ],
        },
    )
    resp = _response(sent, 1)
    assert resp["result"] == {"ok": True, "attached": False}


@pytest.mark.asyncio
async def test_update_external_mounts_empty_snapshot_clears(tmp_path):
    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    backend = ServerWorkspace(
        root=tmp_path,
        sandbox=SubprocessSandbox(),
        root_label="ws",
        location="local",
    )
    from agentcore.workspace.external_mounts import ExternalMount

    backend.attach_external_mounts(
        {
            "desk": ExternalMount(
                alias="desk",
                root_id="r1",
                label="桌面",
                abs_path=str(tmp_path),
                mode="readonly",
            )
        }
    )
    server._register_live_backend("c1", backend)
    await _call_update(
        server, 1, {"conversationId": "c1", "externalMounts": []}
    )
    resp = _response(sent, 1)
    assert resp["result"] == {"ok": True, "attached": True, "count": 0}
    assert backend._mounts == {}  # noqa: SLF001


@pytest.mark.asyncio
async def test_update_external_mounts_requires_conversation_id():
    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    await _call_update(server, 1, {"externalMounts": []})
    resp = _response(sent, 1)
    assert resp["error"]["code"] == protocol.INVALID_PARAMS
