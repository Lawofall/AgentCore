"""工具 schema 体积棘轮——CEO / worker 工具面只许瘦、不许悄悄回潮。

## 为什么有这道棘轮

工具 schema 是**每一轮**都重发的输入：低频工具改按需后开场表已瘦一截，但常驻面
（delegate / git / ask_user …）仍坐在 prefix 前段——一改就是全量 miss。它的膨胀方式
几乎总是同一种：同一条约束在工具描述、参数描述、兄弟工具里各抄一份，或者字段删了、负面
清单还留着。每份副本单看都「只多几十字」，没人拦就月月长。

所以这里按**字符数**（不是 token 估算——``approx_tokens`` 的 chars/token 常量会随口径调整）
钉住已经去过重的那几个面：``measure_openai_tool_chars`` 量的就是真正发给模型的那份 JSON。

## 红了怎么办

- **超了上限**：先问是不是又抄了一份别处已有的话。同一条约束只留一处：
  取值语义留在参数描述，跨工具路由 / 审批策略留在工具描述，HOW 留在 skill / consult。
  确实是**新增**的有效语义 → 把这里的数字调上去，并在 PR 里说清多出来的是什么。
- **远低于上限**（比如又砍了一批）：把数字调下来，棘轮才继续咬合。

数字 = 当次实测值向上取整到十位；只许降不许升。
"""

from __future__ import annotations

import json

