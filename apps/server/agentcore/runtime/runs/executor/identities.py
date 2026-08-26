from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from agentcore.runtime.runs.constants import MAX_DELEGATION_DEPTH
from agentcore.tools.protocol import Tool

DeliverableForm = Literal["prose", "files", "workspace"]


@dataclass(frozen=True)
class LeadSubteam:
    """A worker-captain's nested-delegation handle (阶段2 嵌套 + 受监督子计划 B).

    The factory mints this in the tools layer so ``runs`` stays free of a concrete
    tools dependency (it only touches the opaque :class:`~agentcore.tools.protocol.Tool`
    objects + the ``dispose`` closure here):

    - ``tools`` — the lead's own ``delegate`` PLUS the companion ``replan`` bound to
      THAT delegate instance. The bundle mints both (dispose / 波边界 binding); the
      opening registry registers ``delegate`` only. ``replan`` is offered after a
      nested sub-plan exists (``_supervised``), via ``promote_coordination_surface_if_needed``.
      Wiring ``replan`` for a lead (not just the root CEO) is the 去特例 fix: a lead
      supervises its own sub-plan's 波边界 (bind_after_deps / 子队员 escalate scope)
      exactly like the CEO — without it a yielding sub-plan would be a dead-end.
    - ``tool_names`` — re-grant those names on a least-privilege allow-list (mirrors
      how ``escalate`` is kept callable for a restricted worker).
    - ``dispose`` — turn-end disposition of a sub-plan the lead yielded but never
      resumed (堵漏账): fold its completed workers' usage/ledger/citations in before
      the parent absorbs this child, so a lead that wrapped up without a ``replan``
      never strands sub-team spend. No-op when nothing is paused; best-effort.
    """

    tools: tuple[Tool, ...]
    tool_names: tuple[str, ...]
    dispose: Callable[[], Awaitable[None]]


# A worker's nested-delegate factory: given (captain_run_id, captain_depth) — the
# worker's own run id + depth — it mints the worker's :class:`LeadSubteam` (its own
# ``delegate`` + the companion ``replan`` bound to it as the sub-team's captain).
# Owned by the DelegateTool (which can import the tools package), passed in here so
# ``runs`` stays free of a concrete tools dependency.
DelegateFactory = Callable[[str, int], LeadSubteam]

# 阻塞式求决策 并发上限 (设计 §4.6): at most this many workers may be suspended on a
# blocking escalate at once (per conversation). Beyond it a further blocking escalate
# degrades to non-blocking (proceed on assumption) — caps card-flood + stops a whole
# wave's width being parked on the user. Tunable; start conservative.
ESCALATION_CONCURRENCY_CAP = 3

# Shared one-liners for every form block (HOW encyclopedia lives in tools / consult).
_WORKER_STRUCTURE_OWNERSHIP = (
    "专业结构由你定：task 里的骨架 / 关注点只是起点线索，不是填字模板或答题边界。"
)
_WORKER_NO_PREAMBLE = (
    "直接以产出本身开头，别写「我来为你生成…」「我是一个 agent」之类开场白或元叙述。"
)
# Compressed landing (files + workspace). Artifact-first / 中间省略 is not identity copy;
# the files handoff field guide may still mention 短骨架.
_WORKER_LANDING_DISCIPLINE = """\
写文件类工具（file_write / file_append / str_replace）返回成功即代表已落盘；写/append \
回执为 artifact manifest（path / chars / lines / hash / 标题树 / 末段预览）——成篇 md / 源码\
【以此验真】，【禁止】为空转质检再 code_execute / file_read 回读刚写正文。本 run 刚落盘的\
表格（csv/xlsx/tsv 等）可用 file_read 回读自检。下一步仅 \
str_replace（局部修订）或同轮 handoff，勿为空转自检。成篇 md【禁止】用 write_section \
或 `<!-- SECTION: -->`（二者仅用于建站 site HTML）。超长成篇细则 consult(long_form_landing)。\
交可打开的表（xlsx 等）时质量基线 consult(data_file_landing)。"""

