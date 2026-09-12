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

from agentcore.core.types import ToolApproval, ToolFace
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
from agentcore.tools.builtin.run import RunTool
from agentcore.tools.protocol import ToolSchema

# 桌面 CEO 回合会同时挂上的那一份（ask_user 取桌面态——它比 web 态更胖）。
# host / terminal / browser 按需进表后仍每轮重发，钉住短触发 + HOW→consult。
# 2026-08-28 debate 形态：删 schema 复述（用/不用、过闸禁令、挂号、启服手册、action 表）。
# debate 实测 1640 作短触发锚；ask_user 桌面 2588→2590、web 1892→1900。
# 2026-08-28 grant_attach_folder：本机传统附加可写根（新路由脊柱，ask_user 桌面 2655）。
# 2026-08-29 C：何时用 ask_user 从核下沉 description（挡路才问）。桌面 2668、web 1905。
# 2026-08-29 delegate 删事故反例（引擎已拒 playbook+tasks 同传）。实测 2779。
# 2026-08-29 工作流：description 加「探路够了再派」（停手 when-to-use）。实测 2798。
# 2026-08-29 装配侧：host action 去 OS 命令名/政策复述（Get-WinEvent 归 consult）；
# git 硬拒收成「禁项见失败回执」；delegate task 下沉凭据填 env（实测仍 2600，不抬）。
# host 2800→2630（实测 2622）；git 2530→2430（实测 2425）。
# 同日主审查：补回 pull/push 合同句（ff-only / 恒确认），非新语义抬顶。git 2430→2490。
# 2026-08-30 delegate：brief 改为有共享口径才写；task 不再把开局口径赶进 brief。实测 2547。
# 2026-08-30 debate：产品入口只认正反，schema 不再广告三种形态。实测 1584。
# 2026-09-01 debate：form.enum 广告子集只留 debate；description 不再抄入口分流。实测 1380。
# 2026-08-30 delegate.task 收未装配 ≠ 切口（从核搬家）。当次实测 2457。cap 降到 2460。
# 2026-08-30 派前可见打算从 CEO 核搬进 description。当次实测 2469。cap 2470。
# 2026-08-30 档 1：description 补成篇/可运行应用 + 有写权≠超规模自己做完。
# 当次实测 2510。cap 2510（抬顶=when-to-use 补漏，非回潮）。
# 2026-08-31 ask_user：云桌不再广告 attach_rw，本机不广告 open/bind；实测 2425。
# 2026-08-31 delegate：when-to-use 改默认用、探路停手写进 description。
# 当次实测 2537。cap 2540（抬顶=极性与停手，非回潮）。
# 2026-09-01 schema 同层去重（手册出按钮）：browser 1329、host 2567、run 1004、
# delegate 2409、ask_user 桌面 2235 / web 1686；git 政策表仍只在 description（2430）。
# 2026-09-01 已确认约束填法收进 task 参数、deliverable 不再复述。实测 delegate 2380。
# 2026-09-08 删 deliverable.form 三档：schema 只留 artifacts。实测 delegate 2153。cap 2340→2160。
# 2026-09-02 run：when-to-use 补进 description（验证直接跑 / dev 后台 / action 管已有进程）。
# 省略 wait_for 则起来就返回（不再注入默认就绪信号）。cap 1030。
# 2026-09-06 ask_user：撤 grant_* 模型面广告（区外改走 file_* 运行时授权）。桌面云 1565、web 1400。
# 2026-09-06 delegate.task：自包含 ≠ 逐步改法/改哪些文件/章节骨架（根可见面补对比边界）。
# 实测 2305。cap 2300→2310（抬顶=新语义，非回潮）。
# 2026-09-06 delegate.task：点名路径用工作区相对 POSIX（与工具 path 同形）。
# 实测 2335。cap 2310→2340（抬顶=新语义，非回潮）。
# 2026-09-08 ask_user HOW 同时指 ask_kickoff + ask_midtask（废名 asking_the_user 拆本）。
# 桌面实测 1582。cap 1570→1590。web 实测 1417。cap 1400→1420。
# 抬顶=HOW 指针多一本，不是把提问百科抄回按钮。
# 2026-09-08 撤 desks skill：换桌对照下沉 target_folder_id（实测 delegate 2337，仍 ≤2340）；
# list_folders / resolve_folder / create_folder 去掉 HOW→consult(desks) 指针。
# 实测 207 / 335 / 474。cap 240→210、370→340、510→480。
# 2026-09-10 delegate：读侧极性「不知读哪 ≠ 自己连搜」（与「有写权 ≠」对偶）。
# 实测 2168。cap 2160→2170（抬顶=when-to-use 补漏，非回潮）。
# 2026-09-10 delegate.task：已确认约束不装改法/现状；点名入口或成品路径
# （收掉「≠改哪些文件」，避免和点名打架）。换字不抬顶。
# 2026-09-10 delegate.depends_on：空=同波并行 + 本字段 ≠ task 里写先后
# （对比边界，切开「把流水线写进 task」替身）。实测 2196。cap 2170→2200。
# 2026-09-10 波 2：参数描述只留取值。delegate.task / escalate / ask_user 卡片去
# 补集与判例；git 参数不再复述政策/闸。实测 delegate 2022、ask_user 桌面 1406 /
# web 1238、git 2410。cap 2200→2030、1590→1410、1420→1240、2430→2410。
# 2026-09-10 工具面对照行业：废名/补集/自指 HOW/审批轴/判例出按钮。
# 实测 browser 1274、git 2371、host 2435、str_replace 552。
# cap 1330→1280、2410→2380、2570→2440、610→560。
# 2026-09-10 文案五条：git 政策出按钮（回执纠偏）；delegate.task 只留自包含对比，
# 填法 HOW 回 staffing / lead_subteam。实测 git 2220、delegate 1938。
# cap 2380→2220、2030→1940。
# 2026-09-10 兼容续：Swiss-army 不去 oneOf（Go 网关拒 root oneOf）；host/browser
# 参数 HOW 进 consult。实测 browser 1077、host 2234。cap 1280→1080、2440→2240。
# 2026-09-10 双写尾巴：session_id / Audiosrv / git 政策 / run 例句 / 填卡 HOW
# 出按钮。实测 browser 1036、host 2209、git 2189、run 929、ask_user 1380。
# cap 1080→1040、2240→2210、2220→2190、1030→930、1410→1380。
_CAPS: dict[str, int] = {
    "browser": 1040,
    "git": 2190,
    "host": 2210,
    "run": 930,
    "delegate": 1940,
    "debate": 1380,
    "ask_user": 1380,
    "list_folders": 210,
    "resolve_folder": 340,
    "create_folder": 480,
}
_TOTAL_CAP = sum(_CAPS.values())

