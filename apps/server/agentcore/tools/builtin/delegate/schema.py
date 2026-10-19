"""Delegate tool schema and constants.

Schema layer (工具面瘦身): 判得出就判；判不出只取一次廉价信号，这一次只用来判。
何时用写在本 description（根 / 嵌套共用 when-to-use 核，窗绑定分叉）；
编制 HOW 写在本按钮（根 / 嵌套共用填参与一块验收；嵌套另加拆层）。
依赖何时填在编制段末句（不点字段名）；取值在 ``depends_on`` 参数。
task 只写工人拿不到的增量。不进常驻核、不另开按需 skill。
信号值到派/不派的对照表不进按钮。
两套 when-to-use 并存：默认注意力尺 ``DELEGATE_WHEN``；对话 ``delegate_mode``
开则 ``DELEGATE_WHEN_PREFER``（优先派）。同一回合只注入一套。
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
# 默认尺：自己做在前，要派比成本；墙钟只计能并行且结论各自成立的切片。
# 人数在 DELEGATE_STAFF_HOW，且只在派了之后。不写成问卷。
# 根按钮首句只钉交回后由你收尾。返回时机在启动回执，不进按钮；协调期开口纪律不进按钮。
DELEGATE_WHEN = (
    "委派规则：自己做：答复即结果，或正文已在这扇窗，或点名少量路径读完即答。"
    "要派：连续取证，或读写还不在这扇窗的工作区正文且做完会留下——写 task + 读回执小于这份注意力才派。"
    "探路判断：判得出就判；判不出只取一次廉价信号（列目录、条目数、文件名、回执字数行数），只用来判，不接着收结论。入口已在这扇窗里，探路结束。"
    "墙钟：只在能并行且每件结论不靠别的件补齐时计入；拆不开不计墙钟，仍看注意力。装得下 ≠ 顾得住。"
)

# 写 tasks 时必见。原 consult(staffing)/lead_subteam 上收进本按钮。
# 成稿查证+起草 / 多来源取证的例子在编排器文档，不进按钮。
# 同一写面用 ≠ 切开：只读取证须结论独立才并行，避免盖过「多来源仍是一名队员取证」。
# 末句只写何时填依赖（不点字段名）。取值在 depends_on 参数。
# 连线的条件：下游任务现在就能写死，且只吃落盘或简报。要看正文才知道下一步的，不连。
DELEGATE_STAFF_HOW = (
    "拆分规则：可拆：结论独立；多模块、多文件夹且独立；只读取证且结论独立。"
    "不拆：改同一成品/契约；同一话题侧面。拆不开则这一批只留一名队员。"
    "原则：拆对象不拆人，拆任务不拆话题。"
    "依赖规则：填依赖：下游能写死，且只吃上游落盘/简报。"
    "不填依赖：要看上游正文才知道下一步，等这批回来再派。"
    "原则：能并行就并行，不串队列。"
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

# 输入区「统筹」开：这一场协调。必须派在前，自己做写成仅当。根首句带「优先」；嵌套不加，靠「卡上已是一件则留下」。
# 相对默认尺：需要干活工具就派（次数少也派）；自己做仅限不必动干活工具，或正文已在窗的短合成。无墙钟、不比成本。不剥工具。
DELEGATE_WHEN_PREFER = (
    "这一场你协调：拆任务、问挡路的、收人。调查、读窗外正文、改文件、跑命令、检索收证是队员的活。"
    "委派规则：必须派：完成它需要干活工具（读/写/改工作区、连续取证、跑命令、检索收证）——次数少也派。拆不开也派一名队员。"
    "自己做仅当：答复本身就是结果、不必动干活工具；或正文已在这扇窗，读完即可短合成。"
    "协调工具（delegate / replan / ask_user / consult 手册）≠ 干活工具（read / write / edit / run / grep / glob / web / browser / host）。"
    "探路判断：判得出就判。判不出只取一次廉价信号（列目录、条目数、文件名、回执字数行数），只用来判：不读正文、不收结论。已判明要动手 → 停，派。"
)
DELEGATE_DESCRIPTION_PREFER = (
    "优先把任务交给队员；他们交回之后，由你对用户收尾。"
    f"{DELEGATE_WHEN_PREFER}"
    f"{DELEGATE_STAFF_HOW}"
)
NESTED_DELEGATE_DESCRIPTION_PREFER = (
    "把当前任务拆给由你指挥的子团队。"
    f"{DELEGATE_WHEN_PREFER}"
    "你的窗跟这张任务卡走，卡上已是一件则留下。"
    f"{DELEGATE_STAFF_HOW}"
    f"{NESTED_STAFF_HOW}"
)


def root_delegate_description(*, prefer: bool) -> str:
    return DELEGATE_DESCRIPTION_PREFER if prefer else DELEGATE_DESCRIPTION


def nested_delegate_description(*, prefer: bool) -> str:
    return NESTED_DELEGATE_DESCRIPTION_PREFER if prefer else NESTED_DELEGATE_DESCRIPTION


def catalog_prefer_description(tool_name: str) -> str:
    """Toolbox inspector: root-CEO prefer variant. Nested is runtime-only."""
    return DELEGATE_DESCRIPTION_PREFER if tool_name == "delegate" else ""


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
                            "点名的上游没全部做成时，下游不自动开工。"
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
