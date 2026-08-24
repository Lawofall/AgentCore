"""Delegate tool schema and constants.

Schema layer (工具面瘦身): short trigger + key param cues only. Routing judgment
lives in the CEO core; advanced HOW lives in ``consult(team_orchestration_advanced)``.
"""

from __future__ import annotations

from agentcore.runtime.delegate.force_scopes import FORCE_GATES
from agentcore.runtime.delegate.playbook_declaration import HANDWRITTEN_TASKS_SKELETON
from agentcore.runtime.delegate.task_models import TASK_MODEL_SCHEMA_PROPS
from agentcore.runtime.runs.constants import MAX_DELEGATION_TASKS, MAX_GAP_FILL_ADDS
from agentcore.runtime.runs.playbooks import PLAYBOOKS, playbook_args_schema_description

# Shared task-level deliverable shape (delegate tasks + replan binds/add).
TASK_DELIVERABLE_SCHEMA: dict[str, object] = {
    "type": "object",
    "description": "可选交付物。form；已拍板验收写入「已确认约束」；细则→team_orchestration_advanced。",
    "properties": {
        "form": {
            "type": "string",
            "enum": ["prose", "files"],
            "description": "prose=看；files=用（须落盘）。",
        },
        "required_sections": {"type": "array", "items": {"type": "string"}},
        "output_format": {"type": "string", "enum": ["text", "json"]},
        "artifacts": {"type": "array", "items": {"type": "string"}},
        "artifact_dir": {
            "type": "string",
            "description": "约定落盘目录（可省；写码类改 workspace_native）。",
        },
        "workspace_native": {
            "type": "boolean",
            "description": "true=用户工作区原生文件（改代码/写测试）；与 AgentCore/文档 落点冲突时以本字段为准。",
        },
        "citation_mode": {
            "type": "string",
            "enum": ["two_phase"],
            "description": "two_phase=A 草案不跑成稿闸、同 worker 升 B 再验。省略=非两阶段。",
        },
        "strict": {
            "type": "boolean",
            "description": "不达标：true=硬退；false=软接受。",
        },
    },
}

# Trigger + short cues. Long HOW → CEO core / team_orchestration_advanced.
DELEGATE_DESCRIPTION = (
    f"拆任务给临时团队（默认手写顶层 tasks：role+task，≤{MAX_DELEGATION_TASKS}；非终结）。"
    f"默认可抄：{HANDWRITTEN_TASKS_SKELETON}（deliverable 可选）。"
    "具名 playbook 仅当走固化流水线时用（快捷进阶），且勿同时传 tasks。"
    "【看】→deliverable.form=prose；【用】→files。"
    "多任务先判生产者→消费者；互不依赖才平铺并行。"
    "≥1 worker 默认协调（立即返回、可同回合追加同一张图；含单 worker）。"
    "跨回合再派人＝新开一队、接续上一张图（append_to_execution_id 只填 latest）。"
    "动【同一支团队】（含批次已收口后补跑/接着干）＝ tasks[] 上填 "
    "continue_from_run_id（续派，不限条数）/ replaces_run_id（补缺口），不是冷派整团。"
    "playbook 与 tasks 二选一：禁止二者同时有内容（反例：既填 code_audit 又传 tasks）。"
    "绿场必填 playbook_args.app。"
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
                    "result_handling": {
                        "type": "string",
                        "enum": ["pass_through", "summarize"],
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
                        # 「动同一支团队的正式入口 / 不限条数 / 勿冷派整团」在工具描述里已写，
                        # 这里只留本字段自己的填法。
                        "description": (
                            "同人续派（调查后确认修 / 改稿 / 收口后接着干）；填已完成 run_id。"
                        ),
                    },
                    "checkpoint_after": {"type": "boolean"},
                    "bind_after_deps": {"type": "boolean"},
                    "require_upstream": {
                        "type": "boolean",
                        "description": "false=≥1 上游成功即跑；true=须全量。",
                    },
                    "force_continue": {"type": "boolean"},
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
        "coordinate": {
            "type": "boolean",
            "default": True,
            "description": "协调（默认 true）；false=阻塞。",
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
        "coordination": {
            "type": "string",
            "enum": ["wall", "none"],
            "default": "none",
        },
        "seed_notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["decision", "heads_up", "claim"],
                    },
                    "text": {"type": "string"},
                },
                "required": ["text"],
            },
        },
        "team_brief": {
            "type": "string",
            "description": (
                "全队共识（含「已确认约束」）；各 worker 开局可见；"
                "约束块优先于附件旧角色表。"
            ),
        },
        "complexity_hint": {
            "type": "string",
            "enum": ["light", "standard"],
        },
    },
}
