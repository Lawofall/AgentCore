"""Context subsystem: unified system-prompt assembly + on-demand context sources.

上下文注入统一. Houses the injection-side spine (:class:`ContextAssembler`) and the
shared "目录 + 按需取" protocol (:class:`Consultable`) for on-demand sources (rules
now; skills / memory adopt over time). The OUTPUT side (tool execution, memory writes)
is intentionally NOT here: unification is injection-side only (文档「守恒律」: 复杂度
搬家不消失).
"""

from agentcore.runtime.context.artifact_formats import (
    build_artifact_format_line,
    format_artifact_capability_line,
)
from agentcore.runtime.context.assembler import ContextAssembler, assembly_hash
from agentcore.runtime.context.consultable import Consultable, ConsultDirectoryEntry
from agentcore.runtime.context.contributor import PromptContributor, SectionOrder
from agentcore.runtime.context.outlet_inventory import (
    OutletDirListing,
    collect_outlet_inventory,
)
from agentcore.runtime.context.workspace_context import (
    ChannelProfile,
    WorkspaceGitFact,
    build_workspace_context,
    desktop_client_can_bind,
    detect_workspace_git,
    detect_workspace_git_sync,
    format_workspace_git_line,
    resolve_channel_profile,
)
from agentcore.runtime.context.workspace_overview import (
    attach_workspace_file_index,
    build_workspace_overview,
)

__all__ = [
    "ChannelProfile",
    "Consultable",
    "ConsultDirectoryEntry",
    "ContextAssembler",
    "PromptContributor",
    "SectionOrder",
    "OutletDirListing",
    "WorkspaceGitFact",
    "assembly_hash",
    "build_artifact_format_line",
    "collect_outlet_inventory",
    "build_workspace_context",
    "format_artifact_capability_line",
    "attach_workspace_file_index",
    "build_workspace_overview",
    "detect_workspace_git",
    "detect_workspace_git_sync",
    "desktop_client_can_bind",
    "format_workspace_git_line",
    "resolve_channel_profile",
]
