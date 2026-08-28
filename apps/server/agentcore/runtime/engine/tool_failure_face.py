"""User-facing tool failure face (``tool_use_end.failure``) — category gate.

Isomorphic to :func:`agentcore.core.errors.error_fields_for`:

- Authored product copy (engine deny paths / optional ``ToolResult.failure_message``)
  passes through with a stable ``code``.
- :class:`~agentcore.core.errors.AgentCoreError` on the exception path passes through
  its type-owned code + message.
- Everything else (raw ``str(exc)``, model-facing join text, internal tokens) collapses
  to a curated Chinese sentence for the given code — never the technical detail.

Model-facing ``tool_use_end.result`` / transcript stay untouched; this module only
builds the optional ``failure: {message, code}`` user channel.
"""

from __future__ import annotations

from typing import Any

from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import AgentCoreError

# Default user sentence when no authored product copy / coded AgentCoreError is present.
# Deliberately advice-free: the fallback catches deterministic failures too (a permanently
# blocked target, a malformed argument), where「请稍后重试」is an active lie — the same call
# fails again. State the fact plus the agent's own next move; any real advice belongs to a
# code-specific sentence below, where the cause is actually known.
DEFAULT_TOOL_FAILURE_MESSAGE = "这一步没能完成，我会换个方式继续。"

# The model named something that is not a callable tool — a typo/hallucination, a
# protocol-tag residue, or the legacy landed-status bait. All the same fact for the
# user: the step did not run and the agent routes around it. The name itself is model
# vocabulary and stays on ``result``.
_TOOL_NAME_UNRESOLVED = "这一步没能用上合适的工具，已跳过；我会换个方式继续。"