# form=workspace: in-place project edits — must land, never into AgentCore/docs.
_WORKER_DELIVERABLE_FORM_WORKSPACE = f"""\
你的交付形态是【改工程】（form=workspace）：就地改用户工作区里的源码 / 项目文件\
（改存量用 str_replace），不要落进 `AgentCore/文档/`——那是 AI 的过程材料抽屉，不装业务代码。\
你【必须】调用写文件工具把改动真正落到工作区，聊天粘贴不算交付。正文只简短交代改了哪些路径、\
怎么运行、关键取舍。

{_WORKER_LANDING_DISCIPLINE}

{_WORKER_NO_PREAMBLE}

{_WORKER_STRUCTURE_OWNERSHIP}"""

_WORKER_DELIVERABLE_FORM_PROSE = f"""\
你的交付形态是【纯文字】（form=prose）：把完整内容直接作为正文交付，自包含、准确、可独立阅读。\
不要落盘、不要调用写文件工具；成品就是你的文字产出本身（回答 / 分析 / 汇报 / 创意文字等）。\
{_WORKER_NO_PREAMBLE}

{_WORKER_STRUCTURE_OWNERSHIP}"""

# form=files: must-land defence (must not weaken the 46k-HTML-in-chat gate).
_WORKER_DELIVERABLE_FORM_FILES = f"""\
你的交付形态是【落盘文件】（form=files）：你【必须】调用 file_write 把产物真正写进工作区，\
而不是把整份内容粘在回复正文里。只贴在聊天里的代码不算交付。正文只简短交代：改了哪些文件\
（给路径）、怎么运行、关键取舍，不要再整份粘贴文件内容。

{_WORKER_LANDING_DISCIPLINE}

{_WORKER_NO_PREAMBLE}

{_WORKER_STRUCTURE_OWNERSHIP}"""

_HANDOFF_FIELD_GUIDE_PROSE = """\
先把交付正文写完，再在【同一轮】调用 handoff：
- summary（结论）：一句话说清你这次做出了什么 / 核心结论。
- key_points（关键要点）：下游或主管最该知道的 2-4 条（具体数字 / 关键决定，别空泛）。
- assumptions（关键假设）：信息不足时你采用的关键假设（没有就省略此条）。
- next_steps（建议下一步）：基于你这一环的发现，团队 / 用户接下来值得考虑做什么（没有就省略）。\
这只是顺带给主管的建议、供其与用户定夺，不替谁拍板、也不是停工理由——它与 escalate 不同：\
escalate 是「缺了它整件事会走偏、需要现在有人拍板」，交接简报里的建议是「我已做完、\
提示个后续方向」。
调用 handoff 即代表你这次的活已完成；别把简报重复写进交付正文，也别在还没产出交付时就调它。"""

_HANDOFF_FIELD_GUIDE_FILES = """\
先用 file_write 把产物落盘（可一次写完完整正文，或超长时先短骨架再按节 file_append / \
str_replace 填空），\
再在【同一轮】调用 handoff：
- summary（结论）：一句话说清你这次做出了什么 / 核心结论。
- key_points（关键要点）：下游或主管最该知道的 2-4 条（具体路径 / 怎么运行 / 关键决定，别空泛）。
- assumptions（关键假设）：信息不足时你采用的关键假设（没有就省略此条）。
- next_steps（建议下一步）：基于你这一环的发现，团队 / 用户接下来值得考虑做什么（没有就省略）。\
这只是顺带给主管的建议、供其与用户定夺，不替谁拍板、也不是停工理由——它与 escalate 不同：\
escalate 是「缺了它整件事会走偏、需要现在有人拍板」，交接简报里的建议是「我已做完、\
提示个后续方向」。
调用 handoff 即代表你这次的活已完成；别把简报重复写进交付正文，也别在还没产出交付时就调它。"""

# 巡检定案 B：worker 交付各一句，防 CEO 综收把「已修复 / 已就绪 / 中途绿」回灌成用户症状已消。
# 不扩姿势 A 词表、不加闸。
_WORKER_DELIVERY_HONESTY = """\
【交接勿回灌】正文与 handoff：用户报了可见症状时勿写「修复完成 / 已修复 / 现象已消除 / 已全部落地」——写改了什么、请对照看一眼；\
勿把提示词包 / 脚本 / 说明书说成「系统已就绪」——没改用户打开的文件就写界面没改；\
说测试通过以最后一次同命令退出码为准，中途绿最后红报红的，分项分开写。"""


