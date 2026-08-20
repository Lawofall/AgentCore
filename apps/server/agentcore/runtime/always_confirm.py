"""恒确认 (always-confirm) 判据——审批链上任何「跳过」都必须先问这里。

三类调用永远要人点一次确认卡：结构化 ``git push`` / ``git create_pr``
（[安全权限与治理 §熔断] 普通 push / create_pr 始终弹确认）、``host(action=install_package)``
（[工具与能力系统] P2 桶4），以及 ``delete_folder``（删文件夹逐个确认：一张卡只授权
一次删除，模型同回合发 N 个删除调用就弹 N 张卡）。「恒」= 没有任何授权或沙箱姿态
可以吃掉这张卡：不吃 ``file_write=session``、不吃开工/委派授权、不吃本轮 turn grant、
也不吃云端 worker 的逐次卡豁免。

判据独立成模块（不依赖 approvals / sandbox_approval / 工具注册表），是因为它原本私藏在
``ApprovalGate.authorize`` 里：任何在 authorize **之前**下的判断——云端 worker 免审
（``engine.tool_exec_gates``）、worker 是否分到 gate（``delegate.drive_setup``）——都能
在它开口前把卡短路掉。放在所有审批层之上、零依赖，新增短路路径时只有这一个显然的判据要问。
"""

from __future__ import annotations

from typing import Any

_GIT_REMOTE_PUBLISH_SUBCOMMANDS = frozenset({"push", "create_pr"})
_HOST_INSTALL_ACTION = "install_package"
_DELETE_FOLDER = "delete_folder"
# 与参数无关的恒确认工具（每次调用都要卡）。
_UNCONDITIONAL_ALWAYS_CONFIRM = frozenset({_DELETE_FOLDER})
# 仅按工具名的预筛：这些工具「存在」恒确认形态。当前无生产消费者——它原本服务的
# 「worker 该不该分到本回合 ApprovalGate」预判已删（gate 一律下传，弹不弹卡由收口点按
# arguments 判）。再拿它去上游提前吞掉 gate，就是本模块开头警告的那个 bug。
_ALWAYS_CONFIRM_TOOL_NAMES = _UNCONDITIONAL_ALWAYS_CONFIRM | frozenset({"git", "host"})


def is_git_remote_publish(tool_name: str, arguments: dict[str, Any] | None) -> bool:
    """True 表示这是结构化 ``git push`` / ``git create_pr``（推远端 = 恒确认）。"""
    if tool_name != "git":
        return False
    sub = str((arguments or {}).get("subcommand") or "").strip().lower()
    return sub in _GIT_REMOTE_PUBLISH_SUBCOMMANDS


def is_host_package_install(tool_name: str, arguments: dict[str, Any] | None) -> bool:
    """True 表示这是 ``host(action=install_package)``（按参数恒确认）。"""
    if tool_name != "host":
        return False
    action = str((arguments or {}).get("action") or "").strip().lower()
    return action == _HOST_INSTALL_ACTION


def requires_always_confirm(tool_name: str, arguments: dict[str, Any] | None) -> bool:
    """True 表示这次调用无论持有何种授权都必须弹确认卡。"""
    if tool_name in _UNCONDITIONAL_ALWAYS_CONFIRM:
        return True
    if is_git_remote_publish(tool_name, arguments):
        return True
    return is_host_package_install(tool_name, arguments)


def always_confirm_tool_names() -> frozenset[str]:
    """*可能* 触发恒确认的工具名（与参数无关的预筛）。

    只回答「这个工具有没有恒确认形态」，不回答「这一次调用要不要卡」——后者用
    :func:`requires_always_confirm`，审批链上的判断一律走它。
    """
    return _ALWAYS_CONFIRM_TOOL_NAMES
