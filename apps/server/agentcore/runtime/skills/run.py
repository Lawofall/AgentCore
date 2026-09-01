"""Skill body: run — how to use the unified command face."""

from __future__ import annotations

_RUN = """\
<跑命令>
【跑命令】一条 `run`。人在终端怎么敲，command 就怎么写。\
`cd 子目录 && pnpm test`、管道、`2>&1` 都按这台电脑的壳执行（云端与已装 Git 的 Windows 是 bash；否则 PowerShell）。\
每次从工作区根起（或这次的 cwd）；换目录写在命令里，不跨两次 run。\
后台自己分：会退出的验证/短命令直接跑；dev server / watch 设 background=true，并给 wait_for（宣称就绪须命中）。\
已有后台进程：action=list|read|stop。\
长驻 ≠ host(action=shell)。\
【Windows .bat】写给 `cmd` 双击的 `.bat`：换行须 CRLF；`echo`/注释/提示 ASCII-only（禁 UTF-8 中文）；或改交 `.ps1`（建议 UTF-8 BOM）并写清启动方式。引擎不自动转码/改换行。
</跑命令>"""
