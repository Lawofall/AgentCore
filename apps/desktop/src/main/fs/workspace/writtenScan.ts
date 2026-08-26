/**
 * 产物写回 · 本地执行腿：扫描「本次执行改了工作区哪些文件」。
 *
 * 服务端 `tools/sandbox/written_scan.py` 的镜像实现（同一口径，逐条对应；改一侧须同步
 * 另一侧）。云端 gVisor 与本机一样 bind 落盘，改动靠事后扫描；本机执行（本模块）与
 * sidecar 直接以工作区为 cwd 让脚本写盘，不事后看盘就没人能如实填
 * `ExecutionResult.written_files` —— 桌面用户跑脚本产出的文件于是全部不进交付物台账
 * （不上交付 artifacts、不进用户面路径、CEO 清单也看不见）。
 *
 * 为什么是「执行后单次有界扫描 + mtime 截止」，而不是执行前后各拍一次全树快照：
 *
 * - 成本几乎全在**目录枚举**次数上，与文件数关系不大。实测一个大仓（剪掉 node_modules /
 *   .git 后仍有 19.5k 目录 / 196k 文件）：整树约 1 秒，只枚举目录不 stat 也要 0.9 秒。
 *   前后两次快照 = 双倍，会把一条 100ms 的脚本拖成两秒。
 * - 有界：从 cwd 起 **BFS**，撞到 {@link SCAN_MAX_DIRS} 或 {@link SCAN_BUDGET_MS} 立刻
 *   收手。BFS 保证被截断的永远是最深的尾巴，而产物几乎都落在 cwd 或浅层子目录；于是
 *   「超大工作区」退化成「浅层如实、极深处可能漏报」，而不是每次执行整体变慢。
 *
 * 排除面复用 `workspaceIgnore`（不另起第三份名单）：路径感知旁路区
 * `AgentCore/{index,trash,baselines}`、系统噪音目录、系统噪音后缀（`*.db` / `*.pyc`）、
 * 符号链接。**不**排除 AI 噪音后缀——AI 生成的 `.png` 图表正是交付物。
 */
import { promises as fs } from "node:fs";
import { join, relative } from "node:path";
import type { WorkspaceOpResult } from "@shared/ipc-contract";
import {
  shouldSkipDirName,
  shouldSkipSystemFileName,
} from "../workspaceIgnore";
import { toPosix } from "./result";

/** 目录枚举上限（实测约 45µs/目录，4000 目录 ≈ 180ms，与墙钟预算同量级）。 */
export const SCAN_MAX_DIRS = 4000;
/** 墙钟兜底：网络盘 / 被杀毒软件挂钩的目录上单次 readdir 可能慢几个数量级。 */
export const SCAN_BUDGET_MS = 250;
/** 报告条数上限（与服务端 `_MAX_FILES` 取齐）。 */
export const SCAN_MAX_FILES = 200;

/**
 * mtime 截止余量。文件系统时间戳粒度比墙钟粗且**向下截断**：刚起步就写的文件，mtime
 * 可能落在启动时刻之前一点（实测 Windows/NTFS ~1ms；Linux 粗粒度时钟 ≤10ms）。让
 * 100ms 即十倍余量，何况解释器启动本身就要几十毫秒。
 *
 * 不让更多：余量就是误报窗口（窗口内**别人**刚改过的文件会被算进本次执行）。已知不覆盖
 * FAT/exFAT（修改时间 2 秒粒度）与时钟落后的网络盘——宁可如实说明，不用秒级余量把所有
 * 人的精度赔进去。执行前就存在且未变动的文件在哪种盘上都不会命中（mtime 是旧的）。
 */
export const SCAN_MTIME_MARGIN_MS = 100;

export function writtenScanCutoffMs(): number {
  return Date.now() - SCAN_MTIME_MARGIN_MS;
}

export type WrittenScan = { files: string[]; truncated: boolean };