# Stable-code → curated Chinese (never ``str(exc)``). Engine deny / timeout paths pass a
# code alone and read their user face from here; ``product_message`` is reserved for the
# rare path that authors real product Chinese (write-args parse — see ``tool_exec_args``).
# Twin of tool-side ``metadata["code"]`` / ``ToolResult.failure_code`` — review copy here.
# String literals below stay byte-equal to their tool/db sources (no import — avoids
# engine ↔ db/tools cycles): ``db.errors.DATABASE_UNAVAILABLE_MESSAGE``,
# ``exec_env.EXEC_ENV_PROBE_FAIL_USER_MESSAGE``, ``core.net`` local-search connect copy.
#
# Copy rule for every sentence here: say what happened, what it means for the user, and
# what they can do — in that order, in the user's own vocabulary. Deny paths steer the
# model with imperatives ("不要原样重试" / "请改用其他方案"); those belong on ``result``
# only. A denial the user themselves issued must never come back as an order to them,
# and an unattended path must never promise a confirmation UI that does not exist.
# Engine concepts (run / 收口 / 台账 / handoff / 落盘 / 活性挂起 / 白名单 / 检索预算 /
# 收尾窗口 / 结构闸) are internal — they never appear below.
# Only promise a retry when the cause really is transient: for a deterministic refusal
# (bad argument, permanently blocked target, unsupported host) say what the agent will do
# instead. An unregistered code collapses to the advice-free default rather than lying —
# and ``tests/test_tool_failure_face.py`` scans tool/engine sources for code literals, so
# a new code without a sentence here turns that test red.
_CURATED_BY_CODE: dict[str, str] = {
    ErrorCode.TOOL_ERROR: DEFAULT_TOOL_FAILURE_MESSAGE,
    ErrorCode.TOOL_NOT_FOUND: _TOOL_NAME_UNRESOLVED,
    ErrorCode.SANDBOX_ERROR: "代码执行环境暂时不可用，请稍后重试。",
    ErrorCode.SANDBOX_TIMEOUT: "代码执行超时，请缩小范围后重试。",
    ErrorCode.VALIDATION_ERROR: "工具参数无效，已中止本次调用。",
    ErrorCode.FORBIDDEN: "当前无权执行该操作。",
    ErrorCode.NOT_FOUND: "未找到所需资源，请换一种方式继续。",
    ErrorCode.RATE_LIMITED: "请求过于频繁，请稍后再试。",
    ErrorCode.QUOTA_EXCEEDED: "用量已达上限，请稍后再试或调整配额。",
    ErrorCode.STREAM_ERROR: (
        "工作区/本地文件连不上，请稍后重试或重开桌面，我会基于已经拿到的内容继续。"
    ),
    ErrorCode.DATABASE_UNAVAILABLE: "AgentCore 服务暂时不可用，请稍后重试",
    "database_unavailable": "AgentCore 服务暂时不可用，请稍后重试",
    # Engine meta codes (not ErrorCode members) — still stable on the wire.
    "retrieval_budget_exhausted": (
        "本次任务的联网查资料次数已用完，这一次没有再去搜；我会基于已经查到的内容继续。"
    ),
    "args_parse_failed": "工具参数无效，已中止本次调用。",
    "allowlist_deny": (
        "这一步要用的工具不在本次任务的可用范围内，已跳过。如果需要，可以让我换个方式来做。"
    ),
    # The task is out of time / tokens and only finishing tools stay open.
    "wind_down_deny": "本次任务已接近时间或用量上限，正在整理结果，这一步用到的工具已经停用。",
    # Safety fuse. Nothing ran, so the user's files and data are untouched — say so.
    "safety_breaker_deny": (
        "出于安全考虑，这一步操作已被拦下，没有执行。如果确实需要，请告诉我你想怎么做。"
    ),
    # Fuse / confirmation needed but nobody to ask (unattended job, ops kill switch).
    # A conversation really does have the confirmation card, so pointing there is honest;
    # naming a generic「可确认的界面」to someone who is already the user is not.
    "safety_breaker_unattended": (
        "这一步有安全风险，而当前任务在后台运行、无法向你确认，已跳过。"
        "如果确实需要，可以在对话里让我重做这一步。"
    ),
    "approval_unattended": (
        "这一步需要你确认才能执行，而当前任务在后台运行、无法向你确认，已跳过。"
        "如果需要执行，可以在对话里让我重做这一步。"
    ),
    # Covers an explicit refuse *and* a card that timed out — hence「没有得到确认」rather
    # than「你拒绝了」.
    "approval_denied": (
        "这一步没有得到你的确认，没有执行，也没有改动任何东西。想继续的话告诉我一声。"
    ),
    "timeout": "这一步等了很久都没有响应，已经中止。可以让我缩小范围或换个方式再试一次。",
    "liveness_timeout": "这一步等了很久都没有响应，已经中止。可以让我缩小范围或换个方式再试一次。",
    "workspace_channel_dead": (
        "工作区/本地文件连不上，请稍后重试或重开桌面，我会基于已经拿到的内容继续。"
    ),
    "landed_status_name": _TOOL_NAME_UNRESOLVED,
    "host_unavailable": "浏览器宿主暂时不可用，请稍后重试。",
    "searxng_unreachable": "本地搜索服务不可用，请稍后重试",
    "exec_timeout": "执行超时，请缩小范围后重试。",
    "exec_forced_stop": "执行已强制中止，请缩小范围后重试。",
    "exec_env_probe_failed": (
        "本机执行环境不可用：这次没能判断出具体原因，代码没有运行。"
        "我会换个方式继续。"
    ),
    # --- 代码执行：环境起不来 / 语言缺失 / 联网不支持 ---
    # Distinct from the probe above: nothing about the machine is broken, the requested
    # language simply is not among the configured interpreters. May be cloud or local, so
    # the sentence claims no location.
    "language_unavailable": (
        "运行这段代码需要的语言当前用不了，代码没有执行。我会换用可用的语言继续。"
    ),
    "env_invalid": "这次带上的环境变量格式不对，命令没有执行。",
    "exec_env_no_interpreter": (
        "这台电脑上没找到运行这条命令的解释器，命令没有执行。"
        "装好之后我可以再试，也可以换一种语言继续。"
    ),
    # Same fact as the exec-env-dead bubble: startup too slow / the command did
    # not finish. Vocabulary is「命令」, not「代码执行环境」, so the two faces match.
    "exec_env_probe_timeout": (
        "执行环境没有在时限内就绪，命令没有运行。稍后再试通常会好转。"
    ),
    # The one place where naming security software is honest: the OS refused the spawn.
    "exec_env_spawn_denied": (
        "系统拒绝了启动执行程序，这段代码没有运行。常见原因是安全软件拦截，允许之后我可以再试。"
    ),
    "exec_env_not_linux": (
        "云端隔离执行只在云上的 Linux 环境可用。当前对话跑在你的电脑上，代码没有运行。"
        "我会换个方式继续。"
    ),
    "exec_env_sandbox_unavailable": (
        "云端隔离执行环境当前不可用，代码没有运行。我会换个方式继续。"
    ),
    # Self-correctable in the same round (switch language) — so no「稍后重试」.
    "launcher_unavailable": (
        "代码执行环境没能启动，这段代码没有运行。我会换一种语言或换个方式继续。"
    ),
    # Permanent for this environment: the sandbox has no outbound network at all.
    "sandbox_network_unsupported": (
        "这段代码需要联网，而当前的代码执行环境不支持联网，没能完成。"
        "我会改用其他方式拿到需要的信息。"
    ),
    # --- 工具选错：长驻服务 / 项目级验证走错通道（模型自纠，用户只看见换了做法） ---
    "long_running_redirect": (
        "这一步想启动一个会持续运行的服务，用的方式不对（只会一直干等到超时），没有执行。"
        "我会改用终端启动，或者把本机的启动步骤告诉你。"
    ),
    "wait_for_required": (
        "要启动的是一个会一直运行的服务，但这次没有设好「怎样算启动成功」的判断条件，已经中止。"
        "我会补上判断条件后重新启动。"
    ),
    "project_verify_redirect": (
        "这一步想用跑代码的方式做项目级的安装或检查，那样很容易超时，没有执行。"
        "我会换成专门跑测试 / 构建的方式重来。"
    ),
    "source_dump_redirect": (
        "这一步想用跑代码的方式把源文件内容打到输出里看，没有执行。"
        "我会改用读文件的方式查看正文。"
    ),
    "source_grep_redirect": (
        "这一步想用脚本打开源码再搜索，没有执行。"
        "我会改用搜索工具定位后再读文件。"
    ),
    # --- 项目验证（test_run） ---
    # Deterministic: the same call fails again, so the fix is mine, not a retry by the user.
    "verify_contract": (
        "这次要跑的检查命令没能确定下来（缺少或者填得不对），没有执行。我会改正后重跑。"
    ),
    # Posture split is a product fact (队员分工), not an engine concept.
    "verify_policy_inner": (
        "这类整体检查不归这位队员跑——他只做小范围自检，已跳过。整体验证会交给负责验收的成员。"
    ),
    # The check ran to completion and came back red — an ordinary result, not a breakage.
    "verify_result": "这次检查跑完了，结果没有通过。我会看报错继续修。",
    # --- 浏览器：密码框硬拒 / 没截到画面 / 动作没生效 / 连接授权失效 ---
    # The refusal is the product's own safety line, never an order back to the user.
    "password_blocked": (
        "密码只由你本人输入，我不会代你填写，这一步已经停下。"
        "需要登录的话，你在浏览器里亲自完成后我再接着做。"
    ),
    "no_frame": "这次没能截到页面画面，看不到实际显示效果。我会改用读取页面内容的方式来确认。",
    # The gesture did reach the page; the outcome did not happen ("没抛错" ≠ 成功).
    "postcondition_failed": (
        "页面上的这一步操作发出去了，但没有真正生效（内容没填进去，或者按钮当时点不动）。"
        "我会换个方式再试。"
    ),
    # Credentials expired — the host itself is alive (that case is ``host_unavailable``).
    "bridge_unauthorized": (
        "和浏览器之间的连接授权已经失效，这一步没能执行。重新连接之后我再继续。"
    ),
    # Sandbox network isolation could not be created — browser stays off for the rest
    # of the turn, so say that instead of implying the next click might work.
    "egress_unavailable": (
        "浏览器需要的联网环境没能建立，这一步没能执行，接下来也用不了浏览器。"
        "我会改用搜索和网页阅读继续。"
    ),
    # The named tab is gone / belongs to another job — the agent opens its own instead,
    # so neither sentence asks the user for anything.
    "session_not_found": "之前那个浏览器页面已经不在了，这一步没有执行。我会重新打开页面继续。",
    "session_bound_elsewhere": (
        "这个浏览器页面正被另一项工作使用，这一步没有执行。我会另开一个页面继续。"
    ),
    # The user is driving the browser by hand; the tool yields rather than fighting them.
    "user_in_control": (
        "你正在自己操作浏览器，这期间我不去动它，这一步没有执行。你操作完成后我会接着做。"
    ),
    # --- 读网页：目标指向本机 / 被安全策略拒绝 ---
    "loopback_host": (
        "这个地址指向你自己电脑上的服务，我这边访问不到，这一步没有执行。"
        "我会改用浏览器或终端来查看。"
    ),
    # Path / file:// / drive letter fed to read_url — tool is fine; switch to file_read.
    "not_a_web_url": (
        "这个不是公开网页地址，没法用读网页打开。我会改用读文件继续。"
    ),
    # http(s) URL fed to file_read — switch to read_url; do not treat as a workspace path.
    "url_not_workspace_path": (
        "这是网页地址，不是工作区里的文件。我会改用读网页继续。"
    ),
    # Reserved intranet / cloud-metadata names (*.internal, *.local). Permanent refusal.
    "blocked_host": (
        "这个网址指向内部网络专用的名字，不是公开网站，出于安全没有去访问。"
        "我会换个公开来源；确实需要这页内容的话，可以把它贴给我。"
    ),
    # Also covers a redirect or a connect-time answer that lands on a private address.
    "private_address_blocked": (
        "这个网址最终指向内网地址，出于安全没有去访问。我会换个公开来源继续。"
    ),
    # The block is correct and the link is fine — the user's own proxy is answering DNS
    # with placeholder addresses, and only they can change that.
    "fake_ip_proxy_blocked": (
        "这个网址被本机代理解析成了占位地址，所以没能访问——链接本身没问题。"
        "把代理的 fake-ip 模式关掉之后我可以再试。"
    ),
    "dns_resolve_failed": (
        "这个网址的域名解析不了，没能访问。"
        "可能是链接写错了，也可能是当前网络到不了它；我会换个来源继续。"
    ),
    # 401/403/429/451 — an auth wall or anti-crawl. Retrying the same URL never works,
    # but the user can open the page themselves, so name that as the real option.
    "site_access_denied": (
        "这个网站拒绝了访问（需要登录或者挡了自动抓取），没能读到内容。"
        "我会改查公开来源；如果必须是这一页，你打开后把关键内容贴给我，我接着用。"
    ),
    "http_status_error": "这个网页返回了错误，没能读到内容。我会换个来源继续。",
    # Per-host breaker after repeated transport failures — genuinely transient.
    "egress_circuit_open": (
        "这个站点刚刚连续访问失败，暂时不再去试了，这一步没有读到内容。我会先用已经拿到的材料继续。"
    ),
    "site_unreachable": "连不上这个网站，没能读到内容。我会换个来源继续。",
    "read_timeout": "这个网页读了很久都没有响应，已经中止。我会换个来源继续。",
    "too_many_redirects": "这个网址一直在跳转，没能读到内容。我会换个来源继续。",
    # A never-seen domain carrying a long opaque query is the data-exfil tell; the refusal
    # is the product's own safety line, so state it plainly without the threat model.
    "novel_domain_blocked": (
        "这个链接是个没见过的域名，还带着一长串可疑参数，出于安全没有去访问。我会换个可靠来源继续。"
    ),
    # Deep reading is off for the rest of the task after repeated failures.
    "read_url_retired": (
        "这次任务里网页深读连续失败太多，已经不再继续尝试了。我会基于已有材料继续。"
    ),
    # --- Git / 开 PR ---
    "no_remote": (
        "当前项目没有配置可用的远程仓库地址，创建 PR 这一步没能完成。关联远程仓库后可以让我再试。"
    ),
    "not_github": (
        "创建 PR 目前只支持 GitHub，而当前项目的远程仓库不是 GitHub，这一步没能完成。"
        "其他代码托管平台暂时不支持。"
    ),
    # Covers「没配置」and「凭据查询超时」alike — either way no credential was obtained.
    "unauthenticated": (
        "没有取到可用的 GitHub 凭据，创建 PR 这一步没能完成。"
        "可以在「设置 → Git 凭据」里配置后让我再试。"
    ),
    # Queue pressure, never a repo fault: another git call still holds this project.
    "repo_busy": "这个项目上还有另一个 Git 操作在进行，这一步没轮到、没有执行。我会稍后再试。",
    # The project has version history on disk, but Git refuses to open it (broken or
    # half-finished). Only repairing it helps — retrying the same command never does.
    "repo_unusable": (
        "这个项目的版本记录打不开（可能损坏了，或者上次没建完），这一步没能完成。"
        "修好之后我可以再试，也可以重新建一份版本记录。"
    ),
    # GitHub's own answer when opening a PR — only ``create_pr`` produces this group,
    # so each sentence may name the PR step. Producer: ``workspace/github_pr.py``.
    "auth_failed": (
        "GitHub 拒绝了当前的凭据（权限不够，或者凭据已过期），创建 PR 这一步没能完成。"
        "可以在「设置 → Git 凭据」里换一份有权限的凭据后让我再试。"
    ),
    "not_found": (
        "没有找到这个 GitHub 仓库，或者当前凭据看不到它，创建 PR 这一步没能完成。"
        "确认仓库地址和凭据权限后可以让我再试。"
    ),
    # 422 — most often「这两个分支之间已经有 PR」or head == base.
    "validation_failed": (
        "GitHub 不接受这次的 PR（常见原因是这两个分支之间已经有一个 PR，或者两个分支是同一个），"
        "这一步没能完成。我会先确认分支和已有 PR 的情况再继续。"
    ),
    "no_default_branch": (
        "这个 GitHub 仓库没有写明默认分支，无法确定 PR 要提到哪里，这一步没能完成。"
        "你指定一个目标分支，我就可以再试。"
    ),
    "invalid_args": "创建 PR 的标题或分支填得不对，这一步没有执行。我会改正后重来。",
    "api_error": "GitHub 返回了错误，创建 PR 这一步没能完成。我会看它的回复再决定怎么继续。",
    "network_error": "连接 GitHub 失败，创建 PR 这一步没能完成，请稍后重试。",
    # --- 本机工作区：终端 / 文件读写 ---
    # ``terminal`` ``location=local`` needs a live desktop process channel.
    "local_workspace_required": (
        "在你自己的电脑上启动终端需要连到本机工作区，当前没有这个连接，这一步没有执行。"
        "把项目连到本机之后我可以再试，或者我换个方式继续。"
    ),
    "cloud_desk_required": (
        "云端这边现在没法托管会一直运行的进程，这一步没有执行。我会换个方式继续。"
    ),
    "process_not_registered": (
        "这个后台进程已经找不到了（服务重启后不会假装它还在），这一步没有执行。"
        "需要的话我会重新启动。"
    ),
    "workspace_io_error": "读写工作区文件时出错，这一步没能完成。我会换个方式再试。",
    "too_large": "这份文件太大，没法一次读完。我会换办法继续。",
    "access_denied": "这个文件正被其他程序占用，没能写入。关掉占用它的程序后我可以再试。",
    "outside_workspace": "这个路径不在当前工作区里，没能读写。",
}


