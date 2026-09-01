"""Cold-start explore + folder-profile soft/hard hint fragments."""

# Injected when the conversation has a folder and auto-explore gate fires.
# Three hard reasons share one principle body; only reason_line names the trigger.
# 「闲聊不开幕」 is model-judged from this text — not a code gate.
_COLD_START_EXPLORE_HINT_TEMPLATE = """
<冷启动探索>
【冷启动探索幕】{reason_line}
先轻探再 delegate 调研建档。闲聊不开幕。\
禁止用 `remember` 把文件夹简报写成用户规则。探索 pending 期间 worker 写盘不得出 AgentCore/ 约定记忆与探索笔记（本回合 create_folder 新建的云文件夹除外）。
</冷启动探索>"""

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


# Soft empty-profile hint — never enter <冷启动探索> / never set explore-pending.
# Fact + reconstruction 点名；不怂恿本回合自己写回。Named 先了解 / 工程短语走硬幕。
_FOLDER_PROFILE_EMPTY_SOFT_HINT = """
<文件夹画像空>
【文件夹画像提示】当前文件夹设定（画像）是空的。本回合**不挡**原请求与委派。要重建就点名「先了解」或「继续开发」。
</文件夹画像空>"""


# R2 soft hint only — never enter <冷启动探索> / never set explore-pending.
_FOLDER_NAV_STALE_HINT = """
<文件夹导航过期>
【文件夹结构提示】工作区相对上次探索写入时已变化。当前回合继续用已有画像/导航，**不挡**原请求；\
若需刷新可点名「重新了解」或「刷新文件夹记忆」。
</文件夹导航过期>"""


def _explore_act_block(reason: str | None) -> str:
    if reason == "empty":
        return _COLD_START_EXPLORE_HINT_EMPTY.strip()
    if reason == "rebind":
        return _COLD_START_EXPLORE_HINT_REBIND.strip()
    if reason == "refresh":
        return _COLD_START_EXPLORE_HINT_REFRESH.strip()
    return ""
