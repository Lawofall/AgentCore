"""Skill bodies: build_website + build_app."""

from __future__ import annotations

from agentcore.runtime.runs.playbooks import PLAYBOOKS

_BUILD_WEBSITE_PLAYBOOK = PLAYBOOKS["build_website"]
_BUILD_APP_PLAYBOOK = PLAYBOOKS["build_app"]

_BUILD_WEBSITE = f"""\
<build_website>
【推荐】建站 / 落地页 / 营销官网用 `delegate(playbook="build_website", playbook_args={{...}})`\
（质量管线更稳；手写 / `none` 仍可用，但不走本 playbook 流水线）。\
控制台 / 后台 / 工具台 dense【推荐】同用本 playbook，另加 `playbook_args.style="toolshed"`\
（tool_dense pack + 禁营销皮）；【禁止】再找已删除的独立 `build_toolshed` playbook。

【交付档 → intensity】结构槽（非意图分类器）：`intensity=solo|standard`。\
一页先上线 → `intensity=solo`（一人整页）；品牌站流水线 → `intensity=standard`（文案→前端→QA 三串）；\
工具壳 → `style=toolshed` + intensity 按页复杂度（一页壳 solo / 多分区壳 standard）。\
已确认「一页 / 先上」→ **禁止**默升 standard 满串；糊说「做个网站」→ 先短问形态+桌上档，禁静默满编。

形状：{_BUILD_WEBSITE_PLAYBOOK.summary}
槽位：{_BUILD_WEBSITE_PLAYBOOK.slots}

开工顺序：
1. 关键未齐（类型/受众/风格等）或用户只说「做个网站」→ 可 `ask_user` **短问**一句：\
形态（展示页 / 工具壳 / 业务应用）+ 本轮桌上档（一页先上线 / 品牌站流水线 / 工具壳…）；\
`label` 只写桌上结果、勿写编制。默认风格可由机制写入 DESIGN。\
**勿先** consult 本 skill 再问。业务应用勿硬套本 playbook——改走 `build_app` / 轻切片。
2. **规格已齐**（用户已点名风格/站点类型/交付档等）→ **直接** \
`delegate(playbook="build_website", playbook_args={{"topic": "…", "intensity": "…"}})`，\
**勿先** consult；**必填** `playbook_args.topic`（站点/落地页一句话简述，取用户已给事实；\
产物目录固定 `site/`，不是文件夹槽），按桌上档填 `intensity`；\
【禁止】空 `playbook_args` / 漏 topic；【禁止】自拟视觉施工图（配色 / 动效 / 板块清单交给 playbook）。\
槽位拿不准再查本 skill。
3. 短问澄清后：若尚未读过本指引再 `consult(build_website)`，然后调 `delegate`：\
`playbook="build_website"` + **必填** `playbook_args.topic` + 对应 `intensity`；其余规则同上。
4. 控制台 / 工具台 dense：`playbook_args.style="toolshed"`；可选 `sections` / `stack` / `audience`——\
**只传事实输入**；强制 catalog pack `tool_dense` + anti-slop `domain=tool`；\
【禁止】套营销 hero / pricing 皮。省略 style（或 `marketing`）= 营销/落地页。
5. playbook：`solo`=一人整页；`standard`=文案 → 前端（一人包 DESIGN.md + 整页 HTML/CSS/JS + 轻量 CONTRACT）→ 独立 QA；\
含 `web_quality_scan` / DESIGN 风格 id 质量契约 / catalog / visual critic；\
`sections` 仅覆盖清单，不扇出分区节点。\
【划界】单页 / 落地页 = 一人整页（宜 solo）；**多屏 UI / 单文件大原型**勿套本「一人整页」口径——\
走 MVP 切片（见主提示「立刻派 ≠ 立刻全量」），勿扩本 playbook 语义。

组队进阶旋钮（协调墙 / deliverable 等）见 `consult(team_orchestration_advanced)`。
</build_website>"""

_BUILD_APP = f"""\
<build_app>
【准入】仅真 SPA / 用户明示「完整可跑 / 从 0 搭完整项目」/ 点选「模块流水线一次做完」\
→ 可进本 playbook；满档须 `intensity=full` + 显式 `modules`。\
方向已定但本轮边界未钉（讨论形态 / 先 MVP）→ **禁止**首派本形状满编（五波脚手架不当讨论落点）；\
改首派轻切片（宜 `intensity=lean`）、手写少节点，或单 lead 嵌套再拆，再 `replan`。\
局部单功能 → 手写或可选 `build_feature`。

【交付档 → intensity】结构槽（非意图分类器）：`intensity=lean|full`。\
MVP 主流程可点 → `intensity=lean`；模块流水线一次做完 → `intensity=full` + **显式** `modules`；\
只改一处 → **勿**进本 playbook，改 `build_feature` / 手写 / `repair_code`。\
已确认 MVP / 「先…以后再说」→ **禁止**默认 `intensity=full` 或多 `modules` 满编。

【推荐】绿场软件 / SPA 完整交付（Vue·React·Vite·SPA / 数据看板等）用 \
`delegate(playbook="build_app", playbook_args={{...}})`\
（scaffold-first 多波更稳；手写 / `none` 仍可用，**不硬拒**）。\
营销落地页 / 官网改用 `build_website`；控制台 dense 改用 `build_website` + `style=toolshed`。

形状：{_BUILD_APP_PLAYBOOK.summary}
槽位：{_BUILD_APP_PLAYBOOK.slots}

开工顺序：
1. 关键未齐（栈 / 模块范围 / 交付形态 / 桌上档）→ 可 `ask_user` 短问（技术栈与交付档），\
或写明默认后直接派。`label` 只写桌上结果。**勿先** consult 本 skill 再问。
2. **规格已齐且已准入** → **直接** `delegate(playbook="build_app", …)`，`playbook_args.app` 填应用简述；\
按桌上档填 `intensity`；可选 `modules` / `stack`（默认 Vue3+Vite+TS）/ `root`。\
`lean` 默认单主流程；要多模块满编须用户点选「模块流水线」并显式传 `modules`（超限会折叠，勿一次铺满）。
3. **进入本 playbook 后**：`full` 五阶段不可跳（scaffold → shared → N×module → integrate → smoke）；\
`lean` 为瘦启动（少节点主流程可点）。禁单 worker 包整站；router/入口引用的页面须同波创建（可 stub）。\
五阶段纪律只约束 `full` 形状内部，不强迫一切绿场进本 playbook。
4. 批次会自动扫 `.ts/.tsx/.vue` import 图（`graph_consistent`）；冒烟优先云端 \
`test_run` check=install → build（对照能力行 `package_install=`；未装配再结构自检 / `export_to_local` 本机装包）。\
`package_install=未装配`（云端能跑代码 ≠ 能装依赖）时：【禁止】把仅结构自检说成「自检全过 / 跑绿 / 单测已绿」；\
须写明未装包 / 未外环验绿，并给本机命令或 `export_to_local`（与 Office / 生图 / 零写盘假改分轴）。\
**【外环验绿对账】**宣称「N/N OK / passed / PASS / 全绿」须本回合有成功的 `test_run` 或 `terminal` \
验证证据；本轮仅 error → 【禁止】写全绿，应标工具卡未通过或「曾失败→改命令后通过（附依据）」。\
说测试通过时以最后一次同命令退出码为准（含 `host(action=shell)` / gradle 等，不限记分板）；\
中途绿最后红报红的；分项分开写。

组队进阶旋钮见 `consult(team_orchestration_advanced)`。
</build_app>"""