# 非桌面（web）态 ask_user：桌面独有的 action / well_known 等选项不装配。
# 2026-09-10 填卡 HOW 出按钮。实测 1212。cap 1240→1220。
_ASK_USER_WEB_CAP = 1220

# Worker-only：escalate / handoff / 写盘三件套曾把身份段或 consult HOW 再抄一遍到按钮上。
# 2026-08-29 escalate blocking：已拒凭据→false 短触发（身份段不进按钮）。当次实测 1698。cap 1690→1700。
# 2026-09-02 便条改收尾轮正文、参数表清空。实测 192。
# 2026-09-02 handoff WHEN 收成一句（有下游必须 / 无下游默认不交）。
# 2026-09-02 便条形状（结论 + 2–4 要点）从空 schema 字段 HOW 挪到 description。
# 实测 253。cap 200→260（抬顶=字段 HOW 无落点，不是别处再抄）。
# 2026-09-02 形状改为「现在什么已成立 / 便条 ≠ 文件说明」，去掉 2–4 条配额。实测 247。cap 260→250。
# 2026-09-01 写盘三件套 / escalate description 去重。实测 write 498 / append 413 /
# str_replace 632 / escalate 1508。
# 2026-09-10 波 2：escalate 卡片去补集；实测 1387。cap 1510→1390。
# 2026-09-08 撤 long_form_landing：写工具 description 去掉 HOW→consult。实测 write 334 /
# str_replace 601。cap write 500→340、str_replace 640→610。
# 2026-09-01 常驻文件面：回收站/扁平化手册出按钮，恢复路径留回执。实测
# delete 353 / read 766 / grep 938 / move 328 / copy 375 / glob 684 /
# list 404 / mkdir 223。
# 2026-09-01 mkdir：when-to-use 从 CEO-only skill 下沉到工具 description
#（结构目录 vs 套应用名当工程根）。实测 321。cap 230→330（抬顶=漏层补 when-to-use）。
# 2026-09-01 code_search / code_diagnostics：索引与 unavailable 手册出按钮。
# 实测 search 626 / diagnostics 416。
# 2026-09-01 协调套件：解析失败候选 / 空 wait 审批手册出按钮。实测
# wait 271 / update_synthesis 286 / cancel_worker 337 / resolve_escalation 480 /
# queue_user_message 339。
# 2026-09-02 wait：开口闭集补插话，非回潮。实测 305。cap 280→310。
_COORD_CAPS: dict[str, int] = {
    "wait": 310,
    "update_synthesis": 290,
    "cancel_worker": 340,
    "resolve_escalation": 480,
    "queue_user_message": 340,
}
_WORKER_CAPS: dict[str, int] = {
    "escalate": 1390,
    "handoff": 250,
    "file_write": 340,
    "str_replace": 560,
}
# 2026-09-06 区外路径改走 file_* 本机路径（运行时挂载）：when-to-use 进 description。
# 2026-09-06 已挂 external/ 写升档：file_copy dest 补已挂路径。实测 file_copy 431。
# 实测 file_read 854 / grep 980 / glob 729 / file_list 472。
# 2026-09-10 开场去重：挂载 HOW 只留 consult(local_desk)；path 补集/判例出按钮。
# 实测 file_read 727 / grep 894 / glob 597 / file_list 292 / code_search 550。
_FILE_CAPS: dict[str, int] = {
    "file_delete": 360,
    "file_read": 730,
    "grep": 900,
    "file_move": 330,
    "file_copy": 440,
    "glob": 600,
    "file_list": 300,
    "mkdir": 330,
    "code_search": 550,
    "code_diagnostics": 420,
}
# 2026-09-09 query 定位（唯一命中打开 / 多场列出）。
# 实测 search_conversations 857 / read_conversation 825。
# 2026-09-10 读对话：输出宪法出按钮（基座已有）。实测 read 801。cap 830→810。
# 2026-09-10 分页协议出按钮（truncated / next_cursor 只在回执与 cursor 参数）。
# 实测 read 730。cap 810→730。
_LOG_CAPS: dict[str, int] = {
    "search_conversations": 860,
    "read_conversation": 730,
}


