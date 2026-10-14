"""named_entity_fanout 用户扫硬拒已移除：不再因点名对比少派拒绝 delegate."""

from __future__ import annotations

import importlib

import pytest


def test_named_entity_fanout_module_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("agentcore.runtime.graph.drive.named_entity_fanout")
