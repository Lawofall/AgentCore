"""产物写回 · 直写沙箱的本地腿：扫描「本次执行改了工作区哪些文件」。

云桌 guest 与 ``SubprocessSandbox`` / 桌面 Local 都以工作区真盘为 cwd 写文件
（无 copy-out 枚举）。不事后看盘就没人能如实填 ``ExecutionResult.written_files``。
本模块是这条腿。

为什么是「执行后单次有界扫描 + mtime 截止」，而不是执行前后各拍一次全树快照：

* **成本几乎全在目录枚举次数上**，与文件数关系不大。实测本仓（剪掉 node_modules /
  .git 等噪音目录后仍有 19.5k 目录 / 196k 文件）：整树带 stat ~940ms，只枚举目录不
  stat 文件也要 ~870ms。前后两次快照 = 双倍这个代价，会把一条 100ms 的脚本拖成两秒。
  单次事后扫描省掉一半。
* **有界**：从 cwd 起 BFS，撞到 :data:`_MAX_DIRS` 或 :data:`_BUDGET_MS` 立刻收手。
  BFS 保证被截断的永远是最深的尾巴，而产物几乎都落在 cwd 或浅层子目录；于是「超大
  工作区」退化成「浅层如实、极深处可能漏报」，而不是每次执行整体变慢。代价边界：
  单次执行最多多花 ~``_BUDGET_MS``（普通工作区通常个位数毫秒，实测 4 层以内 6ms）。
* **代价**：mtime 截止有一条边界模糊带，见 :data:`_MTIME_MARGIN_NS`。

排除面（复用 ``_paths`` 两档忽略规则，不另起第三份名单）：路径感知旁路区
``AgentCore/{index,trash,baselines}``、系统噪音目录 ``IGNORED_DIRS``、系统噪音后缀
（``*.db`` / ``*.pyc``）、符号链接。**不**排除 AI 噪音后缀——AI 生成的 ``.png`` 图表、
``.zip`` 打包件正是交付物。

扫描连同 ``IGNORED_DIRS`` 一起剪掉（含 ``node_modules`` / ``out`` / ``dist``）。
三条理由——枚举这些目录正是成本大头；它们对 AI 视图与**用户文件面板**都隐藏；
名单共用单一真源。文件仍在真盘上，只是不进 ``written_files`` 台账。
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from agentcore.workspace._paths import (
    is_ignored_dir_name,
    is_internal_zone_relpath,
    is_system_ignored_file_name,
)

# 目录枚举上限。实测本仓 ~45µs/目录，4000 目录 ≈ 180ms，与下面的墙钟预算同量级。
_MAX_DIRS = 4000
# 墙钟兜底：网络盘 / 被杀毒软件挂钩的目录上，单次 scandir 可能慢几个数量级，
# 目录数上限就兜不住了。两道闸都要。
_BUDGET_MS = 250.0
# 报告条数上限：一次写出上千个文件（构建产物）时，列全既冲爆模型上下文也没有信息量。
_MAX_FILES = 200

# 文件系统时间戳粒度比墙钟粗且**向下截断**：刚起步就写的文件，mtime 可能落在我们读到
# 的启动时刻之前一点。实测 Windows/NTFS ~1ms；Linux 走粗粒度时钟（1 tick，HZ=100 时
# 10ms）；macOS/APFS ~0。截止值往前让 100ms 即十倍余量——而且解释器自身启动就要几十
# 毫秒，真正的写盘远在截止值之后，这条余量基本只是保险。
#
# 为什么不让更多：余量就是误报窗口（窗口内**别人**刚改过的文件会被算进本次执行）。
# 已知不覆盖的两种盘，宁可如实说明也不用秒级余量把所有人的精度赔进去：
# FAT/exFAT 的修改时间是 2 秒粒度（开头 ~2s 内的产物可能漏报）；网络盘（NFS/SMB）
# 若服务端时钟落后于本机，任何余量都救不回来。执行前就存在且未变动的文件在哪种盘上
# 都不会命中——它们的 mtime 是旧的。
_MTIME_MARGIN_NS = 100_000_000


@dataclass(frozen=True)
class WrittenScanResult:
    """一次事后扫描的结果。``truncated`` = 撞了预算，深处可能还有没看到的改动。"""

    files: list[str]
    truncated: bool = False


def written_scan_cutoff_ns() -> int:
    """执行开始前取的 mtime 截止值（已含 :data:`_MTIME_MARGIN_NS` 余量）。"""
    return time.time_ns() - _MTIME_MARGIN_NS


def scan_written_files(
    root: Path,
    *,
    cutoff_ns: int,
    max_dirs: int = _MAX_DIRS,
    budget_ms: float = _BUDGET_MS,
    max_files: int = _MAX_FILES,
) -> WrittenScanResult:
    """``root`` 下 mtime 不早于 ``cutoff_ns`` 的常规文件（工作区相对 POSIX 路径）。

    同步阻塞（大量 ``scandir``），调用方须放到线程里跑。任何单条目的 ``OSError``
    （权限 / 占用 / 竞态删除）只跳过该条目，绝不让整次扫描失败——执行本身已经成功了。
    """
    deadline = time.monotonic() + budget_ms / 1000.0
    queue: deque[tuple[str, str]] = deque([("", str(root))])
    found: list[str] = []
    dirs_seen = 0
    truncated = False

    while queue:
        if dirs_seen >= max_dirs or time.monotonic() >= deadline:
            truncated = True
            break
        parent_rel, abs_dir = queue.popleft()
        dirs_seen += 1
        try:
            entries = list(os.scandir(abs_dir))
        except OSError:
            continue
        for entry in entries:
            child_rel = f"{parent_rel}/{entry.name}" if parent_rel else entry.name
            try:
                # follow_symlinks=False 两处都给：符号链接既不入队也不上报。
                if entry.is_dir(follow_symlinks=False):
                    # 等价于 ``is_ignored_dir_entry``：既然从不下潜进噪音目录，
                    # 它那趟「祖先段是否噪音」的复检在这里恒为假，省掉。
                    if is_ignored_dir_name(entry.name) or is_internal_zone_relpath(
                        child_rel
                    ):
                        continue
                    queue.append((child_rel, entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                if is_system_ignored_file_name(entry.name):
                    continue
                if entry.stat(follow_symlinks=False).st_mtime_ns >= cutoff_ns:
                    found.append(child_rel)
            except OSError:
                continue
        if len(found) >= max_files:
            truncated = True
            break

    found.sort()
    return WrittenScanResult(files=found[:max_files], truncated=truncated)
