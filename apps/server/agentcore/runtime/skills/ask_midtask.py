"""Skill body: ask_midtask (途中提问)."""

from __future__ import annotations

_ASK_MIDTASK = """\
<途中提问>
执行途中拍板：方案 A/B、不可逆删除/覆盖、范围明显超出须重新授权 → ask_user。\
问句写 `questions[].prompt`。途中关键岔路通常不预填 `default`。\
有倾向时按开场提问【字段】。问句不要在正文再抄；假设和背景写正文。规格已齐或用户要拿到的结果已在卡上结算 → 立刻派。

【落盘前对齐】你已承诺落盘前对齐，或用户点名「确认后再存 / 先对齐再写」→ 阻塞向用户发问，\
`default`=「按当前设计落盘」（仅认本回合明示）。

途中改点载体且现有能力做不到 → 坚持则按用户所选继续；明显次优 → 标假设继续。

辩论收场要在对立结论间取舍 → `ask_user` 给出「采纳正方 / 采纳反方 / 都要 / 补充论证」。

【方案挑选 / 风险勾选】发散挑选或审查勾选（多选 choice）走普通 `ask_user`（权衡写进 `label`）；\
挑中后 `continue_from_run_id` 唤回、勾选修订。\
主拍板每任务恰好一次；明文提纲拆波同此。

整理方案用 `card="organize_plan"` → 确认后 `file_batch(organize_plan_id=…)`。
</途中提问>"""