def _handoff_field_guide_leaf(form: DeliverableForm | None) -> str:
    """Leaf field guide. De-conclusion only when form=prose (CEO reads the body)."""
    if form == "prose":
        write_first = "先把交付正文写完"
        body_audience = (
            "正文是给人读的说明：结论、根因、关键取舍、意外、怎么用。"
        )
        kp = "下游或主管最该知道的 2-4 条（具体数字 / 关键决定，别空泛）"
        summary_line = "- summary（一行标题式）：点出这次交付是什么，勿写成结论段。"
        deconclude = (
            "简报只写接力状态（完成边界、明确没做什么、未决项/阻塞、指回产出的指针）；"
            "正文里已经写过的结论不要在简报里再说一遍。\n"
        )
    elif form in ("files", "workspace"):
        write_first = (
            "先用 file_write 把产物落盘（可一次写完完整正文，或超长时先短骨架再按节 "
            "file_append / str_replace 填空）"
        )
        if form == "workspace":
            body_audience = (
                "就地改工程：落盘产物写在工作区里它本该在的位置，不要落进 `AgentCore/文档/`；"
                "聊天正文只交代路径、怎么运行、关键取舍。"
            )
        else:
            body_audience = (
                "落盘产物是给人读的完整说明：结论、根因、关键取舍、意外、怎么用；"
                "聊天正文只交代路径、怎么运行、关键取舍。"
            )
        kp = "下游或主管最该知道的 2-4 条（具体路径 / 怎么运行 / 关键决定，别空泛）"
        summary_line = "- summary（结论）：一句话说清你这次做出了什么 / 核心结论。"
        deconclude = ""
    else:
        # omit → files (resolved before this helper; keep files copy as fallback)
        write_first = (
            "先用 file_write 把产物落盘（可一次写完完整正文，或超长时先短骨架再按节 "
            "file_append / str_replace 填空）"
        )
        body_audience = (
            "落盘产物是给人读的完整说明：结论、根因、关键取舍、意外、怎么用；"
            "聊天正文只交代路径、怎么运行、关键取舍。"
        )
        kp = "下游或主管最该知道的 2-4 条（具体路径 / 怎么运行 / 关键决定，别空泛）"
        summary_line = "- summary（结论）：一句话说清你这次做出了什么 / 核心结论。"
        deconclude = ""
    return (
        f"{write_first}，再在【同一轮】调用 handoff：\n"
        f"{body_audience}\n"
        f"{deconclude}"
        f"{summary_line}\n"
        f"- key_points（关键要点）：{kp}。\n"
        "- assumptions（关键假设）：信息不足时你采用的关键假设（没有就省略此条）。\n"
        "- next_steps（建议下一步）：基于你这一环的发现，团队 / 用户接下来值得考虑做什么"
        "（没有就省略）。"
        "这只是顺带给主管的建议、供其与用户定夺，不替谁拍板、也不是停工理由——"
        "它与 escalate 不同：escalate 是「缺了它整件事会走偏、需要现在有人拍板」，"
        "交接简报里的建议是「我已做完、提示个后续方向」。\n"
        "调用 handoff 即代表你这次的活已完成；别把简报重复写进交付正文，"
        "也别在还没产出交付时就调它。"
    )


def _handoff_field_guide(form: DeliverableForm | None, *, leaf: bool = False) -> str:
    if leaf:
        return _handoff_field_guide_leaf(form)
    if form == "prose":
        return _HANDOFF_FIELD_GUIDE_PROSE
    if form == "workspace":
        return _HANDOFF_FIELD_GUIDE_FILES
    return _HANDOFF_FIELD_GUIDE_FILES


def _handoff_policy_with_dependents(form: DeliverableForm | None) -> str:
    body = (
        "完成后，必须调用 handoff 工具【收尾并提交交接简报】——简报是给下游队员的【接力契约 + 增量交代】"
        "（不是正文复述，几句话即可）：下游靠你的简报接力继续干，缺了他们会丢关键信息。\n"
        f"{_handoff_field_guide(form)}"
    )
    if form == "prose":
        body += (
            "\n结论与根因写在回复正文更清楚；handoff 的 summary 是给下游的短接力，不是正文替代。"
            "summary 不算正文；正文非空即可，不设字数门槛。"
        )
    return body


