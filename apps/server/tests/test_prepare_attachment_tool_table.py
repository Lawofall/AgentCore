"""Prepare / attachment seam: this turn's worker table is passed into the block.

``_build_attachment_context`` already has its own unit tests. This file only
locks the *caller*: ``prepare_fresh_turn`` must hand the assembled worker names
into that function. Omitting ``available_tools`` silently takes the conservative
branch (dead capability-aware copy) — the regression this pins.
"""

from __future__ import annotations

import pytest

from agentcore.config import settings
from agentcore.runtime.events import EventSink
from agentcore.runtime.pipeline.prepare import prepare_fresh_turn
from agentcore.tools.builtin import build_worker_registry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace

pytestmark = pytest.mark.anyio

CONV = "conv-attach-tools-seam"
USER = "u-attach-tools"

_MISSING = object()


class _AttachmentSeamCaptured(Exception):
    """Stop prepare once the attachment-block call has been observed."""


def _cloud(tmp_path) -> ServerWorkspace:
    return ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())


def _stub_prepare_io(monkeypatch, *, on_attach, on_registry) -> None:
    async def _empty_rules(*_a, **_k):
        return ""

    async def _empty_catalog(*_a, **_k):
        return []

    async def _no_vision(*_a, **_k):
        return None

    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare.assemble_turn_rules", _empty_rules
    )
    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare.load_folder_catalog", _empty_catalog
    )
    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare.resolve_vision_reader_for_conversation",
        _no_vision,
    )
    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare.build_worker_registry",
        on_registry,
    )
    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare._build_attachment_prompt",
        on_attach,
    )


async def _capture_available_tools(monkeypatch, backend) -> tuple[list[str], object]:
    assembled: list[str] = []

    def _spy_registry(**kwargs):
        registry = build_worker_registry(**kwargs)
        assembled[:] = list(registry.names)
        return registry

    captured: dict[str, object] = {}

    async def _spy_attach(*_a, **kwargs):
        captured["available_tools"] = kwargs.get("available_tools", _MISSING)
        raise _AttachmentSeamCaptured

    _stub_prepare_io(monkeypatch, on_attach=_spy_attach, on_registry=_spy_registry)

    with pytest.raises(_AttachmentSeamCaptured):
        await prepare_fresh_turn(
            conversation_id=CONV,
            user_id=USER,
            backend=backend,
            sink=EventSink(),
            folder_id=None,
            board_id=None,
            attachments=None,
            permission_axes=None,
            llm_credentials=None,
            x_client_platform="web",
        )

    return assembled, captured["available_tools"]


def _assert_caller_passed_this_turn_table(assembled: list[str], available: object) -> None:
    assert available is not _MISSING
    assert available is not None
    assert set(available) == set(assembled)


async def test_prepare_passes_worker_table_when_code_execute_assembled(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    names, available = await _capture_available_tools(monkeypatch, _cloud(tmp_path))
    assert "code_execute" in names
    _assert_caller_passed_this_turn_table(names, available)


async def test_prepare_passes_worker_table_when_code_execute_withheld(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "gvisor_enabled", False)
    monkeypatch.setattr(settings, "code_execute_cloud_enabled", False)
    names, available = await _capture_available_tools(monkeypatch, _cloud(tmp_path))
    assert "code_execute" not in names
    _assert_caller_passed_this_turn_table(names, available)
