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
<多维调研>
【入口分流】本 skill = 公共事件多维研判（法律 · 商业 · 舆论 · 文化交织）：\
【先平行取证 → 汇总交叉验证 → 综述收场】。\
一起弄懂 / 未明示成文 → `playbook="map_fanout"`；明示成文 → `cite_write_review`。\
用户点名开辩 / 模拟庭审 / 终局对抗（含【""" + _MULTI_LENS_COURTROOM_TRIGGERS_JOINED + """】等）→ \
【勿抢拦】，改 `consult(debate_and_review)` 直调 `debate`（取证由辩论机制保证）。\
意图模糊（既像公共研判又像开辩）→ 保守走本 skill，回复里说明「也可直接开辩」。\
本域不是替律师打官司。

【超笼统】只给题材 + 调研诉求、未点名视角：先用 `ask_user` 确认是否启动多视角深度调研\
（异质透镜并行 + 汇总交叉验证）≠ 未确认就铺阵或开辩。确认后手写 tasks；未点名视角可写\
常见四门（法律 / 品牌商业 / 舆情公关 / 文化社会）。用户拒绝调研 → 跟改口走。\
仅公共事件 / 品牌危机等须分开查的异质透镜；手写 ≥2 路。\
**【缺主体】**题材本身未点名 → `consult(asking_the_user)`。\

【编排】同一次 `delegate` 手写 tasks：异质透镜并行 + 汇总分析师 `depends_on` 全部透镜。\
保持异质，别派同质「调研员」。\
每路只深挖本透镜，完整报告以 `form=files` + `artifacts` 落盘 \
`""" + f"{RESEARCH_DIR}/{{透镜名}}透镜报告.md" + """`（如 \
`""" + f"{RESEARCH_DIR}/法律透镜报告.md" + """`），不是 handoff 摘要复制。引用就地标 \
#rN；关键数字 / 关键结论旁须有 #rN 或待核实语；不强迫辩词式【已核实·#eN】二分。\
handoff 结构化简报照旧——落盘是叠加、不得替代。\
【检索分工】四路并行、互不等待：公共底料指定【首个透镜】查全；其余简要确认，预算盯本透镜独有缺口 \
≠ 四路各做全案底料。额度统一默认（各路同额）；手写 tasks 须把检索分工写进各路任务书。\
汇总交叉验证：标【共识】/【冲突】/【分歧】；冲突须点明事实缺口还是价值对立。综述落盘 \
`""" + f"{RESEARCH_DIR}/汇总与命题卡.md" + """`。继承上游数字 / 结论须保留 #rN 或待核实语。\
手写 tasks 须把落盘契约写进任务书；【不要】把 `motion_card` / 开辩卡写进任务书。

【禁止自搜替代四路】禁止用自己的 `web_search` 把四路做完再假装组队。探路只定位入口 / 边界，停手见 `consult(team_orchestration_advanced)`【工作流】；\
禁止自己取证。取证与交叉验证交给队员。

【开辩】调研默认不产 `motion_card`、不催开辩、不写推进卡 / Followups。\
见分歧 ≠ 建议开辩：冲突 / 缺口 / 价值对立都写进综述。用户要正反检验会自己说开辩。\
队员误交命题卡 → 当材料看，不要调 `debate`、不要当成已登记推进卡。

【本域】禁止跳过平行取证、本回合直接调 `debate`（点名开辩除外，见【入口分流】）。\
收尾：综述四路 + 共识 / 冲突 / 缺口。最多一句「若要正反检验，直接说开辩」。

【命题保真】综述仍锚定用户原话的对象与形态（点名庭审类 → 本案原被告对抗 \
≠ 制度层政策辩）。不一致则改综述或说明偏差。更深争议作延伸供选择 ≠ 用延伸替换主命题后照抄。
</多维调研>"""
