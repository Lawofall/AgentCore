"""Delegate tool schema and constants.

Schema layer (工具面瘦身): short trigger + 拆任务合同 + playbook/tasks 互斥.
何时用写在本 description；根 CEO 编制 HOW →
``consult(team_orchestration_advanced)``；嵌套 lead → ``consult(lead_subteam)``.
"""

from __future__ import annotations

from agentcore.runtime.delegate.playbook_declaration import HANDWRITTEN_TASKS_SKELETON
from agentcore.runtime.delegate.task_models import TASK_MODEL_SCHEMA_PROPS
from agentcore.runtime.runs.constants import MAX_DELEGATION_TASKS, MAX_GAP_FILL_ADDS
from agentcore.runtime.runs.playbooks import PLAYBOOKS, playbook_args_schema_description

# Shared task-level deliverable shape (delegate tasks + replan binds/add).
# CEO / replan fill-in: three-tier form + optional artifact paths only.
# Playbook-internal knobs still parse in builder; they are not on this schema.
TASK_DELIVERABLE_SCHEMA: dict[str, object] = {
    "type": "object",
    "description": (
        "交付形态。省略或空对象=form=files。"
    ),
    "properties": {
        "form": {
            "type": "string",
            "enum": ["prose", "files", "workspace"],
            "description": (
                "【看】prose；【存文档】files（默认）；"
                "【改工程】workspace。漏填=files。裸文件名进工作稿。"
            ),
        },
        "artifacts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "用户点名或流水线写死才填；否则省略。",
        },
    },
}

# Trigger + when-to-use polarity. XOR → playbook 参数一句；探路/编制 HOW → consult.
DELEGATE_DESCRIPTION = (
    f"拆任务给临时团队（默认手写顶层 tasks：role+task，≤{MAX_DELEGATION_TASKS}；非终结）。"
    "默认用本工具（成篇落盘、可运行应用、成规模查证、要并行、实质讨论尤然）；"
    "闲聊、窗口里已有证据的一问一答、一眼写完的短文或小落盘、纯启服不必派。"
    "有写权 ≠ 自己做完。"
    "HOW→consult(team_orchestration_advanced)。"
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
                "摸底抄骨架 form=prose。"
            ),
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                    "task": {
                        "type": "string",
                        "description": (
                            "自包含=目标+边界+验收（worker 看不到完整历史）。"
                            "凭据写入 task 供队员填 env。"
                            "已拍板写同一行「已确认约束：①…；②…」；无则「（无）」；自拟默认标假设不进本行。"
                            "未装配能力 ≠ 写进 task。"
                        ),
                    },
                    "deliverable": TASK_DELIVERABLE_SCHEMA,
                    "id": {
                        "type": "string",
                        "description": "节点 id（可选）。铸 run_id={prefix}_{id}；depends_on 可引用此字面值。",
                    },
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "生产者→消费者（本批 id / 角色名；勿手抄 del_*）。"
                            "跨回合是新开一队，不是 depends_on 连旧图。"
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
                        # 「动同一支团队 / 不限条数 / 勿冷派整团」HOW → team_orchestration_advanced。
                        # 这里只留本字段自己的填法。
                        "description": (
                            "同人续派（调查后确认修 / 改稿 / 收口后接着干）；填已完成 run_id。"
                        ),
                    },
                    "target_folder_id": {
                        "type": "string",
                        "description": (
                            "已解析文件夹 id（该队员坐哪张桌）。跨已登记文件夹须点名；"
                            "裸聊写盘缺桌由运行时建云桌，勿为过闸 create_folder。"
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
            "description": (
                "固化流水线名（非默认快捷进阶）；与 tasks 二选一，填了就不要传 tasks。"
            ),
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
