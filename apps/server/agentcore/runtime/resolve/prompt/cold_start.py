"""Cold-start explore + folder-profile soft/hard hint fragments."""

from agentcore.config import settings

# 探路轮上限唯一真源：settings.engine_team_gate_investigation_rounds（局部拼接，避整段 f-string）。
_IR = str(settings.engine_team_gate_investigation_rounds)

# Injected when the conversation has a folder and auto-explore gate fires.
# Chitchat exclusion is model-judged per this text.
_COLD_START_EXPLORE_HINT_EMPTY = (
    """
<cold_start_explore>
【冷启动探索幕】当前文件夹约定记忆「画像.md」为空。
若用户本条是实质请求（读仓/改仓/调研/交付物/怎么跑等与这个文件夹相关）→ 本回合必须先开探索幕：\
轻量探路（≤"""
    + _IR
    + """ **轮**）写清任务书 → `delegate` 组调研队（**≥2 角并行**，例：目录/入口 vs \
设计·约定文档；走 team_preview / full_auto 同其它委派；**禁止** 1 人包办整仓）→ \
收齐后调用 `update_folder_profile` 写入文件夹画像与短入口导航（大仓且子系统≥2 可臃肿时才带 topics）→ \
**立刻继续处理用户原请求**（直答或再 delegate；禁止「已建档，需要我继续吗」类收尾）。\
纯问候/致谢/与这个文件夹无关的闲聊 → 不要自动开幕。\
用户点名「先了解 / 探索 / 重新了解 / 刷新文件夹记忆」且无其它任务 → 强制开幕，可停在简短建档说明。\
`<workspace_file_index>` 显示工作区为空 → 说明空仓并引导绑仓/列目录或立刻 `ask_user`；\
禁止空转扫仓小队、禁止写假画像、禁止为确认空连续 `file_list`。\
仓**非空**时本幕仍须轻探→`delegate`≥2 角建档，**不可跳过**。与巩固侧「冷启动」无关。\
调研 worker 只调查回报；仅你收尾写画像/导航/主题；禁止用 `remember` 把文件夹简报写成用户规则；\
禁止写用户仓根 AGENTS.md/docs；探索 pending 期间 worker 写盘不得出 AgentCore/ 约定记忆与探索笔记。
</cold_start_explore>"""
)


_COLD_START_EXPLORE_HINT_REBIND = (
    """
<cold_start_explore>
【冷启动探索幕 · 绑定已变】当前文件夹的工作区绑定相对上次写入画像时已变化，旧简报可能不准。
若用户本条是实质请求 → 本回合必须先开探索幕（合并更新，勿整篇清空）：轻量探路（≤"""
    + _IR
    + """ **轮**）→ \
`delegate` 组调研队（**≥2 角并行**；禁止 1 人包办）→ `update_folder_profile` 合并写画像与导航\
（可带 topics）→ **立刻继续原请求**。\
禁止「已建档，需要我继续吗」收尾。纯闲聊不自动开幕。\
用户点名「重新了解 / 刷新文件夹记忆 / 先了解」→ 强制开幕（合并）。\
空工作区不扫仓、不写假画像；禁止为确认空连续 `file_list`；禁止用 `remember` 写文件夹简报；\
探索 pending 期间 worker 写盘不得出 AgentCore/ 约定记忆与探索笔记；\
仓**非空**时本幕仍须轻探→`delegate`≥2 角建档，**不可跳过**。与巩固侧「冷启动」无关。
</cold_start_explore>"""
)


_COLD_START_EXPLORE_HINT_REFRESH = (
    """
<cold_start_explore>
【冷启动探索幕 · 用户点名刷新】用户点名要求重新了解 / 刷新文件夹记忆（画像已有内容，合并更新）。
本回合必须先开探索幕：轻量探路（≤"""
    + _IR
    + """ **轮**）→ `delegate` 组调研队（**≥2 角并行**；禁止 1 人包办）→ \
`update_folder_profile` 合并写画像与导航（可带 topics）→ \
有其它实质原请求则**立刻继续**；仅了解/刷新无其它任务时可停在简短说明。\
禁止「已建档，需要我继续吗」收尾；禁止用 `remember` 写文件夹简报；\
空工作区不扫仓、不写假画像、勿为确认空连续 `file_list`；探索 pending 期间 worker 写盘不得出 AgentCore/ 约定记忆与探索笔记；\
勿写用户仓根 AGENTS.md/docs；厚背景资料写成按需主题条目（`update_folder_profile` 的 topics），不落盘；\
仓**非空**时本幕仍须轻探→`delegate`≥2 角建档，**不可跳过**。与巩固侧「冷启动」无关。
</cold_start_explore>"""
)


# Soft empty-profile hint — never enter <cold_start_explore> / never set explore-pending.
_FOLDER_PROFILE_EMPTY_SOFT_HINT = """
<folder_profile_empty>
【文件夹画像提示】当前文件夹约定记忆「画像.md」仍为空。本回合**不挡**原请求与委派；\
可择机轻量了解并写画像，纯闲聊不必开幕。用户点名了解/继续在这个文件夹里开发时再走正式探索幕。\
索引已空或明显近空 → 优先短问/`ask_user`，【禁止】为确认空连续 `file_list` 烧探路；\
硬冷启动块出现且仓非空时仍须探路→≥2 角建档，本软提示不可当跳过依据。
</folder_profile_empty>"""


# R2 soft hint only — never enter <cold_start_explore> / never set explore-pending.
_FOLDER_NAV_STALE_HINT = """
<folder_nav_stale>
【文件夹结构提示】工作区相对上次探索写入时已变化。当前回合继续用已有画像/导航，**不挡**原请求；\
若需刷新可点名「重新了解」或「刷新文件夹记忆」。
</folder_nav_stale>"""


_FOLDER_PROFILE_TOOL_HINT = """
【文件夹画像写入】探索幕收尾或用户点名了解/重新了解这个文件夹后，用 `update_folder_profile` 合并更新文件夹 \
`画像.md`，并建议同写短入口 `导航.md`；默认不拆主题；仅当≥2 可复用子系统且画像会臃肿时才传 \
topics（单次软顶 5，短 slug；超额截断）。\
写完后：有实质原请求 → 立刻继续；仅了解 → 可停。禁止用 `remember` 写文件夹简报。
"""


def _explore_act_block(reason: str | None) -> str:
    if reason == "empty":
        return _COLD_START_EXPLORE_HINT_EMPTY.strip()
    if reason == "rebind":
        return _COLD_START_EXPLORE_HINT_REBIND.strip()
    if reason == "refresh":
        return _COLD_START_EXPLORE_HINT_REFRESH.strip()
    return ""