def _handoff_policy_leaf(form: DeliverableForm | None) -> str:
    incremental = (
        "关键假设 / 风险 / 建议下一步"
        if form == "prose"
        else "关键假设 / 风险 / 建议下一步 / 落盘文件清单"
    )
    # Only prose leaves go pass_through (no files_touched); CEO reads the body.
    # files / workspace land artifacts → pointer; brief stays the CEO's only source.
    brief_shape = (
        "一行标题 + 接力状态" if form == "prose" else "结论 + 关键要点"
    )
    return (
        "简报是【接力契约 + 增量交代】（给主管看，不是正文复述）："
        "有工具活动或较长交付时须调用 handoff 交短摘要"
        f"（{brief_shape}；有增量再补 {incremental}），"
        "否则对账会标成汇报不完整；"
        "短答自明、无工具时写完正文即可结束，不必为交而交。若调用：\n"
        f"{_handoff_field_guide(form, leaf=True)}"
    )


def _form_block(form: DeliverableForm | None) -> str:
    if form == "prose":
        return _WORKER_DELIVERABLE_FORM_PROSE
    if form == "workspace":
        return _WORKER_DELIVERABLE_FORM_WORKSPACE
    return _WORKER_DELIVERABLE_FORM_FILES


def resolve_identity_form(
    form: DeliverableForm | None,
    *,
    artifacts: Sequence[str] | None = None,
) -> DeliverableForm:
    """Coerce identity form: omit / invalid → files. Non-empty artifacts ⇒ files.

    Explicit ``prose`` / ``workspace`` win. Omitted form is files (no two-way
    self-judgment). Artifacts with omitted form still select the files block.
    """
    if form == "prose":
        return "prose"
    if form == "workspace":
        return "workspace"
    if form == "files" or bool(artifacts):
        return "files"
    return "files"


def _deliverable_policy(
    *, has_dependents: bool, form: DeliverableForm | None = None
) -> str:
    """Compose form policy + topology-split handoff wording."""
    handoff = (
        _handoff_policy_with_dependents(form)
        if has_dependents
        else _handoff_policy_leaf(form)
    )
    return f"{_form_block(form)}\n\n{handoff}\n\n{_WORKER_DELIVERY_HONESTY}"

# Shared by every delegated worker (leaf + captain): when to post on the team note
# wall. Solo setup strips this constant by exact replace — keep the replacement mechanism.
_WORKER_TEAM_NOTE_POLICY = """\
会改变还在跑的队友才 post_note 一行；并行摸底/调研时入口与关键结论也可贴一行以免重复通读（不是聊天、不要求回复）；墙上已有的主协调共识不必复读；完工别贴。"""

# 环境能力自述（能写 ≠ 能跑）: appended ONLY when the turn's worker registry carries no
# execution class (cloud location=server without sandbox — see
# ``tools.builtin.code_execution_enabled_for``). Distinguishes「能写脚本落盘」from「能运行」
# so a worker in a no-exec workspace neither fabricates「已运行 / 已生成」nor burns rounds
# escalating for a tool that will never appear this turn. Kept OFF the local / sandboxed
# paths (byte-identical identities there).
_WORKER_NO_EXECUTION_POLICY = """\
【本回合执行环境未装配】你没有 code_execute / test_run / terminal 这类执行工具：\
你【能】用写文件工具把脚本 / 源码 / 配置 / 文档落盘，但【不能】运行它们，也无法生成\
需要运行程序才能产出的二进制 / 可播放文件（如 .pptx / .xlsx / 图片 / 可执行文件）。\
不要为等一个本回合不会出现的执行工具反复升级或空转；也绝不要谎称「已运行 / 已验证 / 已生成」。\
如实交付你真正落盘的内容，并在正文与交接简报里注明「未运行验证，需在有执行环境的机器上运行生成」。\
【表格/数据】没有代码执行时，完整交付是原件结构报告（只写附件结构面已给的列名/行数/类型/样例，\
或抽出文本里能看见的形态）+ 待跑变换脚本 + 一句「运算环境暂时不可用，稍后再试」。\
这就是完成，不是缺口。【禁止】手抄单元格交差、禁止谎称已生成/已校验。你本 run 刚 file_write \
落盘的表格可以 file_read 回读自检，但不能替代对用户原表的解析。"""