def tool_failure_fields(
    *,
    code: str | None = None,
    product_message: str | None = None,
    exc: BaseException | None = None,
) -> dict[str, str]:
    """Return ``{"message", "code"}`` for ``tool_use_end.failure``.

    Category gate (not string matching on model-facing text):
    - ``exc`` is :class:`AgentCoreError` → pass through type code + product message.
    - Non-empty ``product_message`` → authored user copy (engine / rare ToolResult field).
    - Else → curated Chinese for ``code`` (default ``TOOL_ERROR``); never ``str(exc)``.
    """
    if isinstance(exc, AgentCoreError):
        msg = (exc.message or "").strip() or (
            (product_message or "").strip()
            or _CURATED_BY_CODE.get(exc.code, DEFAULT_TOOL_FAILURE_MESSAGE)
        )
        return {"message": msg, "code": exc.code}

    resolved_code = (code or "").strip() or ErrorCode.TOOL_ERROR
    authored = (product_message or "").strip()
    if authored:
        return {"message": authored, "code": resolved_code}

    curated = _CURATED_BY_CODE.get(resolved_code, DEFAULT_TOOL_FAILURE_MESSAGE)
    return {"message": curated, "code": resolved_code}


def tool_failure_from_result(result: Any) -> dict[str, str]:
    """Map a failed :class:`~agentcore.tools.protocol.ToolResult` to ``failure``.

    Uses optional ``failure_message`` / ``failure_code`` when a tool authored them;
    otherwise curated copy for ``metadata["code"]`` or ``TOOL_ERROR``. Never lifts
    ``error`` / ``output`` onto the user channel.
    """
    meta = getattr(result, "metadata", None) or {}
    meta_code = meta.get("code") if isinstance(meta, dict) else None
    if not isinstance(meta_code, str) or not meta_code.strip():
        meta_code = None
    return tool_failure_fields(
        code=getattr(result, "failure_code", None) or meta_code,
        product_message=getattr(result, "failure_message", None),
    )