def _delegate_schema() -> ToolSchema:
    """DelegateTool.schema 的等价体（真工具要一整套协作依赖才构得出来）。"""
    return ToolSchema(
        name="delegate",
        description=DELEGATE_DESCRIPTION,
        parameters=DELEGATE_PARAMETERS,
        face=ToolFace.ORCHESTRATION,
        approval=ToolApproval.NEVER,
    )


def _debate_schema() -> ToolSchema:
    """DebateTool.schema 的等价体（真工具要 LLM / sink / registry 才构得出来）。"""
    return ToolSchema(
        name="debate",
        description=DEBATE_DESCRIPTION,
        parameters=DEBATE_PARAMETERS,
        face=ToolFace.ORCHESTRATION,
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
    sizes["run"] = measure_openai_tool_chars(RunTool().schema)
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
        FileWriteTool,
        StrReplaceTool,
    )
    from agentcore.tools.builtin.handoff import HandoffTool

    return {
        "escalate": measure_openai_tool_chars(EscalateTool().schema),
        "handoff": measure_openai_tool_chars(HandoffTool().schema),
        "file_write": measure_openai_tool_chars(FileWriteTool().schema),
        "str_replace": measure_openai_tool_chars(StrReplaceTool().schema),
    }


def _measured_coord() -> dict[str, int]:
    from agentcore.runtime.coordination.tools import (
        CancelWorkerTool,
        QueueUserMessageTool,
        ResolveEscalationTool,
        UpdateSynthesisTool,
        WaitTool,
    )

    sink = EventSink()
    return {
        "wait": measure_openai_tool_chars(WaitTool().schema),
        "update_synthesis": measure_openai_tool_chars(
            UpdateSynthesisTool(sink=sink).schema
        ),
        "cancel_worker": measure_openai_tool_chars(CancelWorkerTool().schema),
        "resolve_escalation": measure_openai_tool_chars(ResolveEscalationTool().schema),
        "queue_user_message": measure_openai_tool_chars(
            QueueUserMessageTool(sink=sink).schema
        ),
    }


