"""Skill body: run — how to use the unified command face."""

from __future__ import annotations

_RUN = """\
<跑命令>
【跑命令】一条 `run`。command 按这台电脑的壳写（云端与已装 Git 的 Windows 是 bash；否则 PowerShell）。\
`cd 子目录 && pnpm test`、管道、`2>&1` 都按该壳执行。\
每次从工作区根起（或这次的 cwd）；换目录写在命令里，不跨两次 run。\
后台自己分：会退出的验证/短命令直接跑；dev server / watch 设 background=true（省略 wait_for 时用默认就绪信号，命中前不得宣称已启动）。\
已有后台进程：action=list|read|stop。\
长驻 ≠ host(action=shell)。\
【Windows .bat】写给 `cmd` 双击的 `.bat`：换行须 CRLF；`echo`/注释/提示 ASCII-only（禁 UTF-8 中文）；或改交 `.ps1`（建议 UTF-8 BOM）并写清启动方式。引擎不自动转码/改换行。\
【验绿】内环 `code_diagnostics` / 写盘回执可自检。慢 build / `npm install` / `tsc -b` / 全仓 pytest 走本工具，勿当短内联，勿用它们冒充 UI 修好。
</跑命令>"""
