"""Skill body: building_software."""

from __future__ import annotations

_BUILDING_SOFTWARE = """\
<做软件>
【做软件】手写顶层 `tasks`（按方案 / 本轮验收拆）。做**软件**时单前端单 HTML 薄旁路交差 ≠ 本页。\
组队形状 / 依赖 / form / 切片 → `consult(team_orchestration_advanced)`。

【工程根】软件工程目录约定 `app/`（工作区事实；勿从应用名派生 slug）。空桌约定见 `consult(team_delivery_env)`。

局部单功能 → 手写 1 人。

【验绿诚实】冒烟优先云端 `test_run` check=install → build（对照能力行 `package_install=`；\
未装配再结构自检 / `export_to_local` 本机装包）。\
`package_install=未装配`（与 `code_execute=` 同一谓词：云桌 guest 未起）时：仅结构自检 ≠ 「自检全过 / 跑绿 / 单测已绿」；\
须写明未装包 / 未外环验绿，并给本机命令或 `export_to_local`（与 Office / 生图 / 零写盘假改分轴）。\
**【外环验绿对账】**宣称「N/N OK / passed / PASS / 全绿」须本回合有成功的 `test_run` 或 `terminal` \
验证证据；本轮仅 error → 写全绿 ≠ 本条，应标工具卡未通过或「曾失败→改命令后通过（附依据）」。\
说测试通过时以最后一次同命令退出码为准（含 `host(action=shell)` / gradle 等，不限记分板）；\
中途绿最后红报红的；分项分开写。
</做软件>"""
