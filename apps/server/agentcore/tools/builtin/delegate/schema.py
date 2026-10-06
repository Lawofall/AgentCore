"""Delegate tool schema and constants.

Schema layer (工具面瘦身): 判得出就判；判不出只取一次廉价信号，这一次只用来判。
何时用写在本 description（根 / 嵌套共用 ``DELEGATE_WHEN``，窗绑定分叉）；
编制 HOW 写在本按钮（根 / 嵌套共用填参与一块验收；嵌套另加拆层）。
依赖何时填在编制段末句（不点字段名）；取值在 ``depends_on`` 参数。
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
# 判得出就判；判不出只取一次廉价信号，这一次只用来判。入口已在，探路结束。
# 窗是注意力的计价参数，不是单独一问。根靠「这扇窗」；嵌套分叉在 NESTED 描述。
# 墙钟只计能并行且结论各自成立的切片；拆不开仍看注意力。
# 自己做限于答复就是结果、正文已在窗里、点名少量路径读完即答。装得下 ≠ 顾得住。
# 人数在 DELEGATE_STAFF_HOW，且只在派了之后。不写成问卷。
# 根按钮首句只钉交回后由你收尾。返回时机在启动回执，不进按钮；协调期开口纪律不进按钮。
DELEGATE_WHEN = (
    "判得出就判。判不出只取一次廉价信号（列目录、条目数与文件名、并列对象数、回执里的字数行数），这一次只用来判，不接着收结论。入口已在这扇窗里，探路结束。"
    "委派换墙钟或注意力。墙钟只来自能并行、且每件结论不靠别的件补齐的切片；拆不开，墙钟不计，仍看注意力。"
    "要连着取证，或把还不在这扇窗里的工作区正文读进来、写出去，做完会留下、之后每轮都还在：写 task + 读回执小于这份注意力，要派。"
    "自己做限于答复本身就是结果，或那一处正文已经在这扇窗里，或点名的少量路径读完即答。装得下 ≠ 顾得住。"
)

# 写 tasks 时必见。原 consult(staffing)/lead_subteam 上收进本按钮。
# 成稿查证+起草 / 多来源取证的例子在编排器文档，不进按钮。
# 同一写面用 ≠ 切开：只读取证须结论独立才并行，避免盖过「多来源仍是一名队员取证」。
# 末句只写何时填依赖（不点字段名；结论已各自独立时）。取值在 depends_on 参数。
DELEGATE_STAFF_HOW = (
    "派了之后：拆开仍成立的对象各是一件，否则这一批只留一名队员；同一话题的侧面不是，也不按人拆。"
    "只读取证在结论拆开仍成立时可以并行；改同一份成品或同一套契约 ≠ 拆开仍成立。"
    "多模块、多文件夹且结论仍独立的仍各是一件。"
    "结论已各自独立、下游验收要吃上游产出时，填进下游对上游的依赖。"
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
                            "填上游的本批 id 或无歧义角色名。"
                            "空=同波并行。"
                            "只认本字段 ≠ task 里写先后。"
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