def _measured_file() -> dict[str, int]:
    from agentcore.tools.builtin.code_diagnostics import CodeDiagnosticsTool
    from agentcore.tools.builtin.code_search import CodeSearchTool
    from agentcore.tools.builtin.file_ops import (
        FileCopyTool,
        FileDeleteTool,
        FileListTool,
        FileMoveTool,
        FileReadTool,
        GlobTool,
        MkdirTool,
    )
    from agentcore.tools.builtin.grep import GrepTool

    return {
        "file_delete": measure_openai_tool_chars(FileDeleteTool().schema),
        "file_read": measure_openai_tool_chars(FileReadTool().schema),
        "grep": measure_openai_tool_chars(GrepTool().schema),
        "file_move": measure_openai_tool_chars(FileMoveTool().schema),
        "file_copy": measure_openai_tool_chars(FileCopyTool().schema),
        "glob": measure_openai_tool_chars(GlobTool().schema),
        "file_list": measure_openai_tool_chars(FileListTool().schema),
        "mkdir": measure_openai_tool_chars(MkdirTool().schema),
        "code_search": measure_openai_tool_chars(CodeSearchTool().schema),
        "code_diagnostics": measure_openai_tool_chars(CodeDiagnosticsTool().schema),
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
        "force",
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
    """共用尾巴不点名 typed / clicked；回执字段在 consult HOW，不进 action 参数。"""
    from agentcore.runtime.resolve.prompt.ceo_core import capability_how_suffix

    assert "typed.matched" not in _MUTATION_VERIFY_TAIL
    assert "clicked.was_disabled" not in _MUTATION_VERIFY_TAIL
    action_desc = BrowserTool().schema.parameters["properties"]["action"]["description"]
    assert "typed.matched" not in action_desc
    assert "clicked.was_disabled" not in action_desc
    how = capability_how_suffix({"browser"})
    assert "typed.matched" in how
    assert "clicked.was_disabled" in how


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
    """git / host / browser 保持单名 + 扁平 object；禁止 oneOf 过 Go 网关。"""
    for schema in (GitTool().schema, HostTool().schema, BrowserTool().schema):
        params = schema.parameters
        assert params.get("type") == "object"
        assert "properties" in params
        hits = _GO_UNSUPPORTED_SCHEMA_KEYS.intersection(_schema_keys(params))
        assert not hits, f"{schema.name} 含 Go 拒收关键字 {sorted(hits)}"


def test_git_policy_matrix_lives_in_receipts_not_schema():
    """审批 / 无仓 / ff-only 合同在代码与失败回执，不进按钮。"""
    sub_desc = GIT_TOOL_PARAMETERS["properties"]["subcommand"]["description"]
    assert "须审批" not in sub_desc
    assert "审批" not in sub_desc
    assert "无仓" not in sub_desc
    assert "delegate" not in sub_desc
    dest_desc = GIT_TOOL_PARAMETERS["properties"]["dest"]["description"]
    assert "非空拒绝" not in dest_desc
    tool_desc = GitTool().schema.description
    assert "须审批" not in tool_desc
    assert "no_repo" not in tool_desc
    assert "dirty_skip" not in tool_desc
    assert "ff-only" not in tool_desc
    assert "init_baseline" not in tool_desc
    assert "请确认" in tool_desc
    assert "回执" in tool_desc
    assert "delegate" not in tool_desc
    assert "CEO 拒写" not in tool_desc
    url_desc = GIT_TOOL_PARAMETERS["properties"]["url"]["description"]
    assert "GitHub" not in url_desc
    head_desc = GIT_TOOL_PARAMETERS["properties"]["head"]["description"]
    assert "已推" not in head_desc


def test_run_description_is_one_command_face():
    desc = RunTool().schema.description
    assert "command" in desc.lower() or "命令" in desc
    assert "subcommand" not in desc
    assert "HOW→consult(run)" in desc
    assert "CEO 只启停" not in desc
    assert "验收与短命令由队员" not in desc


def test_on_demand_faces_point_how_to_consult():
    """host / browser 工具描述短触发，手册走 consult。"""
    assert "HOW→consult(host)" in HostTool().schema.description
    assert "HOW→consult(run)" in RunTool().schema.description
    assert "HOW→consult(browser)" in BrowserTool().schema.description
    assert "HOW→consult(debate_and_review)" in DEBATE_DESCRIPTION
    assert "HOW→consult(staffing)" in DELEGATE_DESCRIPTION
    from agentcore.tools.builtin.delegate.schema import NESTED_DELEGATE_DESCRIPTION

    assert "HOW→consult(lead_subteam)" in NESTED_DELEGATE_DESCRIPTION
    assert "staffing" not in NESTED_DELEGATE_DESCRIPTION
    assert "team_orchestration_advanced" not in NESTED_DELEGATE_DESCRIPTION
    assert "lead_subteam" not in DELEGATE_DESCRIPTION
    assert "等到子队收工" in NESTED_DELEGATE_DESCRIPTION
    assert "browser_open" not in BrowserTool().schema.description
    host_action = HostTool().schema.parameters["properties"]["action"]["description"]
    assert "Get-WinEvent" not in host_action
    assert "仅 worker" not in host_action
    from agentcore.runtime.resolve.prompt import capability_how_suffix

    host_how = capability_how_suffix({"host"})
    assert "Get-WinEvent" in host_how
    assert "PowerShell" in host_how
    host_cmd = HostTool().schema.parameters["properties"]["command"]["description"]
    assert "PowerShell" not in host_cmd
    assert "%VAR%" not in host_cmd
    assert "password_blocked" not in BrowserTool().schema.description
    text_desc = BrowserTool().schema.parameters["properties"]["text"]["description"]
    assert "密码" in text_desc
    assert "password_blocked" not in text_desc
    assert "ask_user" not in text_desc
    assert "escalate" not in text_desc
    sid_desc = BrowserTool().schema.parameters["properties"]["session_id"]["description"]
    assert "缺省解析" not in sid_desc
    how_br = capability_how_suffix({"browser"})
    assert "session_id" in how_br and "本 run 已绑定" in how_br
    svc_desc = HostTool().schema.parameters["properties"]["service"]["description"]
    assert "Audiosrv" not in svc_desc
    assert "uvicorn --reload" not in RunTool().schema.description
    wait_desc = RunTool().schema.parameters["properties"]["wait_for"]["description"]
    assert "省略" in wait_desc
    assert "默认就绪" not in wait_desc
    # description 不复述 action 表（取值语义留在 action 参数）。
    assert "navigate/click/type" not in BrowserTool().schema.description
    assert "status/os_log/shell：CEO" not in HostTool().schema.description


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


def test_coordination_tool_schema_chars_within_cap():
    sizes = _measured_coord()
    assert set(sizes) == set(_COORD_CAPS), (
        f"协调棘轮覆盖面漂了：{sorted(set(sizes) ^ set(_COORD_CAPS))}"
    )
    over = {
        name: (chars, _COORD_CAPS[name])
        for name, chars in sizes.items()
        if chars > _COORD_CAPS[name]
    }
    assert not over, f"协调工具 schema 变胖（实测, 上限）：{over}"


def test_resident_file_tool_schema_chars_within_cap():
    sizes = _measured_file()
    assert set(sizes) == set(_FILE_CAPS), (
        f"常驻文件棘轮覆盖面漂了：{sorted(set(sizes) ^ set(_FILE_CAPS))}"
    )
    over = {
        name: (chars, _FILE_CAPS[name])
        for name, chars in sizes.items()
        if chars > _FILE_CAPS[name]
    }
    assert not over, f"常驻文件工具 schema 变胖（实测, 上限）：{over}"


def _measured_log() -> dict[str, int]:
    from agentcore.tools.builtin.read_conversation import ReadConversationTool
    from agentcore.tools.builtin.search_conversations import SearchConversationsTool

    return {
        "search_conversations": measure_openai_tool_chars(
            SearchConversationsTool().schema
        ),
        "read_conversation": measure_openai_tool_chars(ReadConversationTool().schema),
    }


def test_conversation_log_tool_schema_chars_within_cap():
    sizes = _measured_log()
    assert set(sizes) == set(_LOG_CAPS), (
        f"历史对话棘轮覆盖面漂了：{sorted(set(sizes) ^ set(_LOG_CAPS))}"
    )
    over = {
        name: (chars, _LOG_CAPS[name])
        for name, chars in sizes.items()
        if chars > _LOG_CAPS[name]
    }
    assert not over, f"历史对话工具 schema 变胖（实测, 上限）：{over}"
