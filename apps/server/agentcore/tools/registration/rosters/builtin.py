"""Builtin surface roster (``ToolSurface.BUILTIN``).

Append platform / capability-face tools here. Order is part of the public
surface; keep relative order when inserting.
"""

from __future__ import annotations


def load_roster() -> tuple[type, ...]:
    from agentcore.tools.builtin.browser import BrowserTool
    from agentcore.tools.builtin.file_ops import (
        FileBatchTool,
        FileDeleteTool,
        FileListTool,
        FileReadTool,
        FileWriteTool,
        GlobTool,
        StrReplaceTool,
    )
    from agentcore.tools.builtin.grep import GrepTool
    from agentcore.tools.builtin.host import HostTool
    from agentcore.tools.builtin.run import RunTool
    from agentcore.tools.builtin.web.search import WebSearchTool
    from agentcore.tools.builtin.web.web_fetch import WebFetchTool

    return (
        # platform base
        WebSearchTool,
        WebFetchTool,
        FileReadTool,
        FileWriteTool,
        StrReplaceTool,
        FileListTool,
        GlobTool,
        FileDeleteTool,
        FileBatchTool,
        GrepTool,
        RunTool,
        # L3 团队浏览器：单一 ``browser``（GRANTABLE · action 政策表；CEO+worker）
        BrowserTool,
        # Host：用户这台电脑上的短命令（schema NEVER · 运行时按 host 轴升审批）
        HostTool,
    )
