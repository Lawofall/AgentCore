"""Shared workspace capacity ceilings (capacity contract ≠ liveness timeout).

Byte / entry ceilings fail fast as capacity contracts. Wall-clock timeouts are a
separate liveness signal (see ``runtime.engine.tool_deadline`` + tool_exec).

Aligned with desktop ``WORKSPACE_READ_MAX`` (``apps/desktop/src/main/fs/constants.ts``).
"""

from __future__ import annotations

# Whole-file read ceiling (text / bytes / line windows that load the file).
WORKSPACE_READ_MAX_BYTES = 5 * 1024 * 1024  # 5 MiB — mirrors desktop Local

# Office/PDF transparent extract: tighter than raw read so markitdown cannot burn
# the liveness wall-clock on a multi-MiB PDF that still fits the read ceiling.
OFFICE_EXTRACT_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB

# Entry ceiling for the **file panel** listing (REST ``/files``), far above the
# AI-facing ``_MAX_LIST_ENTRIES``: browsing is not a context budget, and a user
# who imported a repo must be able to reach their files. The desktop lists one
# directory at a time, so this only binds on a single enormous directory (or a
# whole-tree pull) — and the response carries ``truncated`` when it does.
WORKSPACE_BROWSE_LIST_MAX = 2000

# Exact detail string shared with desktop ``opErr("WorkspaceIOError", …)``.
FILE_TOO_LARGE_DETAIL = "文件过大，无法读取"

# Channel / tool-result markers for hung desktop / cancelled transport (not capacity).
LIVENESS_TIMEOUT_DETAIL_MARKERS = (
    "timed out",
    "活性挂起",
)

# Local workspace IO family retired together when the desktop channel is sticky-dead
# (fail-fast alone still lets the model thrash / re-delegate writers).
WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS: tuple[str, ...] = (
    "file_read",
    "file_list",
    "file_write",
    "file_append",
    "str_replace",
    "write_section",
    "file_delete",
    "file_move",
    "file_copy",
    "file_batch",
    "mkdir",
    "grep",
    "host",
    # Ambient listing rides the same local channel — retire with the file family
    # so post-dead index_files rejects are not leftover noise.
    "index_files",
    # Export / land-bytes tools: every call round-trips the same dead backend
    # (read the .md or .zip, write the sibling artifact / members / downloaded
    # bytes), so leaving them on the surface only buys guaranteed-failed rounds.
    # download_url even burns its network fetch first, then fails on write.
    "md_to_docx",
    "md_to_pdf",
    "archive_extract",
    "download_url",
    # Same shape: every call unconditionally reads workspace bytes through the dead
    # backend (``read_bytes`` → base64 → vision), so it can only fail — and leaving it
    # on the CEO surface invites 「换个工具再看一眼图」 rounds that never can work.
    "read_image",
)

# Short user-visible honest sentence (chat bubble / harvest fallback). Soft steer
# still tells the model to say this; A2 also pushes it without waiting on LLM.
CHANNEL_DEAD_USER_VISIBLE = (
    "本地文件暂时连不上。请检查桌面连接后重试；我将基于已有材料收口。"
)

# Quiet user-visible line when code_execute/test_run family retires on hangs
# (mirrors CHANNEL_DEAD_USER_VISIBLE — no card, one-shot content_delta).
#
# This is the *unclassified* fallback: it states the fact and stops there. It
# used to say「请检查桌面或安全软件」, which was wrong twice over — the desktop
# channel is normally alive (the same turn keeps reading files / running terminal
# through it), and security software only ever fits the spawn-denied branch below.
EXEC_ENV_DEAD_USER_VISIBLE = "本机暂时跑不了命令。我将基于已有材料收口。"

# Opening clause every variant below shares — harvest fallback detects the fact in
# an already-written body with it, so the wording may grow a cause but not lose this.
EXEC_ENV_DEAD_BODY_MARKER = "本机暂时跑不了命令"

