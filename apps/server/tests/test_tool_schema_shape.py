"""工具 schema 的形状：同形、键集、一层、扁平。"""

from __future__ import annotations

from agentcore.runtime.events import EventSink
from agentcore.tools.builtin.ask_user.tool import AskUserTool
from agentcore.tools.builtin.browser import (
    _MUTATION_VERIFY_TAIL,
    BrowserTool,
)
from agentcore.tools.builtin.debate.schema import (
    DEBATE_DESCRIPTION,
    DEBATE_PARAMETERS,
)
from agentcore.tools.builtin.delegate.schema import (
    DELEGATE_DESCRIPTION,
    DELEGATE_PARAMETERS,
)
from agentcore.tools.builtin.host import HostTool
from agentcore.tools.builtin.replan import _REPLAN_PARAMETERS
from agentcore.tools.builtin.run import RunTool
from agentcore.tools.protocol import ToolSchema, tool_schema_to_openai_format


def _ask_user_schema(*, desktop: bool) -> ToolSchema:
    return AskUserTool(
        sink=EventSink(),
        conversation_id="c1",
        timeout_seconds=30.0,
        advertise_bind_local_folder=desktop,
    ).schema


def test_ask_user_web_surface_matches_desktop():
    """开夹不进按钮后 web / 桌面同形。"""
    web = tool_schema_to_openai_format(_ask_user_schema(desktop=False))
    desktop = tool_schema_to_openai_format(_ask_user_schema(desktop=True))
    assert web == desktop


def test_delegate_top_level_parameter_keys():
    """delegate 顶层参数钉现行集合（键从 schema.py 读出，不预埋废字段）。"""
    assert set(DELEGATE_PARAMETERS["properties"]) == {
        "tasks",
        "team_brief",
    }


def test_shared_mutation_tail_does_not_repeat_per_tool_receipts():
    """共用尾巴不点名 typed / clicked；回执字段在结果里，不进 action 参数。"""
    assert "typed.matched" not in _MUTATION_VERIFY_TAIL
    assert "clicked.was_disabled" not in _MUTATION_VERIFY_TAIL
    action_desc = BrowserTool().schema.parameters["properties"]["action"]["description"]
    assert "typed.matched" not in action_desc
    assert "clicked.was_disabled" not in action_desc


_GO_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {"oneOf", "anyOf", "allOf", "$ref", "$defs", "if", "then", "else"}
)


def _schema_keys(node: object) -> list[str]:
    keys: list[str] = []
    if isinstance(node, dict):
        keys.extend(node.keys())
        for value in node.values():
            keys.extend(_schema_keys(value))
    elif isinstance(node, list):
        for value in node:
            keys.extend(_schema_keys(value))
    return keys


def test_swiss_army_schemas_stay_flat_for_go_gateway():
    """host / browser 保持单名 + 扁平 object；禁止 oneOf 过 Go 网关。"""
    for schema in (HostTool().schema, BrowserTool().schema):
        params = schema.parameters
        assert params.get("type") == "object"
        assert "properties" in params
        hits = _GO_UNSUPPORTED_SCHEMA_KEYS.intersection(_schema_keys(params))
        assert not hits, f"{schema.name} 含 Go 拒收关键字 {sorted(hits)}"


def test_run_description_is_one_command_face():
    desc = RunTool().schema.description
    assert "command" in desc.lower() or "命令" in desc
    assert "subcommand" not in desc
    assert "HOW→consult(run)" not in desc