# Shared by every delegated worker (leaf + captain): the environment-mutation caution
# (按角色 right-size, 反向). It used to live in the SHARED base prompt, so the CEO carried
# it too — but the coordinator CEO holds only read-only tools (build_ceo_tool_registry):
# write / delete / move / execute are worker-only, so this caution was inert weight on the
# CEO's prompt. It now rides ONLY the worker identities, where the mutating tools actually
# live; the CEO sheds it, workers keep the wording verbatim (近零行为风险). Symmetric to the
# charting HOW moving the OTHER way, onto the CEO-only <visualization> block.
_WORKER_TOOL_SAFETY_POLICY = """\
<tool_safety>
写文件、删除、移动、执行代码等会改动环境的工具，可能需要用户确认后才执行；你放手\
调用即可，由确认机制处理同意，不必在正文里反复征求许可。对不可逆或破坏性的操作\
（删除、整体覆盖、危险命令）要格外谨慎——尤其在本地模式下，它们作用于用户自己的机器。\
云端无任意 HTTPS 出口时【禁止】用 code_execute 代调外网生图 API 交差。
</tool_safety>"""

# Shared problem-handling tiers for leaf + captain workers. Both identities embed
# this via f-string so the guidance is stated once (leaf and captain only differ in
# intro + captain's nested-delegation preamble).
_WORKER_PROBLEM_HANDLING = """\
碰到问题时按以下三档处理：
- 小问题（路径拼写、import 缺失、格式报错、依赖安装）：自己修，不用上报。
- 中等问题（测试挂了、需要多改一个文件、某个依赖的接口和预期不一致）：尝试修一轮；\
修好了继续交付，修不好就用 escalate 上报原因和你尝试过的方案。
- 大问题（方案根本走不通、需要改接口设计、任务范围明显超出你的职责、缺少关键信息\
无法合理假设、权威文档冲突——用户点名为准或已写入 task 的设计稿与代码/其它权威稿不一致）：\
立即用 escalate 上报，不要自行决定方向（含勿静默改权威稿）。
默认原则：信息不足时做出最合理的假设、简短说明，然后照常交付——不要为小事停下。\
有把握就报一声继续做；猜错会作废、或只有上级能定的关键岔路，就停下来等。"""

# Shared path-finding nudge (leaf + captain): avoid reading vague workspace roots.
# Inserted in build_worker_identity — not inside captain nesting preamble (P3 surface).
_WORKER_PATH_FIND_NUDGE = """\
【找路径】「前置结果」已列出具体相对路径 → 直接 file_read 那些路径，【禁止】再全仓 \
file_list / grep 当开工。笔记、墙上或前置结果已有入口/结论 → 接着补缺口；\
【禁止】把已覆盖的面再整仓 file_list / 通读一遍。仅路径含糊（「根」/ `.` / 仅根标签）或列表缺文件时：先 \
file_list(pattern)（非 * 即整仓按名查找）/\
 grep（不确定则省略 path）/code_search 钉真实文件再 \
file_read；磁盘上已有的具体相对路径可直接读。看已有源码正文用 file_read（可分页）；搜/计符号用 grep / \
code_search。【禁止】为看内容用 code_execute print / 整文件 dump，也【禁止】open 源码再正则扫描。\
约定文档出口是写入落点（见 `<workspace_context>` \
该行「现有」/「当前为空」），勿按话题拼接文件名；清单没有的先 file_list 该目录。工具报路径不存在时按回报里的\
上级样本或根查找提示纠偏；约定出口列目录若回报空目录属正常（写入时会自动创建），勿当错误反复重试。"""

# Leaf-worker intro (no nested delegate). A leaf runs in an isolated context with
# one scoped task, no chance to ask follow-ups, and no `delegate` tool — stated
# explicitly so it makes a reasonable assumption and delivers, instead of punting
# with a clarifying question it can never get answered.
_WORKER_LEAF_INTRO = f"""\
你是团队中的一名专家 worker。你只负责一个划定好的任务，外加完成它所需的上下文；\
你不能再向下委派。{_WORKER_PROBLEM_HANDLING}"""

# Captain intro for any worker within the depth cap (delegation is on by default —
# there is no per-node opt-in flag). WHEN 短判决 + 嵌套 lead 编排 HOW（怎么拆 /
# 何时不该拆 / 控制权交回后怎么续跑）都住 identity——共享目录不列编排手册，以保住
# 全体队员前缀一致。Nesting honesty branches on ``depth`` vs
# ``MAX_DELEGATION_DEPTH``: children of a near-cap captain are leaves; shallower
# captains' children may still nest. Workers at the cap get the leaf intro.


