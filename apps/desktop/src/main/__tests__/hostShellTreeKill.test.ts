/**
 * `host_shell` 超时路径 —— 杀掉整棵进程树，且仍然立刻回话。
 *
 * 跑的是任意主机命令：AI 让它 `npm install` 或起 dev server，孤儿就留在用户机器上
 * 占端口占 CPU。修复前只 SIGKILL shell 自己。本路径与 `code_execute` 不同——它超时
 * 时先标 settled 再立即回话、不等 `close`，所以这里同时钉住「立刻回话」。
 */
import { mkdtemp, realpath, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({
  app: { getPath: () => tmpdir() },
}));

vi.mock("../log-service", () => ({
  logDesktop: vi.fn(),
}));

import { hostShell } from "../host/shell";

const sleep = (ms: number): Promise<void> =>
  new Promise((r) => setTimeout(r, ms));

/** 孙进程：每 50ms 追加一个字节——文件不再增长 = 它已经停了。 */
const GRANDCHILD_JS = `
const fs = require("node:fs");
const beat = process.argv[2];
setInterval(() => {
  try {
    fs.appendFileSync(beat, ".");
  } catch {}
}, 50);
`;

/**
 * 让 shell 派生一个**在本平台上真能熬过「只杀直接子进程」**的孙进程，然后自己挂住
 * 到超时之后——否则没有杀树也能过测。
 *
 * Windows：普通孙进程会随父进程那个隐藏控制台一起消失，所以要模拟的幸存者是
 * `Start-Process` 拉起的 detached 助手（`taskkill /T` 仍能顺父子链找到它）。命令只用
 * `;` 分隔，避开 host_shell 对 `&&` / `%VAR%` 的 PowerShell 惯用法拦截。
 * POSIX：`&` 后台作业留在 shell 的进程组里，`killpg` 够得着，而单杀 shell 够不着。
 */
function survivorCommand(
  node: string,
  grandScript: string,
  beat: string,
): string {
  if (process.platform === "win32") {
    return [
      `Start-Process -FilePath '${node}' -ArgumentList '${grandScript}','${beat}' -WindowStyle Hidden`,
      "Start-Sleep -Seconds 30",
    ].join("; ");
  }
  return `'${node}' '${grandScript}' '${beat}' & sleep 30`;
}

async function beatSize(path: string): Promise<number> {
  try {
    return (await stat(path)).size;
  } catch {
    return 0;
  }
}

/**
 * 轮询到心跳「出现且在增长」为止。
 *
 * 不写死采样点：PowerShell 启动 + `Start-Process` + node 启动的抖动能吃掉一秒多，
 * 固定 700ms 采样会假阴性。
 */
async function heartbeatGrows(
  path: string,
  budgetMs: number,
): Promise<boolean> {
  const deadline = Date.now() + budgetMs;
  let first = 0;
  while (Date.now() < deadline) {
    const size = await beatSize(path);
    if (size > 0) {
      if (first === 0) first = size;
      else if (size > first) return true;
    }
    await sleep(100);
  }
  return false;
}

describe("hostShell timeout kills the process tree", () => {
  let dir: string;
  let grandScript: string;
  let beat: string;

  beforeEach(async () => {
    dir = await realpath(await mkdtemp(join(tmpdir(), "host-shell-tree-")));
    grandScript = join(dir, "grand.cjs");
    beat = join(dir, "beat.txt");
    await writeFile(grandScript, GRANDCHILD_JS, "utf-8");
  });
  afterEach(async () => {
    // Windows：刚被杀的进程还攥着句柄一小会儿（rmdir EBUSY），重试几次即可。
    await rm(dir, {
      recursive: true,
      force: true,
      maxRetries: 10,
      retryDelay: 100,
    });
  });

  it("reaps grandchildren and still answers immediately with the timeout envelope", async () => {
    const startedMs = Date.now();
    const running = hostShell(
      survivorCommand(process.execPath, grandScript, beat),
      6,
    );

    // 先证明孙进程真的在跑（6s 超时之内），否则「已经死了」会空过。
    expect(await heartbeatGrows(beat, 4_500)).toBe(true);

    const result = await running;
    // shell 自己要睡 30s，所以这次返回只能来自「超时立刻回话」，不是等 close。
    // Windows 上 `Start-Process` 会命中 looksLikeGuiLaunch，窗口快照也算在里面。
    const elapsed = Date.now() - startedMs;
    expect(elapsed).toBeGreaterThanOrEqual(6_000);
    expect(elapsed).toBeLessThan(15_000);

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.timed_out).toBe(true);
      expect(result.value.exit_code).toBeNull();
      expect(result.value.note).toBe("killed after 6s");
      expect(typeof result.value.stdout).toBe("string");
      expect(typeof result.value.stderr).toBe("string");
      expect(result.value.cwd).toBeTruthy();
      expect(result.value.obs_env).toBeTruthy();
    }

    await sleep(500);
    const afterKill = await beatSize(beat);
    await sleep(800);
    expect(await beatSize(beat)).toBe(afterKill);
  }, 40_000);

  it("keeps the normal close path intact", async () => {
    const command =
      process.platform === "win32" ? "Write-Output 'tree-ok'" : "echo tree-ok";
    const result = await hostShell(command, 20);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.timed_out).toBe(false);
      expect(result.value.exit_code).toBe(0);
      expect(String(result.value.stdout)).toContain("tree-ok");
    }
  }, 30_000);
});
