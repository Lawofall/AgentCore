"""Delegate tool schema and constants.

Schema layer (工具面瘦身): 未读前可先买一次廉价信号，再答判据。
何时用写在本 description（根 / 嵌套共用 ``DELEGATE_WHEN``，窗绑定分叉）；
编制 HOW 写在本按钮（根 / 嵌套共用填参与一块验收；嵌套另加拆层）。
task 只写工人拿不到的增量。不进常驻核、不另开按需 skill。
信号值到派/不派的对照表不进按钮。
"""

from __future__ import annotations

from agentcore.runtime.delegate import empty_tasks as _empty_tasks
from agentcore.runtime.delegate.task_models import TASK_MODEL_SCHEMA_PROPS
from agentcore.runtime.runs.constants import MAX_DELEGATION_TASKS, MAX_GAP_FILL_ADDS

EMPTY_DELEGATE_MSG = _empty_tasks.EMPTY_DELEGATE_MSG
HANDWRITTEN_TASKS_SKELETON = _empty_tasks.HANDWRITTEN_TASKS_SKELETON
is_empty_delegate_error = _empty_tasks.is_empty_delegate_error


# Shared task-level artifacts (delegate tasks + replan add).
# CEO / replan fill-in: optional paths only. Write-vs-chat is task
# acceptance + the model; the engine only recognizes pinned paths.
# Leftover nested ``deliverable`` still parses; not advertised.
TASK_ARTIFACTS_SCHEMA: dict[str, object] = {
    "type": "array",
    "items": {"type": "string"},
    "description": "可选路径；省略不催写盘。",
}

# Shared when-to-use（根 / 嵌套；场面表不进按钮）。
# 读之前不必仓促定夺；若仍判不出，先买一次廉价信号再判。装得下收在注意力定义里。
# 窗绑定分叉：根靠「这扇窗」；嵌套=这张任务卡。
# 根按钮首句只钉交回后由你收尾。返回时机在启动回执，不进按钮；协调期开口纪律不进按钮。
DELEGATE_WHEN = (
    "读之前不必仓促定夺；若仍判不出，先取一次廉价信号（列目录、看条目数与文件名、数并列对象、回执里的字数行数），再判。"
    "探路只为定位入口，不为收结论。"
    "收益 = 墙钟（弱依赖切片能并行）或注意力（进这扇窗后每轮都还在，≠ 这一回合装得下）。"
    "成本 = 写 task + 读回执。"
    "拆开后结论还独立成立吗？否→自己做。"
    "换到墙钟或注意力了吗？都没有→自己做。"
    "成本不小于收益 → 自己做。"
)

# 写 tasks 时必见。原 consult(staffing)/lead_subteam 上收进本按钮。
# 成稿查证+起草 / 多来源取证的例子在编排器文档，不进按钮。
# 同一写面用 ≠ 切开：只读取证须结论独立才并行，避免盖过「多来源仍是 1 人取证」。
DELEGATE_STAFF_HOW = (
    "拆开仍成立的对象各是一件，否则整件事 1 人；同一话题的侧面不是，也不按人拆。"
    "只读取证在结论拆开仍成立时可以并行；改同一份成品或同一套契约 ≠ 拆开仍成立。"
    "多模块、多文件夹且结论仍独立的仍各是一件。"
)
# 开局已注入原话 / 前置结果 / 并行队友任务。再抄增量是 0。
# task 只写工人拿不到的三样。不钉「已确认约束：」行格式（引擎不解析）。
TASK_FILL_HOW = (
    "原话、前置结果、并行队友任务已在上下文里，再抄进 task 增量是 0。"
    "task 只写工人拿不到的三样：你拍板而用户没写明的约束、谁不做什么、可判真假的验收。"
    "没拍板的标成假设。"
    "点名路径用工作区相对正斜杠。"
)
NESTED_STAFF_HOW = "收工后由你整合交差。"

DELEGATE_DESCRIPTION = (
    "把任务交给队员；他们交回之后，由你对用户收尾。"
    f"{DELEGATE_WHEN}"
    f"{DELEGATE_STAFF_HOW}"
)

# Nested captain: blocking wait, not coordination. Same fill contract; extra 拆层.
NESTED_DELEGATE_DESCRIPTION = (
    "把当前任务拆给由你指挥的子团队。"
    f"{DELEGATE_WHEN}"
    "你的窗跟这张任务卡走，卡上已是一件则留下。"
    f"{DELEGATE_STAFF_HOW}"
    f"{NESTED_STAFF_HOW}"
)

DELEGATE_PARAMETERS = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "description": f"≤{MAX_DELEGATION_TASKS}。",
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                    "task": {
                        "type": "string",
                        "description": TASK_FILL_HOW,
                    },
                    "artifacts": TASK_ARTIFACTS_SCHEMA,
                    "id": {
                        "type": "string",
                        "description": "节点 id。depends_on 可引用此字面值。",
                    },
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "空=同波并行。"
                            "只认本字段（本批 id / 角色名）≠ task 里写先后。"
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
                        "description": "同人续派；填已完成 run_id。",
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
        "team_brief": {
            "type": "string",
            "description": "有共享口径才写（一行一条）；各 worker 开局可见。",
        },
    },
}