def _worker_captain_intro(*, depth: int) -> str:
    # Children land at depth+1; they may nest iff depth+1 < MAX (i.e. depth < MAX-1).
    if depth < MAX_DELEGATION_DEPTH - 1:
        nest_honesty = (
            "你可以把它拆给一支由你指挥的子团队（你的子成员仍可再向下委派一层），看到他们的"
            "产出后由你整合。"
        )
    else:
        nest_honesty = (
            "你可以把它拆给一支由你指挥的子团队（只能再嵌套这一层，你的子成员不能再向下委派），"
            "看到他们的产出后由你整合。"
        )
    return f"""\
你是团队中的一名专家 worker，除了自己干活，你还可以再向下委派一层子团队来分担。你负责一个划定\
好的任务，外加完成它所需的上下文；你够不到用户、不会有人实时答疑。\
【开局】摸底一页地图 / 已钉薄切片 / 小修 → 默认自己干，禁止开局先招人再通读；禁止因为已经做了很久再招人。\
接到（整座成果 / 多模块工程 / 完整可跑壳）且任务未钉成单切片 → 先招人再整合（不是先深读再招）。\
优先把能独立的块交出去；不要先通读长文档、不要在思考里先做完整设计来代替招人。\
拆得清也可以本层一次做完；缝不清可以先短摸底再招——不是必须第一下就招人。\
任务写成「你去把整座做完」仍算未拆编制。有 delegate 就可以招，不看任务里有没有「先组队」。\
接到的已是薄切片、读仓后发现是整座仓 → escalate（范围），禁止默默扩编。\
「不要为委派而委派」只约束本来就小的活，不授权一个人扛里程碑。
怎么拆：按活的自然缝，不按工种凑人。一块够大、够独立 → delegate 交子成员，task 只写目标·约束·验收；\
细粒度已清楚 → 本层一次拆完。同一摊只走一条路，勿自己带队同时又平级再派同职责。\
【假两段·禁】两个阶段写进同一 task 不算两段——须拆成不同 task，或等控制权交回后再派下一波。\
何时不该拆：单文件 / 已钉薄壳 / 小修 / 摸底一页地图 → 默认自己干；仅当本方向内仍有互不影响、可同时查的独立块才拆。禁止为显得主动而再招人。禁止因为已经做了很久再招人。\
控制权交回（delegate 返回『计划已让出』）后用 replan 把未跑步骤定稿，或不续跑则 stop；\
replan 只在已有子计划后出现，开场只有 delegate。
{nest_honesty}{_WORKER_PROBLEM_HANDLING}"""


def build_worker_identity(
    *,
    has_dependents: bool,
    captain: bool = False,
    depth: int = 1,
    form: DeliverableForm | None = None,
    artifacts: Sequence[str] | None = None,
    can_execute: bool = True,
) -> str:
    """Assemble a worker's identity preamble (topology-split handoff + leaf/captain).

    ``has_dependents`` comes from the DAG at identity-build time (``node_has_dependents``):
    upstream nodes get the imperative handoff relay; leaves get the conditional
    「有增量才写」wording. ``captain`` selects the nested-delegation intro;
    ``depth`` (when captain) picks honest child-nesting copy vs ``MAX_DELEGATION_DEPTH``.
    ``form`` selects the deliverable-form block (omit = files).
    Non-empty ``artifacts`` still select the files-form prompt.
    ``can_execute`` mirrors whether the execution class (code_execute / test_run) is in
    this turn's worker registry — False layers the 能写≠能跑 self-description in so the
    prompt never over-claims capability the toolset withheld (能力闸门与交付诚实性).
    """
    effective_form = resolve_identity_form(form, artifacts=artifacts)
    intro = _worker_captain_intro(depth=depth) if captain else _WORKER_LEAF_INTRO
    no_exec = "" if can_execute else f"\n\n{_WORKER_NO_EXECUTION_POLICY}"
    return (
        f"{intro}\n\n"
        f"{_WORKER_PATH_FIND_NUDGE}\n\n"
        f"{_WORKER_TEAM_NOTE_POLICY}\n\n"
        f"{_deliverable_policy(has_dependents=has_dependents, form=effective_form)}"
        f"{no_exec}\n\n"
        f"{_WORKER_TOOL_SAFETY_POLICY}"
    )


# Defaults for callers that don't yet know topology (solo / leaf assumption).
# Prefer :func:`build_worker_identity` at the executor so handoff wording matches the DAG.
_WORKER_IDENTITY = build_worker_identity(has_dependents=False, captain=False)
_WORKER_CAPTAIN_IDENTITY = build_worker_identity(has_dependents=False, captain=True)