# Per-reason lines, keyed by the exec-env probe codes classified in
# ``tools/sandbox/exec_env.py`` (kept as literals so this constants module stays
# import-free; a test pins the keys to that code set). Each keeps the shared
# 「本机暂时跑不了命令」opening so harvest detection stays keyed on one phrase.
EXEC_ENV_DEAD_USER_VISIBLE_BY_CODE: dict[str, str] = {
    # No language on this path — do not name Python (or any other interpreter).
    "exec_env_no_interpreter": (
        "本机暂时跑不了命令：这台电脑上没找到运行这条命令的解释器。"
        "装好之后可以重试；我将基于已有材料收口。"
    ),
    "exec_env_probe_timeout": (
        "本机暂时跑不了命令：执行环境没有在时限内就绪。"
        "稍后重试通常就好；我将基于已有材料收口。"
    ),
    "exec_env_spawn_denied": (
        "本机暂时跑不了命令：系统拒绝启动运行命令的进程（权限被拒）。"
        "这类拦截通常来自安全软件或权限策略，放行后可以重试；我将基于已有材料收口。"
    ),
}

# Prepare / turn-start abort when the local channel is already sticky-dead —
# no LLM, no "收口" framing (nothing ran yet). Keep ``channel dead`` so
# ``is_channel_dead_detail`` / SSE mapping stay aligned with tool envelopes.
CHANNEL_DEAD_PREPARE_ABORT = (
    "本机工作区通道无响应（已挂起 / channel dead）。请检查桌面连接后重试。"
)

# A desktop holds workspace caps but not this bound root. Shared by the turn-start
# presence gate (``runtime/pipeline/errors.py`` case 2, re-exported there as
# ``LOCAL_ROOT_NOT_HELD``) and by mid-turn op delivery, which meets the same fact
# after the gate has run — roots can be revoked while the turn is in flight. Lives
# here so the delivery side reaches it without importing the pipeline package.
LOCAL_ROOT_NOT_HELD = (
    "桌面已在线，但未声明持有本会话的本地目录"
    "（授权可能已移除，或已换用其他电脑）。"
    "请在桌面重新授权该文件夹后，点「重新生成」"
    "（不要再次发送）。"
)

WORKSPACE_CHANNEL_DEAD_RETIRE_STEER = (
    "本地工作区文件通道已挂起（活性无响应）：本回合起停用全部本地文件读写工具。"
    "请向用户说明「本地文件暂时连不上」，基于已有信息收口或请用户检查桌面连接后重试；"
    "禁止再调用文件工具，也禁止再派需要读写本地文件的队员。"
)


def exec_env_dead_user_visible(code: str | None = None) -> str:
    """User-visible exec-env-dead line for a classified probe reason code.

    Unknown / absent code → the fallback, which claims no cause. Never guess a
    remedy the evidence does not support (the old「请检查桌面或安全软件」).
    """
    return EXEC_ENV_DEAD_USER_VISIBLE_BY_CODE.get(
        (code or "").strip(), EXEC_ENV_DEAD_USER_VISIBLE
    )


def is_file_too_large_detail(detail: str | None) -> bool:
    """True when a workspace I/O detail is the shared oversized-file capacity signal."""
    text = (detail or "").strip()
    return text == FILE_TOO_LARGE_DETAIL or text.startswith(FILE_TOO_LARGE_DETAIL)


def is_liveness_timeout_detail(detail: str | None) -> bool:
    """True when a workspace/channel failure is a hang / no-response timeout."""
    text = (detail or "").lower()
    return any(m.lower() in text for m in LIVENESS_TIMEOUT_DETAIL_MARKERS)


def is_channel_dead_detail(detail: str | None) -> bool:
    """True when the failure is sticky channel-dead (not a single-op settle timeout)."""
    return "channel dead" in (detail or "").lower()


def op_liveness_timeout_metadata() -> dict[str, object]:
    """ToolResult.metadata for a single-op channel settle timeout (no family sticky)."""
    return {
        "code": "liveness_timeout",
        "liveness_timeout": True,
        "timeout_layer": "channel_op",
    }


def channel_dead_retire_metadata() -> dict[str, object]:
    """ToolResult.metadata for sticky channel-dead (family retire + steer)."""
    return {
        "code": "workspace_channel_dead",
        "liveness_timeout": True,
        "timeout_layer": "channel",
        "error_class": "permanent",
        "workspace_channel_dead": True,
        "retire_tools": list(WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS),
        "retire_message": WORKSPACE_CHANNEL_DEAD_RETIRE_STEER,
    }


def channel_dead_error_message(detail: str) -> str:
    """User/model-facing error text when the local file channel is sticky-dead."""
    return (
        f"本地工作区通道活性挂起（无响应）：{detail}。"
        "这不是文件过大或参数合同失败——"
        f"{WORKSPACE_CHANNEL_DEAD_RETIRE_STEER}"
    )
