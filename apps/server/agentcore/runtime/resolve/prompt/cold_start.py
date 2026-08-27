"""Cold-start explore + folder-profile soft/hard hint fragments."""

# Injected when the conversation has a folder and auto-explore gate fires.
# Three hard reasons share one principle body; only reason_line names the trigger.
# Chitchat exclusion is model-judged per this text.
_COLD_START_EXPLORE_HINT_TEMPLATE = """
<cold_start_explore>
【冷启动探索幕】{reason_line}
实质请求（读仓/改仓/调研/交付/怎么跑等与本文件夹相关）：先轻探只定位入口（0～1 轮，禁止自己摸完整仓）→ `delegate` 调研建档（≥2 角并行，禁止 1 人包办；同其它委派直接开跑）→ `update_folder_profile` 后立刻继续原请求。\
纯问候/致谢/无关闲聊 → 不开幕。用户点名「先了解 / 探索 / 重新了解 / 刷新文件夹记忆」且无其它任务 → 开幕，可停在建档说明。\
工作区空 → 说明空仓并引导绑仓或 `ask_user`，勿写假画像。仓非空不可跳过本幕。与巩固侧「冷启动」无关。\
调研 worker 只调查回报；画像由你收尾。禁止用 `remember` 把文件夹简报写成用户规则。探索 pending 期间 worker 写盘不得出 AgentCore/ 约定记忆与探索笔记（本回合 create_folder 新建的云文件夹除外）。
</cold_start_explore>"""

_COLD_START_EXPLORE_REASON_EMPTY = "当前文件夹约定记忆「画像.md」为空。"
_COLD_START_EXPLORE_REASON_REBIND = (
    "当前文件夹的工作区绑定已变，旧简报可能不准；合并更新，勿整篇清空。"
)
_COLD_START_EXPLORE_REASON_REFRESH = (
    "用户点名刷新文件夹记忆（画像已有内容，合并更新）。"
)

_COLD_START_EXPLORE_HINT_EMPTY = _COLD_START_EXPLORE_HINT_TEMPLATE.format(
    reason_line=_COLD_START_EXPLORE_REASON_EMPTY
)
_COLD_START_EXPLORE_HINT_REBIND = _COLD_START_EXPLORE_HINT_TEMPLATE.format(
    reason_line=_COLD_START_EXPLORE_REASON_REBIND
)
_COLD_START_EXPLORE_HINT_REFRESH = _COLD_START_EXPLORE_HINT_TEMPLATE.format(
    reason_line=_COLD_START_EXPLORE_REASON_REFRESH
)


# Soft empty-profile hint — never enter <cold_start_explore> / never set explore-pending.
_FOLDER_PROFILE_EMPTY_SOFT_HINT = """
<folder_profile_empty>
【文件夹画像提示】当前文件夹约定记忆「画像.md」仍为空。本回合**不挡**原请求与委派；纯闲聊不必开幕。\
硬冷启动块出现且仓非空时仍须探路建档，本软提示不可当跳过依据。
</folder_profile_empty>"""


# R2 soft hint only — never enter <cold_start_explore> / never set explore-pending.
_FOLDER_NAV_STALE_HINT = """
<folder_nav_stale>
【文件夹结构提示】工作区相对上次探索写入时已变化。当前回合继续用已有画像/导航，**不挡**原请求；\
若需刷新可点名「重新了解」或「刷新文件夹记忆」。
</folder_nav_stale>"""


def _explore_act_block(reason: str | None) -> str:
    if reason == "empty":
        return _COLD_START_EXPLORE_HINT_EMPTY.strip()
    if reason == "rebind":
        return _COLD_START_EXPLORE_HINT_REBIND.strip()
    if reason == "refresh":
        return _COLD_START_EXPLORE_HINT_REFRESH.strip()
    return ""
