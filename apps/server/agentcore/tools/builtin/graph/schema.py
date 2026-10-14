"""Delegate tool schema and constants.

Schema layer (工具面瘦身): 判得出就判；判不出只取一次廉价信号，这一次只用来判。
何时用写在本 description（根 / 嵌套共用 ``DELEGATE_WHEN``，窗绑定分叉）；
编制 HOW 写在本按钮（根 / 嵌套共用填参与一块验收；嵌套另加拆层）。
依赖何时填在编制段末句（不点字段名）；取值在 ``depends_on`` 参数。
task 只写工人拿不到的增量。不进常驻核、不另开按需 skill。
信号值到派/不派的对照表不进按钮。
"""

from __future__ import annotations

from agentcore.runtime.graph.admit import empty_tasks as _empty_tasks
from agentcore.runtime.runs.constants import MAX_DELEGATION_TASKS

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

# 何时另开节点（根 / 嵌套共用）。场面表不进按钮。
# 窗是注意力的计价参数。根靠「这扇窗」；嵌套分叉在 NESTED 描述。
DELEGATE_WHEN = (
    "判得出就判。判不出只取一次廉价信号（列目录、条目数与文件名、并列对象数、回执里的字数行数），这一次只用来判，不接着收结论。入口已在这扇窗里，探路结束。"
    "另开节点换墙钟或注意力。墙钟只来自能并行、且每件结论不靠别的件补齐的切片；拆不开，墙钟不计，仍看注意力。"
    "要连着取证，或把还不在这扇窗里的工作区正文读进来、写出去，做完会留下、之后每轮都还在：写 goal + 读回执小于这份注意力，要另开节点。"
    "自己做限于答复本身就是结果，或那一处正文已经在这扇窗里，或点名的少量路径读完即答。装得下 ≠ 顾得住。"
)

# 写 nodes 时必见。末句只写何时填依赖（不点字段名）。取值在 depends_on。
DELEGATE_STAFF_HOW = (
    "拆开仍成立的对象各是一个节点，否则这一批只留一个节点；同一话题的侧面不是。"
    "只读取证在结论拆开仍成立时可以并行；改同一份成品或同一套契约 ≠ 拆开仍成立。"
    "多模块、多文件夹且结论仍独立的仍各是一个节点。"
    "结论已各自独立、下游要吃上游结论时，填进下游对上游的依赖。"
)
# goal 只写这扇窗拿不到的增量。不进按钮正文。
TASK_FILL_HOW = (
    "原话和上游结论已在上下文里，再抄进 goal 增量是 0。"
    "goal 只写这扇窗拿不到的：你拍板而用户没写明的约束、不做什么、可判真假的验收。"
    "没拍板的标成假设。"
    "点名路径用工作区相对正斜杠。"
)
NESTED_STAFF_HOW = "子节点收工后由你整合成这一节点的结论。"

DELEGATE_DESCRIPTION = (
    "往协作图上追加节点。每个节点是一段封口的工作，有自己的窗；下游只看见上游的结论。"
    "你正在做的这段会先封口：调用时写下它的结论。没填依赖的节点接在这段后面，只看见这句。"
    f"{DELEGATE_WHEN}"
    f"{DELEGATE_STAFF_HOW}"
)

# Nested captain: blocking wait, not coordination. Same fill contract; extra 拆层.
NESTED_DELEGATE_DESCRIPTION = (
    "把当前这段拆成子节点。"
    f"{DELEGATE_WHEN}"
    "你的窗跟当前节点走；这段已经是一件就留下。"
    f"{DELEGATE_STAFF_HOW}"
    f"{NESTED_STAFF_HOW}"
)

DELEGATE_PARAMETERS = {
    "type": "object",
    "properties": {
        "conclusion": {
            "type": "string",
            "description": (
                "封口你正在做的这段。后面的段只看见这句，看不见这段里的查找和修改。"
                "写下它们接着干所需要的结论。"
            ),
        },
        "nodes": {
            "type": "array",
            "description": f"≤{MAX_DELEGATION_TASKS}。",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "本批节点 id。depends_on 引用此字面值。",
                    },
                    "goal": {
                        "type": "string",
                        "description": TASK_FILL_HOW,
                    },
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "上游节点 id。"
                            "空=与本批无依赖的节点并行。"
                            "只认本字段。"
                        ),
                    },
                },
                "required": ["id", "goal"],
            },
        },
    },
    "required": ["nodes"],
}
