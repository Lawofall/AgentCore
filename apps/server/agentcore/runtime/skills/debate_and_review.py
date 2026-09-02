"""Skill body: debate_and_review (+ courtroom trigger constants)."""

from __future__ import annotations

from typing import Final

# 点名终局对抗触发词（与辩论入口同源，禁止另抄字面量）。
MULTI_LENS_COURTROOM_TRIGGERS: Final[tuple[str, ...]] = (
    "模拟法庭",
    "庭审对抗",
    "对簿公堂",
)
_COURTROOM = "/".join(MULTI_LENS_COURTROOM_TRIGGERS)

_DEBATE_AND_REVIEW = """\
<正反辩论>
【入口】用户点名开辩 / 模拟庭审 / 终局对抗（含【""" + _COURTROOM + """】等）→ 直调 \
`debate`（正反）。未点名 → 不调 `debate`。挑刺 / 压测走 `delegate` 审校岗；多视角对比走 `delegate`。\
取证由辩论机制保证（约定文档桥 / Evidence Pack / 发言期台账），非开工前先拦调研。\
摸清 / 成文编制 → `consult(team_orchestration_advanced)`。

`debate` 是主持人驱动的正反交锋，交回【决策简报 + 交锋叙事线】（非终结）。\
`delegate` 是各方独立产出由你综合；无对立面 / 单点事实勿用。

开辩即正反：`form=debate`，sides=2。你只定 `motion` + `sides`；轮数主持人自调。\
`thorough=false` 仅用户只要轻量看看。

sides：`key` 英文短词；`name` 对称立场名，勿塞模型名（走 `model`）。

【多模型】用户点名双方模型 → 各方 `model` 填可读提及或目录身份（「平台 glm-5.2」/「DeepSeek」/\
`@platform/…` / `@byok/…`）。禁止把未加 `@` 的路由键（`platform/xxx`）写入 `model`。\
消歧失败时工具回执列出目录身份，抄那一条。禁止再 `ask_user` 元问题。只说跨模型未点名 → \
`cross_model=true` 且 `model` 留空（runtime 用 `PLATFORM_MODELS` 默认对阵）。留空且无 \
`cross_model` = 同模型场。点名裁判 → `moderator_model`；未点名 = 系统默认（可与辩手同模）。

stance：每方一句立场倾向（硬上限 80 字；非单句 / 含论证展开会拒绝调用）。\
正例：「支持一审判决正确」。客观事实归 `background`；「核心论点包括…系统论证」是辩手工作产出，\
论点清单勿写进 stance。

background（可选）：具体案件 / 真实事件建议传入已核实客观事实，每条带来源与日期；\
未决 / 推断勿当既定。纯价值观命题不必传。

开辩前：忠于用户点名的对立极，不得砍掉或偷换一极。关键指代模糊先澄清，\
用 `ask_user` 确认，勿自挑一解开辩。

【入口冲突】仅当会话已有调研产物且用户点名开辩：以开辩为准并说明一句 ≠ 跳过调研。

【收尾】结论在辩论室，不要重写简报。用一两句点出需用户拍板的价值之争，适合 `ask_user`。\
别抹平证据状态：简报里【待核实】/ 二手来源转述须保留核实状态，不得升格为既定事实。\
原样传达裁决的置信度 / 保留意见 / 反转条件。不引入场外量化估算。禁止编造未出现的赛况。
</正反辩论>"""
