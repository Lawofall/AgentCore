"""Skill body: team_local_desk (本机进桌 / 通道 / 区外声称)."""

from __future__ import annotations

_TEAM_LOCAL_DESK = """\
<本机进桌>
本机目录进工作区、通道、区外声称——本条。真 Office / 产物路径 → `consult(team_delivery_env)`。\
跨文件夹 → `consult(team_cross_folder)`。

【进桌】用户点名已有目录 → Composer「直接改这个文件夹」或 \
`open_local_project` / `register_local_project` / `bind_local_folder`（≠离线）。\
换手机/网页接着改同一份才「导入到云」。\
远程 GitHub http(s) 进当前云桌 → 开场表有 `git` 则结构化 clone ≠ 说成本机导入；\
用户要自己建一张云桌才 Composer「从 Git 克隆」。\
`create_folder` ≠ 桌内工程根（那是另一张桌）；桌内结构用队员写路径 / `mkdir`。已有用户结构保持原样。\
本机传统工程走上列 open / register / bind。

【通道】桌面默认本地对话 = 本机引擎。云端对话并列可选。网页/手机无本机盘 → 云端对话。\
勿把云沙箱当桌面默认新建。「在哪工作」仅新建会话可选；勿引导用户去设置改模式。\
已绑/本机工程时「打开项目 / 跑起来看一下」= 跑当前工作区 ≠ 再弹 `open_local_project`。\
「优化/改项目」≠默认开文件夹卡：已有附件且用户收窄本轮 → 先读材料动手，勿把开文件夹当开工前置。\
已是云端会话仍缺口含 `run` ≠ 再引导「导入到云」当沙箱修复；说明沙箱不可用，给稍后重试 / \
`export_to_local` / 打开本机文件夹。\
自称已装桌面 / 正在用客户端仍以 `<工作区>` 客户端行与缺口 `host`/`local_open` 为准 ≠ 口述覆盖事实。\
未接先官网下载 → `consult(product_help)`，再桌面打开【本对话】；已连才导入到云 / 从 Git 克隆或本机传统。\
Web/移动端无法履行区外授权 → 须用桌面客户端，下载链同样 `consult(product_help)`。

【区外】看/分析本机目录 → 直接 `file_read` / `file_list` / `glob` / `grep` 本机绝对路径或 ~/Desktop|Downloads|Documents（桌面在线时运行时静默只读挂载）。\
整理/交付 → `file_copy` 到已挂 `external/<别名>/…` 或该本机路径（运行时请用户确认整理授权）；云上直接 `file_write` 本机路径会失败，先写工作区再 copy（单向、不覆盖）。\
用户已口头同意整理 → 立刻 `file_copy`（已挂用 `external/` 路径）。多个候选 → `ask_user` 让人选后再对该路径读写。\
本机传统旁根可改可覆盖 → `file_write` / `file_copy` 到已挂路径或该本机路径（运行时请用户确认可写授权）。\
挂载 ≠ 「同时开发两项目」的默认步。\
整理方案 `card="organize_plan"` → `consult(asking_the_user)`。\
仅当 `<工作区>` 出现「区外：」才可声称已授权 / 授权已确认；无该行 = 无挂载。\
点名找路径 ≠ `host(action=shell)` / `run` 探家目录。
</本机进桌>"""
