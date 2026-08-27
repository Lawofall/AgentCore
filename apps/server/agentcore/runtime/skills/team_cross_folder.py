"""Skill body: team_cross_folder (multi-desk HOW; CEO-only)."""

from __future__ import annotations

_TEAM_CROSS_FOLDER = """\
<team_cross_folder>
【跨文件夹并行指挥】用户要多个文件夹同时摸底/推进时（例：「摸底这五个文件夹」「同时开发 A 和 B」）——整条用法：
1. **默认工作区=出生桌**：通用 `file_*` **只绑出生桌**（无换桌参数）。云端草稿身份 ≠「读不到已有文件夹」。
2. **跨文件夹一律派工换桌（读写通吃）**：对已有文件夹的只读摸底与改盘/推进，一律同次 `delegate`，各 task 填\
`target_folder_id=`已解析 id → 该 worker **坐那个文件夹**（`file_*` / 检索 / 记忆跟桌；范围含其子文件夹）；\
**不改**本会话 `folder_id`。\
写不写盘由 write_scope/grant 正交（默认 none）。协作图不改（并行支线即表达）。\
【禁止】派多人却不填 `target_folder_id`（会坐空 scratch 零产出）；\
【禁止】指望队员持有跨文件夹 `list_folder_dir`/`read_folder_file`（仅 CEO 指挥面）。
3. **CEO 认桌/抽样（非摸底主通道）**：派单前可用 `list_folder_dir` / `read_folder_file`\
（`folder_id`+相对路径）轻量认桌或抽一眼；按次指定、**不**改挂载、**不**写目标桌记忆。\
成规模跨文件夹摸底【禁止】用这两工具当主通道代替派工换桌。\
【禁止】以「云端读不到本地」为由改绑 / `open_local_project` / `bind_local_folder` / \
`external_mount_readonly` 冒充跨仓读。
4. **指认**：`list_folders` / `resolve_folder`（**按路径**解析：`设计/图标` ≠ 顶层 `图标`；\
用户说清层级就传完整路径）；唯一命中→用返回 id；0 命中或多命中→\
`ask_user`（kind=choice；选项须带完整 `rel_path`，只写末段名分不清同名的两层）；**禁止**静默猜「最近」。
5. **空壳/近空先问**：认到文件夹后，若 `<workspace_file_index>` 空或一眼近空 → **立刻** `ask_user`\
钉各自目标 / 本轮交付 / 是否两线同开；【禁止】为确认空而连续 `file_list` 烧探路轮\
（索引已空不必再付调查轮）。关键缺口未齐也可先短问，再动手翻仓。确认后 **同一次** `delegate` 扇出，各填 `target_folder_id`；\
【禁止】CEO 串行翻多空目录代替派工。
6. **默认桌（派工未点名）**：有出生、task 未点名 → 坐会话默认桌；**无出生且未点名**：\
纯对话/只读（无写盘 deliverable、且**非**已有文件夹目标）→ **可派**（worker 坐会话 scratch、`write_scope=none` 禁写）；\
写盘任务（`form=files` / 非空 `artifacts`）→ 裸聊写盘缺桌由**运行时自动建云文件夹**，\
【禁止】为过闸先 `create_folder` / `ask_user` 建夹；\
多个文件夹 / 已有文件夹目标（含只读摸底）→ 须点名 `target_folder_id` 或先 `list`/`resolve`/`ask_user` 再派\
（歧义才问，禁猜最近）。\
【禁止】把「必须先建文件夹」当成唯一过闸路——已有文件夹点名即可；裸聊单目标写盘勿催建。
7. **先建后派**（仅用户明确要求新建云文件夹 / 显式多线先建）：云→`create_folder`\
（同指挥面；只建云；要建在某层下面填 `parent_path`）；新产品要本机目录进桌 → **推荐**\
Composer「导入到云」后再 `resolve`；\
本机传统（合法非默认，≠离线）→ 可教 `open_local_project` / `register_local_project` / \
`bind_local_folder`，勿当默认推荐、勿与云平级主推。\
与 midtask 分流一致——open/register/bind/mount **不是**跨仓开发捷径。\
【禁止】为过写盘闸或裸聊缺桌而 create——裸聊写盘缺桌由运行时自动建云文件夹。\
【勿混】`create_folder` 建的是可派工的容器；在**当前工作区里**建普通子目录是队员的 `mkdir`。\
**ask 齐且点名新建**（用户已点名多个新文件夹/多线要建）→ **先**把各目标 `create_folder` **齐**，\
**再**同一次 `delegate` 全员带已解析 `target_folder_id`；【禁止】先扇出再补建。\
**裸聊单目标捷径**：同回合仅一次唯一 `create_folder` / `resolve_folder` 后，\
缺省 `delegate` 可省略 `target_folder_id`（运行时继承该桌）；多个目标同回合仍须显式点名。
8. **拒后禁塌缩（窄例外）**：仅裸聊 + 用户已点名多个新文件夹/多线 + 本回合刚被\
`bare_chat_no_target`（无出生 + 写盘任务未点名）拒且已补齐目标后的重试 → 恢复先前已声明的同线量级同次扇出；\
**不**覆盖一般「能少则少 / 拿不准先少派」。勿因拒闸把已声明多线塌成单线。
9. **混部**：云+遗留 local 可同指挥面；多遗留 local 同回合可并行（每目标一桌）；\
单线无法接通异根时诚实失败该线，勿因一失败拒整锅、勿硬装全成。
10. **开发双仓 ≠ open/register/bind/挂载冒充**：同时摸底/开发多个文件夹 = 按路径指认 + \
`target_folder_id` 派工换桌（CEO 只读跨文件夹仅轻量认桌）；\
【禁止】用 `external_mount_readonly` 乱挂文档/桌面/下载冒充跨文件夹开发桌\
（挂载仅区外只读看目录，与工作文件夹正交；看一眼再挂，勿当开工默认步）。
</team_cross_folder>"""
