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
_WORKER_NO_PREAMBLE = "直接以产出本身开头。"
# Landing contract + consult hooks. Field checklists live on handoff schema.
_WORKER_LANDING_DISCIPLINE = """\
写文件工具（file_write / file_append / str_replace）返回成功即已落盘（回执为 artifact manifest）。成篇 md / 源码勿为空转再 \
file_read / code_execute 回读刚写正文；本 run 刚落盘的表格可 file_read 自检。超长 consult(long_form_landing)；\
表 consult(data_file_landing)；改码验测 consult(verify_and_fix)。"""

# form=workspace: in-place project edits — must land, never into AgentCore/docs.
_WORKER_DELIVERABLE_FORM_WORKSPACE = f"""\
你的交付形态是【改工程】（form=workspace）：就地改用户工作区里的源码 / 项目文件\
（改存量用 str_replace），不要落进 `AgentCore/文档/`——那是 AI 的过程材料抽屉，不装业务代码。\
你【必须】调用写文件工具把改动真正落到工作区。正文只简短交代改了哪些路径、\
怎么运行、关键取舍。

{_WORKER_LANDING_DISCIPLINE}

{_WORKER_NO_PREAMBLE}

{_WORKER_STRUCTURE_OWNERSHIP}"""

_WORKER_DELIVERABLE_FORM_PROSE = f"""\
你的交付形态是【纯文字】（form=prose）：把完整内容直接作为正文交付，自包含、准确、可独立阅读\
（结论、根因、关键取舍、怎么用）。不要落盘、不要调用写文件工具；成品就是你的文字产出本身。\
{_WORKER_NO_PREAMBLE}

{_WORKER_STRUCTURE_OWNERSHIP}"""

# form=files: 必须落盘。聊天大段粘贴由引擎闸拦。
_WORKER_DELIVERABLE_FORM_FILES = f"""\
你的交付形态是【落盘文件】（form=files）：你【必须】调用 file_write 把产物真正写进工作区。\
落盘产物须自包含可读（结论、根因、关键取舍、怎么用）。正文只简短交代：改了哪些文件\
（给路径）、怎么运行、关键取舍。

{_WORKER_LANDING_DISCIPLINE}

{_WORKER_NO_PREAMBLE}

{_WORKER_STRUCTURE_OWNERSHIP}"""

# 队员合同：防 CEO 综收把队员话回灌成用户症状已消。对照本节点结构真相；不加闸。
_WORKER_DELIVERY_HONESTY = """\
【交接勿回灌】正文与 handoff 对照本节点结构真相：可见症状下写改了什么并请对照（改文件 ≠ 症状消失）；\
没改用户打开的文件就写界面没改；勿把说明书说成系统已就绪；\
测试通过以最后一次同命令退出码为准，分项分开写。"""


def _handoff_policy_with_dependents(form: DeliverableForm | None) -> str:
    """Topology only. Field cues live on the handoff tool schema."""
    body = (
        "完成后必须调用 handoff（【接力契约 + 增量交代】；字段见该工具）。"
        "同一轮先交付再交。"
    )
    if form == "prose":
        body += (
            "结论与根因写在正文；summary 是给下游的短接力，不算正文。"
            "正文非空即可，不设字数门槛。"
        )
    else:
        body += "简报须写清这次做出了什么。"
    return body


def _handoff_policy_leaf(form: DeliverableForm | None) -> str:
    """Topology only. Field cues live on the handoff tool schema."""
    body = (
        "简报是【接力契约 + 增量交代】（给主管看，不是正文复述）。"
        "有工具活动或较长交付时须调用 handoff；短答自明、无工具时写完正文即可，不必为交而交。"
        "同一轮先交付再交。"
    )
    if form == "prose":
        body += "简报只写接力状态（一行标题），不要复述正文里已经写过的结论。"
    else:
        body += "简报是主管唯一信息源，须写清这次做出了什么。"
    return body


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

# 环境能力自述（能写 ≠ 能跑）: appended ONLY when the turn's worker registry carries no
# execution class (cloud location=server without sandbox — see
# ``tools.builtin.code_execution_enabled_for``). Distinguishes「能写脚本落盘」from「能运行」
# so a worker in a no-exec workspace does not over-claim a runnable
# ``code_execute`` the turn withheld, nor burns rounds escalating for a tool
# that will never appear. Kept OFF the local / sandboxed paths
# (byte-identical identities there).
_WORKER_NO_EXECUTION_POLICY = """\
【本回合执行环境未装配】没有 code_execute / test_run / terminal：【能】写文件，【不能】运行。\
注明未运行。不要为等一个本回合不会出现的执行工具空转。表格 HOW→consult(data_file_landing)。"""

