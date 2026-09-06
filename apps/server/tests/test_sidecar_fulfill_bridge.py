"""本机引擎（sidecar）自带履约方：进程内中枢注册 + 帧走 stdio 直投桌面。

Covers the seam that made every native-mode CLIENT_TOOL fail with
「no fulfiller（无履约方）」: the channels deliver through the in-process
:class:`FulfillerHub`, which had a registration point only on the cloud
``GET /v1/fulfill`` route.

Also covers its root-scoped sibling: 跨桌派工 / 裸聊派工 resolve a root the turn's
``localRootId`` never named, which used to leave every worker file op on the
target project reading 「未声明持有本会话的本地目录」.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcore.fulfill.dispatch import DeliverResult, deliver_client_tool
from agentcore.fulfill.hub import FULFILL_CHANNELS, FulfillerHub
from agentcore.runtime.delegate.target_desktop import (
    LocalRootClaimBook,
    apply_target_desktop,
)
from agentcore.runtime.events.client_tool_reattach import (
    CHANNEL_BOARD,
    CHANNEL_BOARD_READ,
    CHANNEL_EXTERNAL_MOUNT,
    CHANNEL_HOST,
    CHANNEL_MCP,
    CHANNEL_WORKSPACE,
    push_client_tool_required,
)
from agentcore.runtime.events.desktop import host_op_required
from agentcore.runtime.interaction import InteractionKind, InteractionRegistry
from agentcore.sidecar.fulfill_bridge import FULFILL_FRAME_METHOD, SidecarFulfillBridge
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.workspace.locate import LocalBinding

# conftest patches fulfill delivery to always succeed; this suite asserts the
# real routing (does the sidecar session actually receive the frame?).
pytestmark = pytest.mark.real_fulfill_dispatch

USER = "u-sidecar"
CID = "c-sidecar"


class Link:
    """Stand-in for ``SidecarServer._send`` (the stdio JSON-RPC line sender)."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.arrived = asyncio.Event()

    async def __call__(self, message: dict[str, Any]) -> None:
        self.sent.append(message)
        self.arrived.set()

    async def next_frame(self, timeout: float = 1.0) -> dict[str, Any]:
        await asyncio.wait_for(self.arrived.wait(), timeout)
        self.arrived.clear()
        message = self.sent[-1]
        assert message["method"] == FULFILL_FRAME_METHOD
        frame = message["params"]["event"]
        assert isinstance(frame, dict)
        return frame


@pytest.fixture
def hub() -> FulfillerHub:
    return FulfillerHub()


@pytest.fixture
def link() -> Link:
    return Link()


@pytest.fixture
def bridge(hub: FulfillerHub, link: Link):
    b = SidecarFulfillBridge(link, hub=hub, device_id="dev-sidecar")
    yield b
    b.close()


async def test_bind_user_registers_every_channel(bridge, hub: FulfillerHub) -> None:
    bridge.bind_user(USER)

    session = hub.get_session(USER, "dev-sidecar")
    assert session is not None
    assert session.caps == FULFILL_CHANNELS
    assert session.platform == "sidecar"
    # 未绑定根：无根 op（host/mcp/board/board_read/external_mount/terminal）即可履约。
    assert session.roots == set()


@pytest.mark.parametrize(
    "channel",
    [
        CHANNEL_HOST,
        CHANNEL_MCP,
        CHANNEL_BOARD,
        CHANNEL_BOARD_READ,
        CHANNEL_EXTERNAL_MOUNT,
        CHANNEL_WORKSPACE,
    ],
)
async def test_all_channels_reach_the_local_fulfiller(
    bridge, hub: FulfillerHub, channel: str
) -> None:
    bridge.bind_user(USER)

    status = deliver_client_tool(
        USER,
        CID,
        channel,
        None,
        host_op_required(request_id="r1", conversation_id=CID, op="host_shell", args={}),
        hub=hub,
    )

    assert status is DeliverResult.DELIVERED


async def test_frame_is_pushed_onto_the_stdio_link(
    bridge, hub: FulfillerHub, link: Link
) -> None:
    bridge.bind_user(USER)

    deliver_client_tool(
        USER,
        CID,
        CHANNEL_HOST,
        None,
        host_op_required(
            request_id="r-push", conversation_id=CID, op="host_shell", args={"command": "ls"}
        ),
        hub=hub,
    )

    frame = await link.next_frame()
    assert frame["type"] == "host_op_required"
    assert frame["payload"]["request_id"] == "r-push"
    assert frame["payload"]["conversation_id"] == CID


