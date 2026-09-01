"""Shared constants for the ReAct engine."""

from agentcore.core.types import ToolCategory

MAX_PARALLEL_TOOLS = 5

# Tool-call arguments stream as many tiny deltas (a delegate 任务书 / file body =
# thousands of chars). Emit a progress event only when a call's accumulated args grow
# by ≥ this many chars (plus once when the tool name is first known) — throttling the
# tick that drives the「正在生成 {工具} · N 字」line (captain bubble via tool_progress,
# worker node via run_tool_progress).
#
# Trade-off — it's a char step, so #events = args_len / STEP and the counter jumps by
# STEP each tick, *independent of stream speed*:
#   • smaller → 更跟手 (counter climbs smoothly, feels live) but more SSE events →
#     more store writes / bubble re-renders, and short calls (a tiny str_replace)
#     emit ticks they don't need;
#   • larger → cheaper but the number lurches / lags on a long task book.
# 64 puts a typical DeepSeek arg stream (~150–300 chars/s) at ~3–5 ticks/s — clearly
# alive without flooding — and ≈ one text line per tick reads as "another line
# written". Each event is a tiny {tool_name, chars} + a one-field store patch, so even
# a 50KB write (≈800 ticks over its whole duration) is comfortably cheap; tune here if
# the bubble ever feels jittery (raise) or laggy (lower).
TOOL_PROGRESS_STEP = 64

# Injected when convergence governance forces finalize (a stuck loop trips a hard
# finalize, or the round budget is exhausted mid-tool-call).
FINALIZE_INSTRUCTION = (
    "[系统提示] 请停止使用调查与执行类工具，基于目前已掌握的全部信息，立即给出你最好的最终答案。"
    "若仍需委派或向用户确认，可调用 delegate / consult / ask_user。"
    "除上述可用工具外，其余工具本轮已停用；请直接用正文写出答案，"
    "切勿在正文里书写或模拟任何工具调用格式（如 <tool_call>…</> 之类标签），那不会被执行。"
)

# Files-form / artifacts workers: finalize must still allow real landing (align
# with wind_down). Without this the model is told to "give a final answer" while
# file_write is stripped — and may paste a DSML pseudo tool_call into prose.
FINALIZE_INSTRUCTION_FILES = (
    "[系统提示] 请停止调查与新战线。你的交付须落盘到真实产品路径：立即调用 file_write "
    "把代码/约定产物写进工作区，并调用 handoff 提交交接简报（若适用）。"
    "先定稿落盘，再 handoff 一次；handoff 即收尾，勿再改同一产物二次交接。"
    "禁止把 AgentCore/文档/research|reviews|debate 下的方案/笔记 md "
    "冒充修码或 form=files 产品交付；"
    "若当前无法改源码，请 handoff 诚实说明阻塞（缺权限、缺路径、契约矛盾等），勿用约定文档交差。"
    "勿把整份文件内容粘在正文里；若仍需向用户确认，可调用 ask_user。"
    "除上述落盘/交接工具外，其余工具本轮已停用；正文请直接书写，"
    "切勿在正文里模拟任何工具调用格式（如 <tool_call>…</> 之类标签），那不会被执行。"
)

# Coordination tools still offered during a forced-finalize round; investigation and
# execution tools are withheld so the model cannot keep spinning reads/writes.
FINALIZE_COORDINATION_TOOLS = frozenset({"delegate", "consult", "ask_user"})

# Persist tools kept on finalize when the worker's tool surface still offers
# file_write (form=files / artifacts / wind_down) — mirrors wind_down intent.
FINALIZE_PERSIST_TOOLS = frozenset({"file_write", "handoff"})

# Investigation + execution tools blocked during finalize (by name, explicit list).
# ``file_write`` is removed from the effective forbid-set when persist finalize is on
# (see ``resolve_finalize_coordination_tools``).
FINALIZE_FORBIDDEN_TOOLS = frozenset(
    {
        "file_read",
        "grep",
        "web_search",
        "read_url",
        "file_write",
        "str_replace",
        "run",
    }
)

# Tool categories whose calls are NOT bounded by the engine timeout backstop (B1):
# they legitimately block for minutes on a sub-run or the user, and are bounded by
# their own lifecycle instead — delegate/revise drive sub-DAGs (each constituent
# tool call is itself bounded), ask_user waits on the user behind its own checkpoint
# timeout. A flat ceiling here would wrongly kill a legitimate long wait.
TIMEOUT_EXEMPT_CATEGORIES = frozenset({ToolCategory.ORCHESTRATION, ToolCategory.INTERACTION})
