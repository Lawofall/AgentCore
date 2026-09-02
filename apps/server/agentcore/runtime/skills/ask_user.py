"""Skill bodies: ask_user_kickoff + ask_user_midtask."""

from __future__ import annotations

_ASK_USER_KICKOFF = """\
<开场提问>
字段 HOW：拿不准 `ask_user` 字段时查阅本条。

【短改稿】本条是短句原文释义 / 改词 / 改句 → 先复述改稿点再答或派改。缺信息靠短问。

【决策/澄清短问·default】决策或澄清类短问（含日程/范围/关键缺口，不限三路简报）→ \
有倾向时按【字段】（卡面**不预选**）。用户须点选或写补充才提交；\
派工跟勾选/人话走。仍建议填 `default`：空 continue 兼容回灌时派工用该 default 并标「按确认默认」。\
【继续·承接确认项】用户说「继续」且上轮已给出确认选项 / 缺口清单 → 正文【必须】至少复述\
那些选项（或卡上 default）。\
新建仓库 / 本地目录类短问 → `questions`【必须】预填可确认默认路径（`default`）。\
【缺主体短问】三路/多路调研未点名主体（产品/市场/事件/对象）→ **必须** `ask_user` 短问 ≠ \
静默自拟市场或产品占位后直接 `map_fanout`（含 `cite_write_review` \
的 topic——须来自用户已给或 ask 确认）。无勾选且无回灌不得把自拟主体当已确认。\
方向 / 方案 choice 的 `label` / `message` 写清**本轮交付边界**；权衡写进选项名，勿填 `detail`；\
选完仍立刻派，范围跟选项走 ≠ 暗示「选完即全仓开工」。

【交付档·桌上结果】交付类 choice：`label` **只写桌上结果**（几人几步 / 流水线角色等内部编制不进选项）。不映射编制套餐。\
糊说「做个网站」≠ 已钉形态（展示页 / 工具壳 / 业务应用）；\
建站用手写 `tasks`；糊则短问消歧。

【点名载体/手段】能力盖不住 → 短问：`message` **第一句**说清做不到什么，再给替代；用户坚持 → **零摩擦**按所选。\
明显次优 → 回复里标假设继续，不必先停。Word / Office 真图形盖不住 → `consult(team_delivery_env)`。\
说满 ≠ 空派。本钩只管载体·手段 ≠ 把形态短问扩成载体审讯。

【字段】普通 `ask_user`（**不填** `card`，除非途中专用卡）：
- `message`：必填。普通卡不展示为标题。
- `assumptions`：可选；`label` 写 2–6 字项名，详情放 `value`。
- `questions`：可选，最多 5；**问句写 `prompt`**（要什么 / 给谁 / 做到哪一档）；可填 `default`；有倾向时把该项放第一、名末加「（推荐）」或 (recommended)；卡面不预选。\
权衡写进 `label`，普通短问勿填 `detail`（`detail` 仅整理 / 复盘 card）。\
choice 只服务下一步动作 ≠ 把正文已摆出的候选菜单再投进卡里催收敛。
- 专用 `card`：仅 `organize_plan`（恰好 1 题多选）。

【软件 / 应用】交付形态不清时短问或写明你假设的形态。桌上结果已钉 → 立刻派那个结果。
</开场提问>"""

_ASK_USER_MIDTASK = """\
<途中提问>
执行途中拍板：方案 A/B、不可逆删除/覆盖、范围明显超出须重新授权 → ask_user。\
**问句写 `questions[].prompt`**。`message` 必填。途中关键岔路通常【不预填 default】。\
有倾向时按 kickoff【字段】。正文在发问前留空。规格已齐或卡已结算桌上结果 → 立刻派。

【落盘前对齐】你已承诺落盘前对齐，或用户点名「确认后再存 / 先对齐再写」→ 阻塞短问，\
`default`=「按当前设计落盘」（仅认本回合明示）。\
**【收尾·先报断点】**标「都实现了 / 已交付 / 收尾完成」前：先自报真实断点；有断点不得先报满口完成。

途中改点载体且盖不住 → 坚持则零摩擦；明显次优 → 标假设继续。

辩论收场要在对立结论间取舍 → `ask_user` 给出「采纳正方 / 采纳反方 / 都要 / 补充论证」。

【方案挑选 / 风险勾选】发散挑选或审查勾选（多选 choice）走普通 `ask_user`（权衡写进 `label`）；\
挑中后 `continue_from_run_id` 唤回、勾选修订 → `consult(team_orchestration_advanced)`。\
主拍板每任务恰好一次；明文提纲拆波同该 consult。

【区外目录授权】进桌 / 本机传统 → `consult(team_delivery_env)`。跨文件夹 → `consult(team_cross_folder)`。\
区外授权 HOW → `consult(external_mount_readonly)`。范围/手段 choice 不加 action；整题授权才挂 grant_*。\
整理方案用 `card="organize_plan"` → 确认后 `file_batch(organize_plan_id=…)`。
</途中提问>"""

# Catalog still registers a single asking_the_user skill.
_ASKING_THE_USER = _ASK_USER_KICKOFF + _ASK_USER_MIDTASK
