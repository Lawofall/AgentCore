"""Builtin surface roster (``ToolSurface.BUILTIN``).

Append platform / capability-face tools here. Order is part of the public
surface; keep relative order when inserting.
"""

from __future__ import annotations


def load_roster() -> tuple[type, ...]:
    from agentcore.tools.builtin.archive_create import ArchiveCreateTool
    from agentcore.tools.builtin.archive_extract import ArchiveExtractTool
    from agentcore.tools.builtin.browser import BrowserTool
    from agentcore.tools.builtin.code_diagnostics import CodeDiagnosticsTool
    from agentcore.tools.builtin.code_execute import CodeExecuteTool
    from agentcore.tools.builtin.code_search import CodeSearchTool
    from agentcore.tools.builtin.external_mount_readonly import ExternalMountReadonlyTool
    from agentcore.tools.builtin.file_ops import (
        FileAppendTool,
        FileBatchTool,
        FileCopyTool,
        FileDeleteTool,
        FileListTool,
        FileMoveTool,
        FileReadTool,
        FileWriteTool,
        GlobTool,
        MkdirTool,
        StrReplaceTool,
        WriteSectionTool,
    )
    from agentcore.tools.builtin.git_ops import GitTool
    from agentcore.tools.builtin.grep import GrepTool
    from agentcore.tools.builtin.host import HostTool
    from agentcore.tools.builtin.md_to_docx import MdToDocxTool
    from agentcore.tools.builtin.md_to_pdf import MdToPdfTool
    from agentcore.tools.builtin.terminal import TerminalTool
    from agentcore.tools.builtin.test_run import TestRunTool
    from agentcore.tools.builtin.web.download_url import DownloadUrlTool
    from agentcore.tools.builtin.web.read_url import ReadUrlTool
    from agentcore.tools.builtin.web.search import WebSearchTool

    return (
        # platform base
        WebSearchTool,
        ReadUrlTool,
        FileReadTool,
        FileWriteTool,
        FileAppendTool,
        StrReplaceTool,
        WriteSectionTool,
        FileListTool,
        GlobTool,
        FileDeleteTool,
        FileMoveTool,
        FileCopyTool,
        MkdirTool,
        FileBatchTool,
        MdToDocxTool,
        MdToPdfTool,
        ArchiveExtractTool,
        ArchiveCreateTool,
        DownloadUrlTool,
        GrepTool,
        CodeSearchTool,
        CodeDiagnosticsTool,
        GitTool,
        TestRunTool,
        CodeExecuteTool,
        # Long-running process face (CEO+worker · execution_class · start 运行时升审批)
        TerminalTool,
        # L3 团队浏览器：单一 ``browser``（GRANTABLE · action 政策表；screenshot 仅 worker）
        BrowserTool,
        # Host 第三能力面：单一 ``host``（schema NEVER · action 政策表 · host_class）
        HostTool,
        # C1 silent read-only external mount (CEO+worker · desktop_online only)
        ExternalMountReadonlyTool,
    )