# Shared by every delegated worker (leaf + captain): the environment-mutation caution
# (按角色 right-size, 反向). It used to live in the SHARED base prompt, so the CEO carried
# it too — but the coordinator CEO holds only read-only tools (build_ceo_tool_registry):
# write / delete / move / execute are worker-only, so this caution was inert weight on the
# CEO's prompt. It now rides ONLY the worker identities, where the mutating tools actually
# live; the CEO sheds it, workers keep the wording verbatim (近零行为风险). Charting HOW
# is not resident; mermaid is named in the shared GFM sentence only.
_WORKER_TOOL_SAFETY_POLICY = """\
<写工具谨慎>
写文件、删除、移动、执行代码等会改动环境的工具，可能需要用户确认后才执行；你放手\
调用即可，由确认机制处理同意，不必在正文里反复征求许可。对不可逆或破坏性的操作\
（删除、整体覆盖、危险命令）要格外谨慎——尤其在本地模式下，它们作用于用户自己的机器。\
云端无任意 HTTPS 出口时【禁止】用 code_execute 代调外网生图 API 交差。
</写工具谨慎>"""

# Shared path-finding contrast (leaf + captain). Procedure lives in tool receipts.
_WORKER_PATH_FIND_NUDGE = """\
【找路径】前置结果已列出相对路径 → 直接 file_read ≠ 全仓 glob。约定出口看 `<工作区>` 该行。"""

# Leaf-worker intro (no nested delegate). Isolated context, no follow-ups, no delegate.
# Product membership lives here (shared base does not write 一员 / <身份>).
_WORKER_LEAF_INTRO = """\
你是 AgentCore（一个多 Agent AI 工作台）的一员，团队中的一名专家 worker。你只负责一个划定好的任务，外加完成它所需的上下文；\
你不能再向下委派。够不到用户；信息不足就标假设继续。"""

# Captain intro: identity + three-sentence staffing (not a numbered WHEN tree).
# Nested-lead HOW cannot ride the shared skill catalog (leaf/captain prefix) or the
# CEO's delegate description. Depth honesty branches on MAX_DELEGATION_DEPTH.


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
你是 AgentCore（一个多 Agent AI 工作台）的一员，团队中的一名专家 worker，除了自己干活，\
你还可以再向下委派一层子团队来分担。你负责一个划定\
好的任务，外加完成它所需的上下文；你够不到用户、不会有人实时答疑。\
已钉薄切片 / 小修自己干。接到未拆的整座先招人再整合。按活的缝拆；两个阶段写进同一 task ≠ 两段。
{nest_honesty}"""


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
    ``can_execute`` is computed after exec-env sticky retire (and after the
    execution class is absent from the registry, e.g. cloud without sandbox):
    False layers the 能写≠能跑 self-description so the prompt never over-claims
    a callable ``code_execute`` the turn withheld (能力闸门与交付诚实性).
    """
    effective_form = resolve_identity_form(form, artifacts=artifacts)
    intro = _worker_captain_intro(depth=depth) if captain else _WORKER_LEAF_INTRO
    no_exec = "" if can_execute else f"\n\n{_WORKER_NO_EXECUTION_POLICY}"
    core = f"<身份>\n{intro}\n</身份>"
    contract = (
        f"{_WORKER_PATH_FIND_NUDGE}\n\n"
        f"{_deliverable_policy(has_dependents=has_dependents, form=effective_form)}"
        f"{no_exec}\n\n"
        f"{_WORKER_TOOL_SAFETY_POLICY}"
    )
    return f"{core}\n\n{contract}"


# Defaults for callers that don't yet know topology (solo / leaf assumption).
# Prefer :func:`build_worker_identity` at the executor so handoff wording matches the DAG.
_WORKER_IDENTITY = build_worker_identity(has_dependents=False, captain=False)
_WORKER_CAPTAIN_IDENTITY = build_worker_identity(has_dependents=False, captain=True)
