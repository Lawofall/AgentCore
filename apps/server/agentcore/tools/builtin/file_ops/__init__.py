"""File operations tools (read, write, list, precise str_replace edit, delete,
move, copy, mkdir, batch).

Thin shells over ``ToolContext.backend``: each tool parses arguments, calls the
workspace backend, maps typed ``WorkspaceError`` failures back to user-facing
messages, and renders a ``ToolResult``. All actual I/O and the path-traversal
guard live in the backend, so the same tools run unchanged against a server or a
local (desktop) workspace.

Split axes (implementation modules):
- ``integrity`` — integrity & write-scope policy
- ``errors`` — error / result mapping
- ``read`` — file_read / file_list (one-layer LS; FileListTool still in read.py)
- ``listing`` / ``glob`` — listing helpers + globstar search
- ``mutate`` — write / append / str_replace
- ``meta`` — delete / move / copy / mkdir
- ``batch`` — file_batch

Public import path stays ``agentcore.tools.builtin.file_ops``.
"""

from agentcore.tools.builtin.file_ops.batch import FileBatchTool
from agentcore.tools.builtin.file_ops.errors import _outside_workspace_msg
from agentcore.tools.builtin.file_ops.glob import GlobTool
from agentcore.tools.builtin.file_ops.integrity import (
    _mark_landed_files,
    _prepare_write_relpath,
    classify_write_kind,
    content_sha256_short,
    extract_title_tree,
    format_artifact_manifest,
    has_omission_marker,
    has_skeleton_markers,
    is_severe_shrink,
    is_skeleton_content,
    prose_append_rejection,
    write_scope_rejection,
)
from agentcore.tools.builtin.file_ops.listing import expand_brace_globs
from agentcore.tools.builtin.file_ops.meta import (
    FileCopyTool,
    FileDeleteTool,
    FileMoveTool,
    MkdirTool,
)
from agentcore.tools.builtin.file_ops.mutate import (
    FileAppendTool,
    FileWriteTool,
    StrReplaceTool,
)
from agentcore.tools.builtin.file_ops.read import (
    _DEFAULT_READ_LINES,
    FILE_READ_SAFETY_CHAR_CAP,
    FILE_READ_SAFETY_LINE_CAP,
    FileListTool,
    FileReadTool,
)

__all__ = [
    "FileAppendTool",
    "FileBatchTool",
    "FileCopyTool",
    "FileDeleteTool",
    "FileListTool",
    "FileMoveTool",
    "FileReadTool",
    "FileWriteTool",
    "GlobTool",
    "MkdirTool",
    "StrReplaceTool",
    "FILE_READ_SAFETY_CHAR_CAP",
    "FILE_READ_SAFETY_LINE_CAP",
    "_DEFAULT_READ_LINES",
    "_mark_landed_files",
    "_outside_workspace_msg",
    "_prepare_write_relpath",
    "classify_write_kind",
    "content_sha256_short",
    "expand_brace_globs",
    "extract_title_tree",
    "format_artifact_manifest",
    "has_omission_marker",
    "has_skeleton_markers",
    "is_severe_shrink",
    "is_skeleton_content",
    "prose_append_rejection",
    "write_scope_rejection",
]