from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.runtime.events import EventSink
from agentcore.runtime.resolve.ceo_surface import measure_openai_tool_chars
from agentcore.tools.builtin.ask_user.tool import AskUserTool
from agentcore.tools.builtin.browser import (
    _MUTATION_VERIFY_TAIL,
    BROWSER_TOOL_CLASSES,
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
from agentcore.tools.builtin.folders import (
    CreateFolderTool,
    ListFoldersTool,
    ResolveFolderTool,
)
from agentcore.tools.builtin.git_ops.policy import GIT_TOOL_PARAMETERS
from agentcore.tools.builtin.git_ops.tool import GitTool
from agentcore.tools.builtin.host import HostTool
from agentcore.tools.builtin.replan import _REPLAN_DESCRIPTION, _REPLAN_PARAMETERS
from agentcore.tools.builtin.terminal import TerminalTool
from agentcore.tools.protocol import ToolSchema

# 桌面 CEO 回合会同时挂上的那一份（ask_user 取桌面态——它比 web 态更胖）。
# host / terminal / browser 按需进表后仍每轮重发，钉住短触发 + HOW→consult。
# 2026-08-28 debate 形态：删 schema 复述（用/不用、过闸禁令、挂号、启服手册、action 表）。
# debate 实测 1640 作短触发锚；ask_user 桌面 2588→2590、web 1892→1900。
# 2026-08-28 grant_attach_folder：本机传统附加可写根（新路由脊柱，ask_user 桌面 2655）。
_CAPS: dict[str, int] = {
    "browser": 1590,
    "git": 2530,
    "host": 2800,
    "terminal": 1360,
    "delegate": 2850,
    "debate": 1640,
    "ask_user": 2660,
    "list_folders": 240,
    "resolve_folder": 370,
    "create_folder": 510,
}
_TOTAL_CAP = sum(_CAPS.values())

# 非桌面（web）态 ask_user：桌面独有的 action / well_known 等选项不装配。
_ASK_USER_WEB_CAP = 1900

# Worker-only：escalate / handoff / 写盘三件套曾把身份段或 consult HOW 再抄一遍到按钮上。
_WORKER_CAPS: dict[str, int] = {
    "escalate": 1690,
    "handoff": 1660,
    "file_write": 610,
    "file_append": 510,
    "str_replace": 840,
}


def _delegate_schema() -> ToolSchema:
    """DelegateTool.schema 的等价体（真工具要一整套协作依赖才构得出来）。"""
    return ToolSchema(
        name="delegate",
        description=DELEGATE_DESCRIPTION,
        parameters=DELEGATE_PARAMETERS,
        category=ToolCategory.ORCHESTRATION,
        approval=ToolApproval.NEVER,
    )


def _debate_schema() -> ToolSchema:
    """DebateTool.schema 的等价体（真工具要 LLM / sink / registry 才构得出来）。"""
    return ToolSchema(
        name="debate",
        description=DEBATE_DESCRIPTION,
        parameters=DEBATE_PARAMETERS,
        category=ToolCategory.ORCHESTRATION,
        approval=ToolApproval.NEVER,
    )


def _ask_user_schema(*, desktop: bool) -> ToolSchema:
    return AskUserTool(
        sink=EventSink(),
        conversation_id="c1",
        timeout_seconds=30.0,
        advertise_bind_local_folder=desktop,
    ).schema


def _measured() -> dict[str, int]:
    sizes = {
        cls().schema.name: measure_openai_tool_chars(cls().schema)
        for cls in BROWSER_TOOL_CLASSES
    }
    sizes["git"] = measure_openai_tool_chars(GitTool().schema)
    sizes["host"] = measure_openai_tool_chars(HostTool().schema)
    sizes["terminal"] = measure_openai_tool_chars(TerminalTool().schema)
    sizes["delegate"] = measure_openai_tool_chars(_delegate_schema())
    sizes["debate"] = measure_openai_tool_chars(_debate_schema())
    sizes["ask_user"] = measure_openai_tool_chars(_ask_user_schema(desktop=True))
    sizes["list_folders"] = measure_openai_tool_chars(ListFoldersTool().schema)
    sizes["resolve_folder"] = measure_openai_tool_chars(ResolveFolderTool().schema)
    sizes["create_folder"] = measure_openai_tool_chars(CreateFolderTool().schema)
    return sizes


def _measured_worker() -> dict[str, int]:
    from agentcore.tools.builtin.escalate import EscalateTool
    from agentcore.tools.builtin.file_ops.mutate import (
        FileAppendTool,
        FileWriteTool,
        StrReplaceTool,
    )
    from agentcore.tools.builtin.handoff import HandoffTool

    return {
        "escalate": measure_openai_tool_chars(EscalateTool().schema),
        "handoff": measure_openai_tool_chars(HandoffTool().schema),
        "file_write": measure_openai_tool_chars(FileWriteTool().schema),
        "file_append": measure_openai_tool_chars(FileAppendTool().schema),
        "str_replace": measure_openai_tool_chars(StrReplaceTool().schema),
    }


def test_per_tool_schema_chars_within_cap():
    sizes = _measured()
    assert set(sizes) == set(_CAPS), f"棘轮覆盖面漂了：{sorted(set(sizes) ^ set(_CAPS))}"
    over = {
        name: (chars, _CAPS[name]) for name, chars in sizes.items() if chars > _CAPS[name]
    }
    assert not over, f"工具 schema 变胖（实测, 上限）：{over}"


def test_total_ceo_tool_schema_chars_within_cap():
    total = sum(_measured().values())
    assert total <= _TOTAL_CAP, f"这批工具合计 {total} 字符 > 上限 {_TOTAL_CAP}"


def test_ask_user_web_surface_stays_lighter_than_desktop():
    web = measure_openai_tool_chars(_ask_user_schema(desktop=False))
    assert web <= _ASK_USER_WEB_CAP, f"web 态 ask_user 变胖：{web}"
    assert web < _CAPS["ask_user"]


def test_deleted_delegate_fields_have_no_negative_list():
    """已删字段不留负面清单：字段不在 schema 里，就别再花 100+ 字符说「勿再填」。"""
    props = DELEGATE_PARAMETERS["properties"]
    retired = (
        "completion_criteria",
        "requires_files",
        "must_contain",
        "min_length",
        "objective",
        "playbook_none_reason",
        "finalize",
        "playbook_id",
        "parallelism",
        "seed_notes",
        "force_continue",
        "coordination",
        "checkpoint_after",
        "bind_after_deps",
        "complexity_hint",
        "result_handling",
        "require_upstream",
    )
    blob = DELEGATE_DESCRIPTION + json.dumps(DELEGATE_PARAMETERS, ensure_ascii=False)
    for field in retired:
        assert field not in props, f"{field} 不该回到 delegate 顶层参数"
        assert field not in blob, f"{field} 已删，schema 不必再提它"
    task_props = DELEGATE_PARAMETERS["properties"]["tasks"]["items"]["properties"]
    assert "checkpoint_after" not in task_props
    assert "bind_after_deps" not in task_props
    assert "result_handling" not in task_props
    assert "require_upstream" not in task_props
    # C1：顶层 coordinate 已下架。子串检查会误伤 retired 的 coordination，故单独钉 JSON 键。
    assert "coordinate" not in props
    assert '"coordinate"' not in json.dumps(DELEGATE_PARAMETERS, ensure_ascii=False)
    replan_blob = _REPLAN_DESCRIPTION + json.dumps(_REPLAN_PARAMETERS, ensure_ascii=False)
    for field in ("coordination", "checkpoint_after", "bind_after_deps"):
        assert field not in replan_blob, f"{field} 已删，replan schema 不必再提它"


def test_shared_mutation_tail_does_not_repeat_per_tool_receipts():
    """四个 mutation 工具共用的尾巴不再逐个点名 typed / clicked——各自那行自己说。"""
    assert "typed.matched" not in _MUTATION_VERIFY_TAIL
    assert "clicked.was_disabled" not in _MUTATION_VERIFY_TAIL
    action_desc = BrowserTool().schema.parameters["properties"]["action"]["description"]
    assert "typed.matched" in action_desc
    assert "clicked.was_disabled" in action_desc


def test_git_policy_matrix_lives_only_in_tool_description():
    """审批 / 无仓 / CEO 写入策略只写一遍：subcommand 参数不复述。"""
    sub_desc = GIT_TOOL_PARAMETERS["properties"]["subcommand"]["description"]
    assert "须审批" not in sub_desc
    assert "delegate" not in sub_desc
    tool_desc = GitTool().schema.description
    assert "须审批" in tool_desc
    assert "delegate" in tool_desc
    assert "no_repo" in tool_desc


def test_terminal_description_routes_without_restating_subcommands():
    """工具描述只做路由；四个子命令各干什么留在 subcommand 参数。"""
    from agentcore.tools.builtin.terminal import TERMINAL_TOOL_PARAMETERS

    desc = TerminalTool().schema.description
    assert "read：" not in desc
    assert "list：" not in desc
    sub_desc = TERMINAL_TOOL_PARAMETERS["properties"]["subcommand"]["description"]
    for sub in ("start", "read", "stop", "list"):
        assert f"{sub}：" in sub_desc


def test_on_demand_faces_point_how_to_consult():
    """host / terminal / browser 工具描述短触发，手册走 consult。"""
    assert "HOW→consult(host)" in HostTool().schema.description
    assert "HOW→consult(terminal)" in TerminalTool().schema.description
    assert "HOW→consult(browser)" in BrowserTool().schema.description
    assert "HOW→consult(debate_and_review)" in DEBATE_DESCRIPTION
    assert "HOW→consult(team_orchestration_advanced)" in DELEGATE_DESCRIPTION
    host_action = HostTool().schema.parameters["properties"]["action"]["description"]
    assert "Get-WinEvent" in host_action
    assert "password_blocked" not in BrowserTool().schema.description
    assert (
        "password_blocked"
        in BrowserTool().schema.parameters["properties"]["text"]["description"]
    )
    assert "uvicorn --reload" not in TerminalTool().schema.description
    from agentcore.tools.builtin.terminal import TERMINAL_TOOL_PARAMETERS

    assert "uvicorn --reload" in TERMINAL_TOOL_PARAMETERS["properties"]["wait_for"]["description"]
    # description 不复述 action 表（取值语义留在 action 参数）。
    assert "navigate/click/type" not in BrowserTool().schema.description
    assert "status/os_log/shell：CEO" not in HostTool().schema.description
    assert "CEO 可只启服" not in TerminalTool().schema.description


def test_worker_tool_schema_chars_within_cap():
    sizes = _measured_worker()
    assert set(sizes) == set(_WORKER_CAPS), (
        f"worker 棘轮覆盖面漂了：{sorted(set(sizes) ^ set(_WORKER_CAPS))}"
    )
    over = {
        name: (chars, _WORKER_CAPS[name])
        for name, chars in sizes.items()
        if chars > _WORKER_CAPS[name]
    }
    assert not over, f"worker 工具 schema 变胖（实测, 上限）：{over}"
