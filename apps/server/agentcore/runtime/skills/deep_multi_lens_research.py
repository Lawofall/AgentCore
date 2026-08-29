"""Skill body: deep_multi_lens_research (+ courtroom trigger constants)."""

from __future__ import annotations

from typing import Final

from agentcore.workspace.stage_dirs import RESEARCH_DIR

# 多维取证类终局对抗触发词（MLR / debate 入口分流句同源，禁止另抄字面量）。
MULTI_LENS_COURTROOM_TRIGGERS: Final[tuple[str, ...]] = (
    "模拟法庭",
    "庭审对抗",
    "对簿公堂",
)
_MULTI_LENS_COURTROOM_TRIGGERS_JOINED = "/".join(MULTI_LENS_COURTROOM_TRIGGERS)

_DEEP_MULTI_LENS_RESEARCH = """\
<deep_multi_lens_research>
【入口分流】本 skill = 公共事件多维研判（法律 · 商业 · 舆论 · 文化交织）：\
【先平行取证 → 汇总交叉验证 → 必要时产命题卡 → 用户批准后再辩】。\
一起弄懂 / 未明示成文 → `playbook="map_fanout"`；明示成文 → `cite_write_review`。\
用户点名开辩 / 模拟庭审 / 终局对抗（含【""" + _MULTI_LENS_COURTROOM_TRIGGERS_JOINED + """】等）→ \
【勿抢拦】，改 `consult(debate_and_review)` 直调 `debate`（取证由辩论机制保证）。\
意图模糊（既像公共研判又像开辩）→ 保守走本 skill，回复里说明「也可直接开辩」。\
本域不是替律师打官司。

【超笼统】只给题材 + 调研诉求、未点名视角：先用 `ask_user` 确认是否启动多视角深度调研\
（异质透镜并行 + 汇总交叉验证）≠ 未确认就铺阵或开辩。确认后默认手写 tasks；未点名视角可写\
常见四门（法律 / 品牌商业 / 舆情公关 / 文化社会）。用户拒绝调研 → 跟改口走，勿强行挂 playbook。\
走 `playbook="lens_crosscheck"` 时须填 `lenses`（≥2 个异质透镜名）；仅公共事件 / 品牌危机等\
须分开查的异质透镜。\
**【缺主体】**题材本身未点名 → `consult(ask_user_kickoff)`。\

【编排】同一次 `delegate`：异质透镜并行 + 汇总分析师 `depends_on` 全部透镜。透镜名写入 \
`lenses`（手写同）；保持异质，别派同质「调研员」。默认手写 tasks。\
每路只深挖本透镜，完整报告以 `form=files` + `artifacts` 落盘 \
`""" + f"{RESEARCH_DIR}/{{透镜名}}透镜报告.md" + """`（如 \
`""" + f"{RESEARCH_DIR}/法律透镜报告.md" + """`），不是 handoff 摘要复制。引用就地标 \
#rN；关键数字 / 关键结论旁须有 #rN 或待核实语；不强迫辩词式【已核实·#eN】二分。\
handoff 结构化简报照旧——落盘是叠加、不得替代。\
【检索分工】四路并行、互不等待：公共底料指定【首个透镜】查全；其余简要确认，预算盯本透镜独有缺口 \
≠ 四路各做全案底料。额度统一默认（各路同额）；手写 tasks 须把检索分工写进各路任务书（playbook 已内嵌）。\
汇总交叉验证：标【共识】/【冲突】/【分歧】；冲突须点明事实缺口还是价值对立。综述落盘 \
`""" + f"{RESEARCH_DIR}/汇总与命题卡.md" + """`。继承上游数字 / 结论须保留 #rN 或待核实语。\
`motion_card` 走 handoff 字段，落盘不替代该对象。手写 tasks 须把命题卡纪律与 \
`""" + f"{RESEARCH_DIR}/" + """` 落盘契约写进任务书。

【禁止自搜替代四路】禁止用自己的 `web_search` 把四路做完再假装组队。探路只定位入口 / 边界，停手见 `consult(team_orchestration_advanced)`【工作流】；\
禁止自己取证。取证与交叉验证交给队员。

【motion_card】真对立轴（价值对立或主张相互否证、继续取证消解不了）须在 \
`handoff.motion_card` 产卡 ≠ 只在正文写「建议开辩」。见分歧 ≠ 建议开辩：仅事实缺口 → \
补派或写进缺口、不要产卡；仅并列观点、无真对立轴 → 对比综述（不出辩题）。\
字段：`motion`；`sides`（≥2，`stance` 一句话结论倾向，形状同 `debate_and_review`）；\
`fact_pointers`（可空列表但须显式给出）；`rationale` 必须论证继续调研 / 再派透镜解决不了、\
需要对抗检验；`form` 可选、默认 `debate`。\
汇总员任务书须点名 `handoff.motion_card`【对象字段】。禁止只写「给出命题卡 / Followups 芯片」\
却不点名该字段——markdown 表不能代替结构化卡。阶段推进卡由系统据卡登记。禁止自制 markdown \
命题表冒充已有命题卡。

【本域】禁止跳过平行取证、本回合直接调 `debate`。收到命题卡后：收尾呈报命题 / 薄立场 / \
为何必须对抗；系统登记阶段推进卡供一键开辩——勿口头征求开辩同意，本回合不要自行调用 \
`debate`。无卡则综述四路 + 共识 / 冲突 / 缺口。

【命题保真】呈报前校验 motion 仍锚定用户原话的对象与形态（点名庭审类 → 本案原被告对抗 \
≠ 制度层政策辩）。不一致则重产卡或说明偏差。更深争议作延伸辩题供选择 ≠ 用延伸辩题替换主命题后照抄呈报。

【幕 2】用户批准开辩后（见 `debate_and_review`）：简报形状是辩论交回之后的收尾，不是辩论的替代。\
本会话尚无完赛 `debate` → 必须先真辩完赛（先辩后报）≠ 因「终稿要跨维度决策简报」跳过 `debate`。\
已有完赛产物且用户只要终稿 → 已辩复用，可不重开辩。终稿做成跨维度决策简报（分维小标题覆盖实际透镜）；\
禁止改写成辩论收报 / 正反拍板综述。赛况忠实：终审 / 交锋 / 证据状态原样引用，禁止编造；\
【待核实】与保留意见不得抹平。
</deep_multi_lens_research>"""