def test_on_demand_faces_point_how_to_consult():
    """同名手册不在按钮复指；debate 入口合同写在 description。"""
    assert "HOW→consult(host)" not in HostTool().schema.description
    assert "HOW→consult(run)" not in RunTool().schema.description
    assert "HOW→consult(browser)" not in BrowserTool().schema.description
    assert DEBATE_DESCRIPTION == "不主动启动仅推荐：结构化正反辩论。"
    assert "HOW→consult" not in DEBATE_DESCRIPTION
    assert "决策简报" not in DEBATE_DESCRIPTION
    assert "非终结" not in DEBATE_DESCRIPTION
    assert "HOW→consult" not in DELEGATE_DESCRIPTION
    from agentcore.tools.builtin.delegate.schema import (
        DELEGATE_STAFF_HOW,
        DELEGATE_WHEN,
        NESTED_DELEGATE_DESCRIPTION,
        NESTED_STAFF_HOW,
    )

    assert DELEGATE_STAFF_HOW in DELEGATE_DESCRIPTION
    assert DELEGATE_STAFF_HOW in NESTED_DELEGATE_DESCRIPTION
    assert NESTED_STAFF_HOW in NESTED_DELEGATE_DESCRIPTION
    assert NESTED_STAFF_HOW not in DELEGATE_DESCRIPTION
    assert DELEGATE_WHEN in DELEGATE_DESCRIPTION
    assert DELEGATE_WHEN in NESTED_DELEGATE_DESCRIPTION
    assert "成篇落盘" not in NESTED_DELEGATE_DESCRIPTION
    host_cmd_desc = HostTool().schema.parameters["properties"]["command"]["description"]
    assert "Get-WinEvent" not in host_cmd_desc
    from agentcore.runtime.resolve.prompt import capability_how_suffix

    assert capability_how_suffix({"host"}) == ""
    assert capability_how_suffix({"browser"}) == ""
    host_cmd = HostTool().schema.parameters["properties"]["command"]["description"]
    assert "PowerShell" not in host_cmd
    assert set(HostTool().schema.parameters["properties"]) == {"command"}
    text_desc = BrowserTool().schema.parameters["properties"]["text"]["description"]
    assert "密码" in text_desc
    sid_desc = BrowserTool().schema.parameters["properties"]["session_id"]["description"]
    assert "缺省解析" not in sid_desc
    assert not sid_desc.startswith("可选")
    assert set(BrowserTool().schema.parameters["properties"]) == {
        "action",
        "url",
        "ref",
        "text",
        "snapshot_version",
        "session_id",
    }
    assert set(_ask_user_schema(desktop=True).parameters["properties"]) == {
        "questions",
    }
    assert set(RunTool().schema.parameters["properties"]) == {
        "command",
        "cwd",
        "background",
        "wait_for",
        "action",
        "process_id",
    }
    assert set(DEBATE_PARAMETERS["properties"]) == {
        "motion",
        "sides",
        "cross_model",
        "background",
        "moderator_model",
    }
    assert set(DEBATE_PARAMETERS["properties"]["sides"]["items"]["properties"]) == {
        "key",
        "name",
        "stance",
        "model",
    }
    wait_desc = RunTool().schema.parameters["properties"]["wait_for"]["description"]
    assert "省略" in wait_desc
    assert "默认就绪" not in wait_desc
    assert not wait_desc.startswith("可选")
    cwd_desc = RunTool().schema.parameters["properties"]["cwd"]["description"]
    assert not cwd_desc.endswith("可选。")
    assert not cwd_desc.startswith("可选")
    task_props = DELEGATE_PARAMETERS["properties"]["tasks"]["items"]["properties"]
    assert not task_props["id"]["description"].startswith("可选")
    assert not task_props["model"]["description"].startswith("（可选）")
    add_props = _REPLAN_PARAMETERS["properties"]["add"]
    assert not add_props["description"].startswith("可选")
    assert "必填" not in add_props["description"]
    assert not add_props["items"]["properties"]["id"]["description"].startswith("可选")
    assert not add_props["items"]["properties"]["depends_on"]["description"].startswith(
        "可选"
    )
    assert not _REPLAN_PARAMETERS["properties"]["tell"]["description"].startswith(
        "可选"
    )
    assert not _REPLAN_PARAMETERS["properties"]["stop"]["description"].startswith("可选")
    key_desc = DEBATE_PARAMETERS["properties"]["sides"]["items"]["properties"]["key"][
        "description"
    ]
    assert not key_desc.startswith("可选")
    assert not DEBATE_PARAMETERS["properties"]["background"]["description"].startswith(
        "可选"
    )
    from agentcore.tools.builtin.grep import GrepTool

    glob_desc = GrepTool().schema.parameters["properties"]["glob"]["description"]
    assert not glob_desc.startswith("可选")
    # description 不复述 action 表（取值语义留在 action 参数；enum 不再抄进 description）。
    assert "navigate/click/type" not in BrowserTool().schema.description
    for schema in (BrowserTool().schema,):
        action = schema.parameters["properties"]["action"]
        desc = action["description"]
        names = sorted(action["enum"])
        assert " / ".join(names) not in desc
        assert "|".join(names) not in desc
