"""Skill body: debate_and_review."""

from __future__ import annotations

from agentcore.runtime.skills.deep_multi_lens_research import MULTI_LENS_COURTROOM_TRIGGERS

_COURTROOM = "/".join(MULTI_LENS_COURTROOM_TRIGGERS)

_DEBATE_AND_REVIEW = """\
<debate_and_review>
【入口分流】用户点名开辩 / 模拟庭审 / 终局对抗（含【""" + _COURTROOM + """】等）→ 本 skill，直调 \
`debate`。取证由辩论机制保证（约定文档桥 / Evidence Pack / 发言期台账），非开工前先拦调研，\
勿先拦 `deep_multi_lens_research`。公共事件跨域研判 → `consult(deep_multi_lens_research)`\
（MLR → 命题卡 → 推进卡）。一起弄懂、未明示成文 → `map_fanout`；明示成文 → `cite_write_review`。\
既像研判又像开辩（意图模糊）→ 保守走 MLR，回复里说明「也可直接开辩」。

`debate` 是主持人驱动的对抗性交锋，交回【决策简报 + 交锋叙事线】（非终结）。\
`delegate` 是各方独立产出由你综合；无对立面 / 单点事实勿用。

form：`debate` 正反 sides=2；`red_team` 被审方 `is_subject=true` + 红队（攻→应→复；\
`thorough=false` 时攻→应）；`roundtable` 3+ 视角。你只定 `motion` + `sides`；轮数主持人自调。\
`thorough=false` 仅用户只要轻量看看。

sides：`key` 英文短词；`name` 对称立场 / 视角名，勿塞模型名（走 `model`）。

【多模型】用户点名双方模型 → 各方 `model` 填可读提及或目录身份（「平台 glm-5.2」/「DeepSeek」/\
`@platform/…` / `@byok/…`）。禁止把未加 `@` 的路由键（`platform/xxx`）写入 `model`。\
消歧失败时工具回执列出目录身份，抄那一条。禁止再 `ask_user` 元问题。只说跨模型未点名 → \
`cross_model=true` 且 `model` 留空（runtime 用 `PLATFORM_MODELS` 默认对阵）。留空且无 \
`cross_model` = 同模型场。点名裁判 → `moderator_model`；未点名 = 系统默认（可与辩手同模）。\
红队 / 圆桌可不填 per-side。

stance：每方一句立场倾向（硬上限 80 字；非单句 / 含论证展开会拒绝调用）。\
正例：「支持一审判决正确」。客观事实归 `background`；「核心论点包括…系统论证」是辩手工作产出，\
预写会退化成剧本。论点清单勿写进 stance。

background（可选）：具体案件 / 真实事件建议传入已核实客观事实，每条带来源与日期。\
纯价值观命题不必传。「被告表示将上诉」≠「案件处于二审」。

开辩前：忠于用户点名的对立极，不得砍掉或偷换一极；可补没想到的视角。关键指代模糊先澄清，\
用 `ask_user` 确认，勿自挑一解开辩。

【形态冲突】仅当会话已有 `motion_card` / 调研产物且与用户点名形态冲突：纠正偷换，\
以用户点名形态为准并说明一句。本条非跳过调研通行证；需不需要先取证以【入口分流】为准。

【收尾分流】命题卡 / 底料源自 `deep_multi_lens_research`：本会话尚无完赛 `debate` 时不可因\
「要写跨维简报」跳过；完赛后终稿须按该 skill 幕 2 写成跨维度决策简报（总裁决 + 各透镜分节），\
禁止改写成辩论收报 / 正反拍板综述；禁止编造未出现的赛况、禁止未辩先写简报。\
其余辩论用自己的话收尾——先结论与建议，再点出需用户拍板的价值之争，适合 `ask_user`。\
别抹平证据状态：简报里【待核实】/ 二手来源转述须保留核实状态，不得升格为既定事实。\
原样传达裁决的置信度 / 保留意见 / 反转条件。不引入场外量化估算。
</debate_and_review>"""
