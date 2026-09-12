"""Skill body: staffing.

Scene WHEN lives in the catalog summary (L1). 自包含对比边界在
``delegate.task`` 参数；填约束 / 路径 / 凭据 HOW 在本 consult，不进常驻核。
何时用 ``delegate`` 写在工具 description；图语义 / 满编套餐在 schema 与
playbook summary。
"""

from __future__ import annotations

# Shared with lead_subteam (same task field, different consult audience).
TASK_FILL_HOW = (
    "写 task：已拍板约束同一行「已确认约束：①…」，没有则「（无）」；"
    "未拍板的标假设，改法与现状不进该行。"
    "凭据写入供填 env。未装配能力 ≠ 写入。"
    "点名入口或成品路径用工作区相对 POSIX。"
)

_STAFFING = f"""\
<团队拆法>
先摸清入口就停：只定位入口（看目录、按名找）≠ 打开正文收集结论。\
已点名的少量路径自己读；不知读哪 / 连搜收齐 = 成规模取证，立刻派，task 写清了解到什么算够。\
能点名入口即可写目标·约束·验收，不必先自己摸完。

按活的结构组队，人数不是优化目标。1 人只在活本身是一块。\
task = 目标·约束·验收。{TASK_FILL_HOW}\
「先组队 / 你可以组队」≠ 已经拆好团队。\
根侧多节点 DAG 与单 lead 嵌套同一摊二选一：交了 lead ≠ 再平铺同名角色。\
收口后再动同一支团队：只写还要干的人，点名上一批 run_id 接着干或补缺口 ≠ 把上一批整表再交一遍。

【成品文件只装成品】用户要拿去提交的文件只装正文；核对提醒写在你的回复里 ≠ 写进交付文件。

约束归你、专业方案归专家。验收项与工人小标题同一套原文。

审查默认只报告、不落盘；审查者 ≠ 作者。只报告的活默认 1 人。
点名开辩 → `debate`。
</团队拆法>"""
