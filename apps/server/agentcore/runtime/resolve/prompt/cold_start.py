"""Cold-start explore + folder-profile hint fragments."""

# Injected only when the user names a refresh (先了解 / 重新了解 / …).
# Empty profile and workspace rebind no longer open this act.
_COLD_START_EXPLORE_HINT_TEMPLATE = """
<冷启动探索>
【冷启动探索幕】{reason_line}
先轻探再 delegate 调研建档。\
禁止用 `remember` 把文件夹简报写成用户规则。探索 pending 期间 worker 写盘不得出 AgentCore/ 约定记忆与探索笔记（本回合 create_folder 新建的云文件夹除外）。
</冷启动探索>"""

_COLD_START_EXPLORE_REASON_REFRESH = (
    "用户点名刷新文件夹记忆（画像已有内容，合并更新）。"
)

_COLD_START_EXPLORE_HINT_REFRESH = _COLD_START_EXPLORE_HINT_TEMPLATE.format(
    reason_line=_COLD_START_EXPLORE_REASON_REFRESH
)


# R2 soft hint only — never enter <冷启动探索> / never set explore-pending.
_FOLDER_NAV_STALE_HINT = """
<文件夹导航过期>
【文件夹结构提示】工作区相对上次探索写入时已变化。当前回合继续用已有画像/导航，**不挡**原请求；\
若需刷新可点名「重新了解」或「刷新文件夹记忆」。
</文件夹导航过期>"""


def _explore_act_block(reason: str | None) -> str:
    if reason == "refresh":
        return _COLD_START_EXPLORE_HINT_REFRESH.strip()
    return ""