async def test_push_client_tool_required_no_longer_settles_no_fulfiller(
    bridge, hub: FulfillerHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The engine-facing seam: with a bound bridge the op stays pending (真能履约)."""
    monkeypatch.setattr(
        "agentcore.fulfill.dispatch.default_fulfiller_hub", lambda: hub, raising=True
    )
    bridge.bind_user(USER)
    registry = InteractionRegistry()
    fut = registry.create("r-seam", CID, kind=InteractionKind.CLIENT_TOOL, payload={})

    delivered = push_client_tool_required(
        user_id=USER,
        conversation_id=CID,
        channel=CHANNEL_HOST,
        root_id=None,
        event=host_op_required(
            request_id="r-seam", conversation_id=CID, op="host_shell", args={}
        ),
        registry=registry,
        request_id="r-seam",
        error_kind="HostOpError",
        error_detail="no fulfiller（无履约方）",
    )

    assert delivered is True
    assert not fut.done()


async def test_root_scoped_workspace_needs_a_declared_root(
    bridge, hub: FulfillerHub
) -> None:
    """Bound but rootless: refused as ``root_not_held`` — the engine IS online."""
    bridge.bind_user(USER)

    def deliver() -> DeliverResult:
        return deliver_client_tool(
            USER,
            CID,
            CHANNEL_WORKSPACE,
            "root-1",
            host_op_required(request_id="r-w", conversation_id=CID, op="read", args={}),
            hub=hub,
        )

    assert deliver() is DeliverResult.ROOT_NOT_HELD
    bridge.declare_root("root-1")
    assert deliver() is DeliverResult.DELIVERED


async def _apply_cross_desk_target(
    *, session_folder_id: str | None, target_root_id: str
) -> None:
    """Run the real delegate target-desk wiring for a folder bound to another root.

    Only the two cloud/DB lookups are faked (folder row, worker prompt rebuild);
    backend construction — the seam under test — is the production one.
    """
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        backend=SimpleNamespace(location="server", _channel=None),  # type: ignore[arg-type]
        user_id=USER,
        conversation_id=CID,
    )
    binding = SimpleNamespace(
        folder_id="f-target",
        rel_path="f-target",
        name="目标项目",
        local_binding=LocalBinding(root_id=target_root_id, root_label="目标项目"),
    )

    async def _fake_rebuild(**_kwargs: Any) -> str:
        return "TARGET_PROMPT"

    with (
        patch(
            "agentcore.runtime.delegate.target_desktop.load_target_folder_binding",
            new=AsyncMock(return_value=binding),
        ),
        patch(
            "agentcore.runtime.delegate.target_desktop.rebuild_worker_prompt_for_target",
            new=_fake_rebuild,
        ),
    ):
        applied = await apply_target_desktop(
            target_folder_id="f-target",
            session_folder_id=session_folder_id,
            env_system_prompt="OLD",
            base_tool_context=ctx,
            worker_tools=ToolRegistry(),
            sink=MagicMock(),
            local_root_claims=LocalRootClaimBook(),
        )
    assert applied.tool_ctx.backend.location == "local"


def _deliver_workspace(hub: FulfillerHub, root_id: str) -> DeliverResult:
    return deliver_client_tool(
        USER,
        CID,
        CHANNEL_WORKSPACE,
        root_id,
        host_op_required(request_id="r-x", conversation_id=CID, op="read", args={}),
        hub=hub,
    )


async def test_cross_desk_delegate_reaches_the_target_root(
    bridge, hub: FulfillerHub
) -> None:
    """派到另一个项目：目标桌的根由本进程自己声明，队员才读写得了那个文件夹。

    ``localRootId`` 只覆盖本回合会话那张桌；目标桌的 root 来自目标文件夹的绑定。
    """
    bridge.bind_user(USER)
    bridge.declare_root("root-session")  # 本回合 localRootId

    assert _deliver_workspace(hub, "root-target") is DeliverResult.ROOT_NOT_HELD

    await _apply_cross_desk_target(
        session_folder_id="f-birth", target_root_id="root-target"
    )

    assert _deliver_workspace(hub, "root-target") is DeliverResult.DELIVERED
    # 会话自己那张桌不受影响（声明是扩集合，不是替换）。
    assert _deliver_workspace(hub, "root-session") is DeliverResult.DELIVERED
    # root 是授权边界：没用到的根不会被顺手声明。
    assert _deliver_workspace(hub, "root-untouched") is DeliverResult.ROOT_NOT_HELD


async def test_bare_chat_delegate_reaches_the_target_root(
    bridge, hub: FulfillerHub
) -> None:
    """裸聊派工：``localRootId`` 为 null，一个根都没声明过，目标桌仍要够得着。"""
    bridge.bind_user(USER)

    assert _deliver_workspace(hub, "root-target") is DeliverResult.ROOT_NOT_HELD

    await _apply_cross_desk_target(session_folder_id=None, target_root_id="root-target")

    assert _deliver_workspace(hub, "root-target") is DeliverResult.DELIVERED


async def test_rebinding_a_new_account_moves_the_session(
    bridge, hub: FulfillerHub
) -> None:
    """Probe-spawned sidecars initialize as ``local``; the turn brings the real id."""
    bridge.bind_user("local")
    bridge.declare_root("root-1")
    bridge.bind_user(USER)

    assert hub.get_session("local", "dev-sidecar") is None
    assert hub.has_fulfiller("local", root_id=None, channel=CHANNEL_HOST) is False
    assert hub.has_fulfiller(USER, root_id="root-1", channel=CHANNEL_WORKSPACE) is True


async def test_close_removes_the_fulfiller(bridge, hub: FulfillerHub) -> None:
    bridge.bind_user(USER)
    bridge.close()

    assert hub.get_session(USER, "dev-sidecar") is None
    assert hub.has_fulfiller(USER, root_id=None, channel=CHANNEL_HOST) is False
