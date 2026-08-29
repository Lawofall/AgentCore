"""Tests for on_demand user rules via unified ``consult`` (定案 B + 步 1).

Covers apply_mode API validation + rule slice of MergedConsultSource.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agentcore.api.routes.documents import DocumentCreateRequest, DocumentPatchRequest
from agentcore.core.types import ToolCategory
from agentcore.memory.rules_injection import OnDemandUserRule, rule_consult_name
from agentcore.runtime.context.consult_sources import MergedConsultSource, RuleConsultSource
from agentcore.runtime.context.consultable import Consultable, ConsultDirectoryEntry
from agentcore.runtime.resolve.prompt import (
    assemble_system_prompt,
    compose_ceo_chat_prompt,
    compose_worker_base_prompt,
    render_on_demand_directory,
)
from agentcore.runtime.skills import build_system_skill_registry
from agentcore.tools.builtin.consult import ConsultTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def _ctx(user_id: str = "u") -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id=user_id,
    )


def _rule_tool() -> ConsultTool:
    return ConsultTool(source=MergedConsultSource(rule=RuleConsultSource()))


def test_create_request_accepts_always_and_on_demand():
    assert DocumentCreateRequest(name="r.md", role="rule").apply_mode == "always"
    assert (
        DocumentCreateRequest(name="r.md", role="rule", apply_mode="on_demand").apply_mode
        == "on_demand"
    )


def test_create_request_rejects_conditional():
    with pytest.raises(ValidationError):
        DocumentCreateRequest(name="r.md", role="rule", apply_mode="conditional")  # type: ignore[arg-type]


def test_patch_request_rejects_conditional():
    with pytest.raises(ValidationError):
        DocumentPatchRequest(apply_mode="conditional")  # type: ignore[arg-type]


def test_rule_consult_name_strips_md():
    assert rule_consult_name("合规附录.md") == "合规附录"
    assert rule_consult_name("合规附录") == "合规附录"


def test_rule_source_implements_consultable():
    src = RuleConsultSource()
    assert isinstance(src, Consultable)


def test_consult_schema_orchestration():
    schema = _rule_tool().schema
    assert schema.name == "consult"
    assert schema.category is ToolCategory.ORCHESTRATION


async def test_consult_rule_hit_soft_miss():
    src = RuleConsultSource()

    async def _fake_fetch(user_id: str, name: str) -> str | None:
        return "须遵守：演练暗号为 ALPHA" if name == "演练暗号" else None

    async def _fake_list(user_id: str) -> list[ConsultDirectoryEntry]:
        return [ConsultDirectoryEntry(name="演练暗号", summary="暗号")]

    src.fetch_by_name = _fake_fetch  # type: ignore[method-assign]
    src.list_directory = _fake_list  # type: ignore[method-assign]
    tool = ConsultTool(source=MergedConsultSource(rule=src))

    hit = await tool.execute({"name": "演练暗号"}, _ctx())
    assert hit.success and "ALPHA" in hit.output
    assert hit.display["origin"] == "user"
    # 细 kind 只进日志；display 只带两桶 origin。
    assert "kind" not in hit.display

    miss = await tool.execute({"name": "不存在"}, _ctx())
    assert miss.success and miss.error is None
    assert "没有名为" in miss.output
    assert "演练暗号" in miss.output
    assert miss.display is None or "origin" not in miss.display


def test_compose_ceo_rule_entries_with_consult():
    rules = [OnDemandUserRule(name="合规附录", summary="长条文")]
    base = assemble_system_prompt()
    out = compose_ceo_chat_prompt(
        base,
        skill_registry=build_system_skill_registry(),
        ceo_tool_names={"consult", "delegate"},
        on_demand_rules=rules,
    )
    assert "<按需目录>" in out
    assert "合规附录" in out


def test_compose_worker_rule_entries():
    rules = [OnDemandUserRule(name="合规附录", summary="长条文")]
    out = compose_worker_base_prompt(assemble_system_prompt(), on_demand_rules=rules)
    assert "<按需目录>" in out
    assert "合规附录：长条文" in out


def test_render_on_demand_with_rule_entry():
    out = render_on_demand_directory(
        [ConsultDirectoryEntry(name="合规附录", summary="长条文")]
    )
    assert "合规附录：长条文" in out
