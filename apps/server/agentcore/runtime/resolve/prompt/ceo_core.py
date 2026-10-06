"""CEO routing core fragment (FRAGMENT_CEO_CORE).

Resident core is empty unless eval proves a strategy residual the tool graph
cannot encode — Assembler skips a falsy fragment. 署名 / 「你是谁」走 ``product_help``；
何时派在 ``delegate`` description。全员基座同样现空；consult 钩在
``<按需目录>`` / consult description。
何时用 ``delegate`` / ``ask_user`` / ``debate`` 写在各工具 description（``delegate`` = 信息判据原理句，不进核）；场面 HOW 的
唯一所有者是 skill 正文；编制 HOW 在 ``delegate`` 按钮；填卡 HOW 在 ``ask_user`` 按钮。
``<工作区>`` 只陈述本回合事实；``<按需目录>`` 的系统 Skill 行写何时才查阅（前言另切目录行 ≠ 已查阅）。
全员基座现空（``prompt/base.py``）。未装配 ≠ 写进队员任务 在 ``delegate.task``。
不写编号判决树。每条纪律在装配后的提示串里只应出现一次。
"""

# Appended ONLY to the entry CEO chat agent's prompt (not to delegated workers).
# Factory identity is empty: add ``<身份>`` back only via the 入场闸 after eval.
# when-to-use for ``delegate`` is the principle on its description, not a quiz.
# 「你是谁」→ ``product_help``. Assembly state lives in ``<工作区>``.
# HOW lives on the owning tool / skill — one owner per piece.
# Credential plaintext writes: breaker DENY (not this fragment).
_CEO_CORE_HINT = ""

# 何时用工具写在各工具 description。目录只写这是什么。无第二处会对打。
_CEO_CORE_HINT_TEMPLATE = _CEO_CORE_HINT

# 工具侧已无 consult 手册（host / browser 装配即在开场表，选错通道靠回执）。
# ``capability_how_suffix`` 仍给 consult 拼，现恒为空。


def capability_how_suffix(ceo_tool_names: set[str]) -> str:
    """CEO consult HOW for on-demand tool faces. Empty: no tool handbooks remain."""
    del ceo_tool_names
    return ""


def assemble_ceo_core(ceo_tool_names: set[str]) -> str:
    """Resident identity core. On-demand HOW is consult-owned, not a core suffix."""
    del ceo_tool_names
    return _CEO_CORE_HINT


# Scene-gated：仅本回合有附件块或结构化 ``[resident missing]`` 时注入。
# 不进 ``assemble_ceo_core`` / 常驻核。
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
