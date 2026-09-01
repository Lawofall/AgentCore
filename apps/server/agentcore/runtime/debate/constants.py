"""辩论域共享常量（prompt / schema / events 共用，单一源）。"""

from __future__ import annotations

from agentcore.runtime.debate.types import DebateForm
from agentcore.runtime.runs.retrieval_budget import (
    DEFAULT_RETRIEVAL_BUDGET_DEBATER_WITH_DOSSIER,
)

DEBATE_OUTPUT_LIMIT = 16000

# 真纯丙·H4：已退役 DEBATER_TOOLS / WITNESS_TOOLS 系统只读窄名单。
# 辩手/证人默认与全开工具面一致（仍受写盘授权 / write_scope）；只读纪律靠角色提示自觉。

# 庭前无 pack / pack 非 full 时：发言期对称有界检索（复用约定文档残搜槽位，不平行发明第二套）。
# 庭前调查员舰队已删；此常量只驱动辩手 debater_retrieval_budgets。
BOUNDED_GAP_FILL_RETRIEVAL_BUDGET = DEFAULT_RETRIEVAL_BUDGET_DEBATER_WITH_DOSSIER

# 辩手发言长度指引：旧观测里单方动辄数千 token（一条就几十秒），既拖慢又稀释论点。引导「宁深
# 勿长」——聚焦最有力的少数论点，显著降低每轮墙钟与 token。首轮立论与后续轮续写都注入。
LENGTH_HINT = (
    "聚焦你最有力的 2–3 个论点、约 400–600 字讲透，宁深勿长——不堆砌、不面面俱到。"
)

# 结辩陈词长度预算（阶段化发言角色 P4）：结辩是收束不是新立论，比逐轮发言更短——只留最能定胜负的
# 话。显著收紧长度是「阶段化长度预算」的落点（立论 400–600 字 → 结辩 150–250 字），避免结辩变成
# 又一轮长篇复述。仅结辩环节（:func:`~agentcore.runtime.debate.prompt.closing_task`）注入。
CLOSING_LENGTH_HINT = (
    "结辩要【短而有力】：约 150–250 字收束，只留最能定胜负的话，删掉一切铺垫、复述与新枝节。"
)

# 质询作答长度预算：逐条须写完（表态 + 论据），禁止在冒号 / 列举 /「理由是」处截断。
# 仅质询成稿 brief（:func:`~agentcore.runtime.debate.prompt.cx_draft_brief`）注入；
# 装配端另有尾部悬垂检测 + 一次自动续写补全。
CX_LENGTH_HINT = (
    "质询作答须【逐条写完】：每条约 120–220 字讲透表态与论据；每条必须以完整句子收束，"
    "禁止在冒号、未闭合列表或「理由是 / 如下 / 包括」处截断停笔。"
)

# 「快速对碰」(thorough=False，主持人单轮即收) 的辩手附加约束。观测：即便是 trivial 命题，快速辩
# 论的辩手仍各刷十余次 web_search、跑近十轮 ReAct（自停于内容、远未触及安全上限），墙钟与成本几乎
# 全耗在这。轮数上限不是有效杠杆（辩手自停在上限内），真正的杠杆是【告诉辩手这是轻量交锋】——直接
# 压「检索次数」与「论点广度」。仅快速模式注入；认真辩透（thorough=True）不加，保留深挖取证。
QUICK_DEBATER_HINT = (
    "【快速对碰】这是一次轻量单轮交锋：以你的常识与推理直接立论，能不检索就不检索"
    "（至多 1 次必要取证），只把你【最有力的 1 个论点】讲透即可——不深挖、不多角度铺开。"
)

# 展示名：键必须穷尽 DebateForm（权威成员集）；漏键 / 多键在 import 时炸。
FORM_LABELS: dict[DebateForm, str] = {
    DebateForm.DEBATE: "正反辩论",
    DebateForm.RED_TEAM: "红队挑刺",
    DebateForm.ROUNDTABLE: "多方圆桌",
}
if set(FORM_LABELS) != set(DebateForm):
    missing = set(DebateForm) - set(FORM_LABELS)
    extra = set(FORM_LABELS) - set(DebateForm)
    raise RuntimeError(
        f"FORM_LABELS must cover DebateForm exactly; missing={missing!r} extra={extra!r}"
    )

# 回放 / wire 全员（= DebateForm 声明序）。产品入口广告子集见 DEBATE_SCHEMA_FORM_VALUES。
DEBATE_FORM_VALUES: tuple[str, ...] = tuple(m.value for m in DebateForm)

# 发给模型的 schema 广告子集。产品入口只认正反；扩 DebateForm 不自动扩本集。
DEBATE_SCHEMA_FORM_VALUES: tuple[str, ...] = (DebateForm.DEBATE.value,)
if not set(DEBATE_SCHEMA_FORM_VALUES).issubset(set(DEBATE_FORM_VALUES)):
    schema_extra = set(DEBATE_SCHEMA_FORM_VALUES) - set(DEBATE_FORM_VALUES)
    raise RuntimeError(
        "DEBATE_SCHEMA_FORM_VALUES must be a subset of DEBATE_FORM_VALUES; "
        f"extra={schema_extra!r}"
    )
