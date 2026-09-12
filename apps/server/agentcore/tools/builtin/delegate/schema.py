"""Delegate tool schema and constants.

Schema layer (工具面瘦身): short trigger + 拆任务合同 + playbook/tasks 互斥.
何时用写在本 description；根 CEO 编制 HOW →
``consult(staffing)``；嵌套 lead → ``consult(lead_subteam)``.
task 参数只留自包含对比边界；填约束/路径/凭据 HOW 在上述 consult。
"""

from __future__ import annotations

from agentcore.runtime.delegate.playbook_declaration import HANDWRITTEN_TASKS_SKELETON
from agentcore.runtime.delegate.task_models import TASK_MODEL_SCHEMA_PROPS
from agentcore.runtime.runs.constants import MAX_DELEGATION_TASKS, MAX_GAP_FILL_ADDS
from agentcore.runtime.runs.playbooks import PLAYBOOKS, playbook_args_schema_description

# Shared task-level deliverable shape (delegate tasks + replan binds/add).
# CEO / replan fill-in: optional artifact paths only. Write-vs-chat is task
# acceptance + the model; the engine only recognizes pinned paths.
# Playbook-internal knobs still parse in builder; they are not on this schema.
TASK_DELIVERABLE_SCHEMA: dict[str, object] = {
    "type": "object",
    "description": "可选。用户点名或流水线写死才填 artifacts；省略=不催写盘。",
    "properties": {
        "artifacts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "路径列表。",
        },
    },
}

# Trigger + when-to-use polarity. XOR → playbook 参数一句；探路/编制 HOW → consult.
DELEGATE_DESCRIPTION = (
    f"拆任务给临时团队（默认手写顶层 tasks：role+task，≤{MAX_DELEGATION_TASKS}；非终结）。"
    "默认用本工具（成篇落盘、可运行应用、成规模查证、要并行、实质讨论尤然）；"
    "闲聊、窗口里已有证据的一问一答、一眼写完的短文或小落盘、纯启服不必派。"
    "有写权 ≠ 自己做完。不知读哪 ≠ 自己连搜。"
    "HOW→consult(staffing)。"
)

# Nested captain: blocking wait, not coordination. HOW is a different consult.
NESTED_DELEGATE_DESCRIPTION = (
    f"把当前任务拆给由你指挥的子团队（手写 tasks：role+task，≤{MAX_DELEGATION_TASKS}；"
    "调用后等到子队收工）。"
    "成果级目标·约束·验收、尚未钉成单切片时优先用本工具再整合；"
    "单文件 / 已钉薄壳 / 强耦合同 run 切片 / 小修·机械单步自己干。"
    "有写权 ≠ 自己做完。"
    "HOW→consult(lead_subteam)。"
)

DELEGATE_PARAMETERS = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "description": (
                f"默认主路（≤{MAX_DELEGATION_TASKS}）。"
                f"顶层非空数组可抄：{HANDWRITTEN_TASKS_SKELETON}（deliverable 可选）。"
            ),
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                    "task": {
                        "type": "string",
                        "description": (
                            "自包含=目标+边界+验收（worker 看不到完整历史）"
                            "≠逐步改法、章节骨架。"
                        ),
                    },
                    "deliverable": TASK_DELIVERABLE_SCHEMA,
                    "id": {
                        "type": "string",
                        "description": "可选节点 id。depends_on 可引用此字面值。",
                    },
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "生产者→消费者：空=同波并行。"
                            "排队只认本字段（本批 id / 角色名）≠ task 里写先后。"
                            "跨回合是新开一队。"
                        ),
                    },
                    "replaces_run_id": {
                        "type": "string",
                        "description": (
                            "补缺口：接手某个失败/跳过的 run（填其 run_id）。"
                            f"单次≤{MAX_GAP_FILL_ADDS}。"
                        ),
                    },
                    "continue_from_run_id": {
                        "type": "string",
                        # 「动同一支团队 / 不限条数 / 勿冷派整团」HOW → staffing。
                        # 这里只留本字段自己的填法。
                        "description": (
                            "同人续派（调查后确认修 / 改稿 / 收口后接着干）；填已完成 run_id。"
                        ),
                    },
                    "target_folder_id": {
                        "type": "string",
                        "description": (
                            "已解析文件夹 id（该队员坐哪张桌）。"
                            "跨已登记文件夹（只读摸底与改盘通吃）须点名；"
                            "云端草稿 ≠ 读不到已有文件夹。接到工作区 / 挂载 ≠ 换桌。"
                        ),
                    },
                    **TASK_MODEL_SCHEMA_PROPS,
                },
                "required": ["role", "task"],
            },
        },
        "append_to_execution_id": {
            "type": "string",
            "description": (
                '跨回合接续上一张图：只填 "latest"（引擎解析）；'
                "同回合再调一般不必传。"
            ),
        },
        "playbook": {
            "type": "string",
            "enum": sorted(PLAYBOOKS),
            "description": "固化流水线名（非默认快捷进阶）；与 tasks 二选一。",
        },
        "playbook_args": {
            "type": "object",
            "description": playbook_args_schema_description(),
        },
        "team_brief": {
            "type": "string",
            "description": (
                "有共享口径才写（一行一条）；各 worker 开局可见。省略即可。"
            ),
        },
    },
}
