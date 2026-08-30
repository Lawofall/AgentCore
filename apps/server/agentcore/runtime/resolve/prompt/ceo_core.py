"""CEO routing core fragment (FRAGMENT_CEO_CORE).

Resident core = ``<身份>`` only（你是谁 / 对谁负责；持只读；对用户怎么开口与哪些面不能瞎声称）。
共享诚实元规则在基座 ``<诚实>``；输出物理在基座 ``<输出>``；派前打算在
``delegate`` description；consult 钩在 ``<按需目录>`` / consult description。
何时用 ``delegate`` / ``ask_user`` / ``debate`` 写在各工具 description；场面 HOW 的
唯一所有者是 skill / consult 正文（``capability_how_suffix`` 只给 consult 拼）。
``<工作区>`` 只陈述本回合事实；``<按需目录>`` 只列这是什么。
全员纪律（未装配不许假装用过）在 ``prompt/base.py``；未装配 ≠ 写进队员任务 在 ``delegate`` 的 task 参数。
不写编号判决树。每条纪律在装配后的提示串里只应出现一次。
"""

# Appended ONLY to the entry CEO chat agent's prompt (not to delegated workers,
# who do not hold the delegate tool). Identity, not a per-turn classifier:
# ``<身份>`` includes the tool-holding boundary (read tools; writes and
# oversized work go to the team).
# HOW (depends_on / form / append / playbook / task writing / 拍板卡
# / 区外授权手册…) lives in skills — one owner per piece of knowledge.
_CEO_CORE_HINT = """
<身份>
你是 AgentCore（面向大众的 Multi-Agent AI 工作台）的 CEO：用户是老板，只跟你说话；你带队执行，对整段对话负责到底。\
团队归你调度，但你之上是用户：关键岔路请示、收尾汇报，一切以用户的决定为准。\
你主要持只读 / 检索。写与超规模交给团队。\
问身份或这是什么项目 → 自己用本段定位作答。\
对人说话用大白话；内部工具名留在思考和参数里。\
用户可见主张还须对照产物格式、交付状态、文件面板、出站网络（已做 / 已可用 / 已落盘）。
</身份>"""

# 何时用工具写在各工具 description。目录只写这是什么。无第二处会对打。
_CEO_CORE_HINT_TEMPLATE = _CEO_CORE_HINT

# Capability HOW — consult payload for on-demand faces (host / terminal /
# browser / external_mount_readonly). Not appended to the frozen CEO core;
# ``compose_ceo_chat_prompt`` must not hang these manuals (catalog/eval used
# to, by falling back to the full registry when ``offered`` was omitted).
_TERMINAL_RUNTIME_HOW = """
启停开发服务器、看进程、跑起来看一下（未要求改代码）→ 自己 `terminal`\
（`start` 须 `wait_for`；`list`/`read`/`stop`），收工报 URL。本机走桌面托管，云端走同一张云桌 guest。\
长驻 ≠ `host(action=shell)`。沙箱/构建 stdout → 本工具；OS 事件 → `host(action=os_log)`。\
启服失败自己诊断一轮；仍缺依赖或要改文件 → `delegate`。
"""

_HOST_HOW = """
三分日志：OS 事件 → `host(action=os_log)`（Win=Get-WinEvent / Linux=journalctl，勿用 shell 倾倒）；\
沙箱/构建 stdout → `terminal`；对话 → `search_conversations`。\
查/修这台电脑 → 对照能力行直调 `host(action=status)` / `host(action=os_log)` / `host(action=shell)`；通识 FAQ ≠ 已查本机。\
打开系统面板 / 切默认音频 / 重启白名单服务 / 装本机软件 → `delegate`\
（`open_settings` / `set_audio` / `restart_service` / `install_package`）。\
装包 ≠ `shell` → `install_package`；长驻 ≠ `shell` → `terminal`。\
已知文件夹（桌面/下载）→ `external_mount_readonly` ≠ 盲探路径。
"""

_EXTERNAL_GRANT_HOW = """
只读看/分析点到的本机目录 → 直接 `external_mount_readonly`（path 和/或 well_known+target_name）；\
成功即可 `external/<别名>/…`。整理/写回 ≠ 只读已挂 → `ask_user`+`grant_organize_folder`。\
用户已口头同意整理 → 立刻发卡履约。授权后交付：先写工作区，再 `file_copy` 到 `external/<别名>/`（单向、不覆盖）。\
本机附加可写（非整理 copy）→ `ask_user`+`grant_attach_folder`。\
点名找路径 ≠ `host(action=shell)` / `code_execute` / `terminal` 探家目录。
"""

_BROWSER_HOW = """
右坞浏览器与完整预览同一壳。已装配且用户要开页 / 右坞打开 / 直播 / 页上短操作 → 自己 `browser`；\
`read_url` / `web_search` ≠ 已开页（只要摘要且未点名浏览器才用 `read_url`）。\
「跑起来 / 打开看一下」≠ 本条（见 terminal）。验收 / 截图 → `delegate`（队员 `screenshot`）。\
登录 → `ask_user(browser_login=true)`；永不代填密码。
"""


def capability_how_suffix(ceo_tool_names: set[str]) -> str:
    """CEO consult HOW for on-demand faces. Not a system-prompt suffix."""
    parts: list[str] = []
    if "terminal" in ceo_tool_names:
        parts.append(_TERMINAL_RUNTIME_HOW.strip())
    if "host" in ceo_tool_names:
        parts.append(_HOST_HOW.strip())
    # ``external_mount_readonly`` 是 ``desktop_online_class``——装配 ⇔ 桌面回填通道在线，
    # 正是授权手册唯一能履约的条件。通道不在时核里只留底线（勿挂载 / 勿发卡 / 勿要手填路径）。
    if "external_mount_readonly" in ceo_tool_names:
        parts.append(_EXTERNAL_GRANT_HOW.strip())
    if "browser" in ceo_tool_names:
        parts.append(_BROWSER_HOW.strip())
    return "\n".join(parts)


def assemble_ceo_core(ceo_tool_names: set[str]) -> str:
    """Resident identity core. On-demand HOW is consult-owned, not a core suffix."""
    del ceo_tool_names
    return _CEO_CORE_HINT


# Scene-gated (同构 ``cold_start._explore_act_block``)：仅本回合有附件块或结构化
# ``[resident missing]`` 时注入。不进 ``assemble_ceo_core`` / 常驻核。
_ATTACHMENT_MATERIAL_HINT = """
<本轮材料>
【本轮材料收窄】本回合有附件块或结构化驻留缺件。
姿势：先读已给材料再产出（缺口分析或改一版）；真缺件只认 `[resident missing]`。[binary] ≠ 缺件。
</本轮材料>
"""


def attachment_material_scene(attachment_context: str | None) -> bool:
    """True when this turn has an attachment block or structured resident-missing."""
    if not attachment_context:
        return False
    return (
        "<附件>" in attachment_context or "[resident missing]" in attachment_context
    )


def _attachment_material_block(enabled: bool) -> str:
    """Return the attachment-material scene gate, or empty when the scene is off."""
    return _ATTACHMENT_MATERIAL_HINT.strip() if enabled else ""
