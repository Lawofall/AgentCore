"""辩论域共享常量（prompt / schema / events 共用，单一源）。"""

from __future__ import annotations

from agentcore.runtime.debate.types import DebateForm
from agentcore.runtime.runs.retrieval_budget import (
    DEFAULT_RETRIEVAL_BUDGET_DEBATER_WITH_DOSSIER,
)

DEBATE_OUTPUT_LIMIT = 16000

# 真纯丙·H4：已退役 DEBATER_TOOLS / WITNESS_TOOLS 系统只读窄名单。
# 辩手/证人默认与全开工具面一致（仍受写盘授权 / write_scope）；只读纪律靠角色提示自觉。

# 无 pack / pack 非 full 时：发言期对称有界检索（复用约定文档残搜槽位，不平行发明第二套）。
# 调查员舰队已删；此常量只驱动辩手 debater_retrieval_budgets。
BOUNDED_GAP_FILL_RETRIEVAL_BUDGET = DEFAULT_RETRIEVAL_BUDGET_DEBATER_WITH_DOSSIER

# 质询作答：逐条须写完（表态 + 论据），禁止在冒号 / 列举 /「理由是」处截断。
# 仅质询成稿 brief（:func:`~agentcore.runtime.debate.prompt.cx_draft_brief`）注入；
# 装配端另有尾部悬垂检测 + 一次自动续写补全。字数课不进出厂。
CX_LENGTH_HINT = (
    "质询作答须【逐条写完】：每条必须以完整句子收束，"
    "禁止在冒号、未闭合列表或「理由是 / 如下 / 包括」处截断停笔。"
)

# 展示名：键必须穷尽 DebateForm（权威成员集）；漏键 / 多键在 import 时炸。
FORM_LABELS: dict[DebateForm, str] = {
    DebateForm.DEBATE: "正反辩论",
}
if set(FORM_LABELS) != set(DebateForm):
    missing = set(DebateForm) - set(FORM_LABELS)
    extra = set(FORM_LABELS) - set(DebateForm)
    raise RuntimeError(
        f"FORM_LABELS must cover DebateForm exactly; missing={missing!r} extra={extra!r}"
    )

# 产品入口与 wire 同集（仅正反）。
DEBATE_FORM_VALUES: tuple[str, ...] = tuple(m.value for m in DebateForm)
DEBATE_SCHEMA_FORM_VALUES: tuple[str, ...] = (DebateForm.DEBATE.value,)
if set(DEBATE_SCHEMA_FORM_VALUES) != set(DEBATE_FORM_VALUES):
    raise RuntimeError(
        "DEBATE_SCHEMA_FORM_VALUES must equal DEBATE_FORM_VALUES; "
        f"schema={DEBATE_SCHEMA_FORM_VALUES!r} wire={DEBATE_FORM_VALUES!r}"
    )
