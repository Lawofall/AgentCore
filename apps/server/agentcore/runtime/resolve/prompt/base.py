"""Shared system-prompt base fragment (FRAGMENT_BASE) + runtime date context."""

import time

# 全员基座（CEO + 每位 worker）：现空、不注入。Assembler 跳过 falsy。
# 回潮走内容闸，且 eval 证明这句承重。
# 来源编号在回执尾；目录行 ≠ 已查阅在 `<按需目录>` 前言，不进本基座。
# 「已写进文件则结论给路径」不进本基座：条件句每回合都在，会被读成催写盘。写盘场面才成立：prose write 的模型转录尾注，不进写回执，不预写进常驻。
# 听谁的：真用户 role=user；引擎信封/纠偏 = [系统提示] 围栏的合成 user；回执 role=tool。
# 不进本基座。用户指令 vs <设定>：设定块前言 + 路由硬约束。
# 工种不对称（卡住问谁）进工具 description。CEO 工厂身份现空（回潮走入场闸）。路由在 delegate description。
# 基座不写「队员」、不套 <身份>。叶子 / 队长不写工厂身份。
# 不写 CEO 路由、交法展览（已声明路径才进当场「交付物规格」）。
# 检索何时收敛写在 web_search description，不进本基座。
# 未装配 ≠ 写进队员任务 在 delegate task 参数，不进核。
# 「邻格 ≠ 否决本格」出核（补集）。换路 HOW 不进本基座（缺口在工作区；晋升在 consult）。
# 不写注入近义词表；consult 时序写在 consult description，不进本基座。
# 凭据落盘：写侧熔断硬拒。不进本基座。
# 某时刻长到需要目录再套该时刻标签；不要预留空壳。
_DEFAULT_SYSTEM_PROMPT = ""

# Date granularity (NOT second-precision time) on purpose. CEO and workers:
# this block rides a per-turn ``[系统提示]`` envelope so ``role: system`` stays
# frozen. A date is byte-identical within a day. Time-of-day stays out of the
# frozen system prefix.
_RUNTIME_CONTEXT_TEMPLATE = """
<运行时>
当前日期：{date}
</运行时>"""


def render_runtime_date_block() -> str:
    """``<运行时>`` date line — CEO and worker envelopes share this render."""
    return _RUNTIME_CONTEXT_TEMPLATE.format(
        date=time.strftime("%Y-%m-%d %Z", time.localtime())
    ).strip()