/**
 * `rootAbs` 下 mtime 不早于 `cutoffMs` 的常规文件（相对 `rootAbs` 的 POSIX 路径）。
 *
 * 单条目的读取失败（权限 / 占用 / 竞态删除）只跳过该条目 —— 执行本身已经成功了，
 * 记账扫描不得把它变成失败。
 */
export async function scanWrittenFiles(
  rootAbs: string,
  cutoffMs: number,
  opts?: { maxDirs?: number; budgetMs?: number; maxFiles?: number },
): Promise<WrittenScan> {
  const maxDirs = opts?.maxDirs ?? SCAN_MAX_DIRS;
  const maxFiles = opts?.maxFiles ?? SCAN_MAX_FILES;
  const deadline = Date.now() + (opts?.budgetMs ?? SCAN_BUDGET_MS);
  const queue: Array<{ abs: string; rel: string }> = [
    { abs: rootAbs, rel: "" },
  ];
  const found: string[] = [];
  let dirsSeen = 0;
  let truncated = false;

  while (queue.length > 0) {
    if (dirsSeen >= maxDirs || Date.now() >= deadline) {
      truncated = true;
      break;
    }
    const cur = queue.shift();
    if (!cur) break;
    dirsSeen += 1;
    let dirents: import("node:fs").Dirent[];
    try {
      dirents = await fs.readdir(cur.abs, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const d of dirents) {
      // 符号链接既不下潜也不上报（与服务端 staging 写回腿同口径）。
      if (d.isSymbolicLink()) continue;
      const childRel = cur.rel ? `${cur.rel}/${d.name}` : d.name;
      if (d.isDirectory()) {
        if (shouldSkipDirName(d.name, cur.rel)) continue;
        queue.push({ abs: join(cur.abs, d.name), rel: childRel });
        continue;
      }
      if (!d.isFile()) continue;
      if (shouldSkipSystemFileName(d.name)) continue;
      try {
        const st = await fs.lstat(join(cur.abs, d.name));
        if (st.mtimeMs >= cutoffMs) found.push(childRel);
      } catch {
        // 竞态删除 / 权限拒绝：漏掉这一条，不影响其余。
      }
    }
    if (found.length >= maxFiles) {
      truncated = true;
      break;
    }
  }

  found.sort();
  return { files: found.slice(0, maxFiles), truncated };
}

/** 超时 / 起不来的信封统一用 `exit_code: -1`（见 `exec.ts` timeoutResult）。 */
function completedOnItsOwn(value: Record<string, unknown>): boolean {
  return Number(value.exit_code) !== -1;
}

/**
 * 给一次 `execute` 的成功信封补上 `written_files`（工作区相对 POSIX 路径）。
 *
 * 只在进程**自己跑完**（任何退出码）时报：与云端 copy-out 腿同一条规则。云端跳过是因为
 * 强杀后压根没落盘；本机的写其实已经在盘上了，但把强杀可能写了一半的文件当产物广告出去
 * 更糟，于是同样闭嘴。
 *
 * 路径按**绑定根**相对返回（与 list / grep / index_files 一致，服务端
 * `LocalWorkspace._out` 再剥掉子路径前缀）；旁路区与忽略名单则按 `cwdAbs`（本工作区的
 * 有效根）判定，因为 `AgentCore/` 旁路区就挂在那一层。
 */
export async function withWrittenFiles(
  result: WorkspaceOpResult,
  opts: { rootAbs: string; cwdAbs: string; cutoffMs: number },
): Promise<WorkspaceOpResult> {
  if (!result.ok || typeof result.value !== "object" || result.value === null) {
    return result;
  }
  const value = result.value as Record<string, unknown>;
  if (!completedOnItsOwn(value)) return result;
  const scan = await scanWrittenFiles(opts.cwdAbs, opts.cutoffMs);
  const basePrefix = toPosix(relative(opts.rootAbs, opts.cwdAbs));
  const prefix = basePrefix && basePrefix !== "." ? `${basePrefix}/` : "";
  return {
    ok: true,
    value: { ...value, written_files: scan.files.map((p) => `${prefix}${p}`) },
  };
}
