"""Delegate tool schema and constants.

Schema layer (工具面瘦身): short trigger + firing when/not + key param cues.
编制自选 / 结局分层 lives in the CEO core; advanced HOW lives in
``consult(team_orchestration_advanced)``.
"""

from __future__ import annotations

from agentcore.runtime.delegate.force_scopes import FORCE_GATES
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
        "交付形态。省略或空对象=form=files（默认工作稿/）。"
        "已拍板验收写入「已确认约束」；细则→team_orchestration_advanced。"
    ),
    "properties": {
        "form": {
            "type": "string",
            "enum": ["prose", "files", "workspace"],
            "description": (
                "【看】prose；【存文档】files（默认，落工作稿）；"
                "【改工程】workspace。漏填=files。"
            ),
        },
        "artifacts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "钉具体路径（不扫 task 自由文）。",
        },
    },
}

# Trigger + firing when/not + structure contract. HOW → consult(team_orchestration_advanced).
DELEGATE_DESCRIPTION = (
    f"拆任务给临时团队（默认手写顶层 tasks：role+task，≤{MAX_DELEGATION_TASKS}；非终结）。"
    "用：改产物、成规模取证、点名对比、跨模块摸底。"
    "不用：讨论/判断/闲聊、已知一两处文件、单符号。"
    "playbook 与 tasks 二选一（具名 playbook 仅固化流水线快捷进阶）："
    "禁止二者同时有内容（反例：既填 code_audit 又传 tasks）；绿场必填 playbook_args.app。"
    "HOW→consult(team_orchestration_advanced)。"
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
                "手写此数组时勿填 playbook；与具名 playbook 互斥。"
            ),
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                    "task": {
                        "type": "string",
                        "description": (
                            "自包含=目标+边界+验收（worker 看不到完整历史）。"
                            "细则进 artifacts/team_brief。已拍板写入「已确认约束」。"
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
        "force": {
            "type": "array",
            "items": {"type": "string", "enum": list(FORCE_GATES)},
            "description": (
                "逐闸点名放行（只开列出的那道，无「全开」）：见各闸拒绝正文里的 scope 名。"
                "同团队用 continue_from_run_id/replaces_run_id。"
            ),
        },
        "playbook": {
            "type": "string",
            "enum": sorted(PLAYBOOKS),
            "description": "固化流水线名（非默认）；填了就不要传 tasks。绿场→build_app。",
        },
        "playbook_args": {
            "type": "object",
            "description": playbook_args_schema_description(),
        },
        "team_brief": {
            "type": "string",
            "description": (
                "全队共识（含「已确认约束」）；各 worker 开局可见；"
                "约束块优先于附件旧角色表。非空会建便签墙并按行贴。"
            ),
        },
    },
}
