"""Skill body: build_app."""

from __future__ import annotations

from agentcore.runtime.runs.playbooks import PLAYBOOKS

_BUILD_APP_PLAYBOOK = PLAYBOOKS["build_app"]

_BUILD_APP = f"""\
<build_app>
【准入】仅真 SPA / 用户明示「完整可跑 / 从 0 搭完整项目」/ 点选「模块流水线一次做完」\
→ 可进本 playbook；满档须 `intensity=full` + 显式 `modules`。\
方向已定但本轮边界未钉（讨论形态 / 先 MVP）→ **禁止**首派本形状满编（五波脚手架不当讨论落点）；\
改首派轻切片（宜 `intensity=lean`）、手写少节点，或单 lead 嵌套再拆，再 `replan`。\
局部单功能 → 手写或 `diagnose_fix_verify`。\
绿场仍走 `intensity` / playbook，**勿**改成临时交成果组长。\
做**软件**时**【禁止】**单前端单 HTML 薄旁路交差。

【交付档 → intensity】结构槽（非意图分类器）：`intensity=lean|full`。\
MVP 主流程可点 → `intensity=lean`；模块流水线一次做完 → `intensity=full` + **显式** `modules`；\
只改一处 → **勿**进本 playbook，改手写 / `diagnose_fix_verify`。\
已确认 MVP / 「先…以后再说」→ **禁止**默认 `intensity=full` 或多 `modules` 满编。

【推荐】绿场软件 / SPA 完整交付（Vue·React·Vite·SPA / 数据看板等）用 \
`delegate(playbook="build_app", playbook_args={{...}})`\
（scaffold-first 多波更稳；手写可用）。\
营销落地页 / 官网 / 控制台【勿】进本 playbook。单页一人做完；控制台别套营销皮；\
HTML 落盘即自动静态质检。

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
`package_install=未装配`（与 `code_execute=` 同一谓词：云桌 guest 未起）时：【禁止】把仅结构自检说成「自检全过 / 跑绿 / 单测已绿」；\
须写明未装包 / 未外环验绿，并给本机命令或 `export_to_local`（与 Office / 生图 / 零写盘假改分轴）。\
**【外环验绿对账】**宣称「N/N OK / passed / PASS / 全绿」须本回合有成功的 `test_run` 或 `terminal` \
验证证据；本轮仅 error → 【禁止】写全绿，应标工具卡未通过或「曾失败→改命令后通过（附依据）」。\
说测试通过时以最后一次同命令退出码为准（含 `host(action=shell)` / gradle 等，不限记分板）；\
中途绿最后红报红的；分项分开写。

组队进阶旋钮见 `consult(team_orchestration_advanced)`。
</build_app>"""
