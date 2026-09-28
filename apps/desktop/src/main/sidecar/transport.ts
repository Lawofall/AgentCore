import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { app } from "electron";

// --- 传输层（与 child_process 解耦，便于单测）---

/** sidecar 进程的双向行管道：发一行、收一行、感知断开、关闭。 */
export interface Transport {
  /** 写一行（已含结尾 `\n`）到子进程 stdin。 */
  send(line: string): void;
  /** 注册「收到一整行 stdout」回调（行内不含结尾 `\n`）。 */
  onLine(cb: (line: string) => void): void;
  /** 注册「进程退出 / 出错」回调（err 为退出原因，正常退出可为 undefined）。 */
  onClose(cb: (err?: Error) => void): void;
  /** 终止进程。 */
  close(): void;
}

export interface SpawnConfig {
  cmd: string;
  args: string[];
  cwd: string;
  /** 额外环境变量，合并覆盖继承的环境（如内置运行时旁路 site-packages 的 `PYTHONPATH`）。 */
  env?: Record<string, string>;
}

/**
 * 解析拉起 sidecar 的命令。
 *
 * **打包态**（`app.isPackaged`）走**内置 Python 运行时**（双模式工作区 §十「内置 Python 打包」
 * 方案 B）：随包带一份独立 CPython 发行版 + `--target` 装好的 site-packages（构建见
 * `scripts/bundle-sidecar.mjs`），由 electron-builder `extraResources` 拷到
 * `process.resourcesPath/sidecar`。用户机器**无需任何系统 Python / venv / uv**；引擎包不在
 * 解释器自带 site-packages、而在旁路目录，故经 `PYTHONPATH` 注入（免 venv 重定位之痛）。
 *
 * **dev 态**（未打包）保持原行为：服务端 venv 的 python > `uv run python`。`AGENTCORE_SIDECAR_CMD`
 * 覆写**始终最高优先**（即便已打包），便于对一个打包后的应用临时指向自定义解释器联调。
 * 服务端目录默认取 `AGENTCORE_SERVER_DIR`，否则按 app 路径推 `../server`（dev 下 appPath =
 * apps/desktop）。
 *
 * @internal 导出供单测；生产路径经 SidecarManager → spawnFn。
 */
/** 本机 sidecar 的服务端目录（dev：`apps/server`）。打包态仍解析，供测试覆写。 */
export function resolveSidecarServerDir(): string {
  return (
    process.env.AGENTCORE_SERVER_DIR ?? join(app.getAppPath(), "..", "server")
  );
}

export function resolveSpawnConfig(): SpawnConfig {
  const serverDir = resolveSidecarServerDir();

  const cmdOverride = process.env.AGENTCORE_SIDECAR_CMD;
  if (cmdOverride) {
    const args = (process.env.AGENTCORE_SIDECAR_ARGS ?? "-m agentcore.sidecar")
      .split(" ")
      .filter(Boolean);
    return { cmd: cmdOverride, args, cwd: serverDir };
  }

  // 打包态：内置 Python 运行时（方案 B）。
  if (app.isPackaged) {
    const base = join(process.resourcesPath, "sidecar");
    // unix：优先版本化二进制（与 bundle-sidecar.mjs 的 PYTHON_VERSION=3.13 对齐），
    // 避免依赖可能仍是坏绝对 symlink 的 python3（历史包曾指向 CI 的 uv 缓存路径）。
    const python =
      process.platform === "win32"
        ? join(base, "python", "python.exe")
        : (() => {
            const versioned = join(base, "python", "bin", "python3.13");
            if (existsSync(versioned)) return versioned;
            return join(base, "python", "bin", "python3");
          })();
    return {
      cmd: python,
      args: ["-m", "agentcore.sidecar"],
      cwd: base,
      env: {
        PYTHONPATH: join(base, "site-packages"),
        AGENTCORE_RG_PATH: join(
          process.resourcesPath,
          "rg",
          process.platform === "win32" ? "rg.exe" : "rg",
        ),
      },
    };
  }

  // dev：服务端 venv 的 python（最稳，免 uv 的 PATH 问题）。
  const venvPython =
    process.platform === "win32"
      ? join(serverDir, ".venv", "Scripts", "python.exe")
      : join(serverDir, ".venv", "bin", "python");
  // 与打包态同一份 resources/rg。始终注入：文件不在就让 grep 显式失败，
  // 不改落到 apps/server/bin（那只给 pytest / 本地 API）。
  const rgEnv = {
    AGENTCORE_RG_PATH: join(
      app.getAppPath(),
      "resources",
      "rg",
      process.platform === "win32" ? "rg.exe" : "rg",
    ),
  };
  if (existsSync(venvPython)) {
    return {
      cmd: venvPython,
      args: ["-m", "agentcore.sidecar"],
      cwd: serverDir,
      env: rgEnv,
    };
  }
  // 回退：让 uv 解析环境（需 uv 在 PATH）。
  return {
    cmd: "uv",
    args: ["run", "python", "-m", "agentcore.sidecar"],
    cwd: serverDir,
    env: rgEnv,
  };
}

/** 从累积的 stderr 提取用户可见的失败原因（优先 Python ImportError / 末行异常）。 */
export function formatSidecarExitError(
  code: number | null,
  stderr: string,
): Error {
  const trimmed = stderr.trim();
  if (trimmed) {
    const importErr = trimmed.match(/^ImportError:\s*(.+)$/m);
    if (importErr) {
      return new Error(`sidecar 启动失败：${importErr[1].trim()}`);
    }
    const lines = trimmed
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    for (let i = lines.length - 1; i >= 0; i--) {
      const line = lines[i];
      if (!line) continue;
      if (/^(?:\w+Error|\w+Exception):\s*.+/.test(line)) {
        return new Error(`sidecar 启动失败：${line}`);
      }
    }
  }
  if (code) {
    return new Error(`sidecar 进程退出（code ${code}）`);
  }
  return new Error("sidecar 进程已退出");
}

/** Strip SOCKS proxy env so sidecar httpx does not require optional ``socksio``.
 *
 * Desktop inherits the user shell env (Clash/V2Ray often set ``ALL_PROXY=socks5://…``).
 * Product egress uses ``trust_env=False``; scrubbing here is defense-in-depth for any
 * library that still reads proxy env. HTTP(S) proxies are left intact.
 */
export function scrubSocksProxyEnv(env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const out: NodeJS.ProcessEnv = { ...env };
  const keys = [
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
  ] as const;
  for (const key of keys) {
    const raw = out[key];
    if (typeof raw === "string" && /^socks5h?:\/\//i.test(raw.trim())) {
      delete out[key];
    }
  }
  return out;
}

/** 真实传输：spawn 子进程，把 stdout 按行切分，stderr 透传到主进程控制台（dev 可见）。 */
export function spawnTransport(config: SpawnConfig): Transport {
  const child = spawn(config.cmd, config.args, {
    cwd: config.cwd,
    env: scrubSocksProxyEnv({
      ...process.env,
      PYTHONUTF8: "1",
      PYTHONIOENCODING: "utf-8",
      ...config.env,
      // Bridge creds are per-turn via RPC (currentBrowserBridge), not spawn env.
    }),
    stdio: ["pipe", "pipe", "pipe"],
  });
  child.stdin.setDefaultEncoding("utf-8");
  child.stdout.setEncoding("utf-8");
  child.stderr.setEncoding("utf-8");

  let lineCb: ((line: string) => void) | null = null;
  let closeCb: ((err?: Error) => void) | null = null;
  let buffer = "";
  let stderrBuf = "";

  child.stdout.on("data", (chunk: string) => {
    buffer += chunk;
    let idx = buffer.indexOf("\n");
    while (idx >= 0) {
      const line = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 1);
      if (line.trim()) lineCb?.(line);
      idx = buffer.indexOf("\n");
    }
  });
  // sidecar 的日志（structlog）走 stderr——透传到主进程控制台，便于 dev 排查。
  child.stderr.on("data", (chunk: string) => {
    stderrBuf += chunk;
    for (const l of chunk.split("\n")) {
      if (l.trim()) console.error(`[sidecar] ${l}`);
    }
  });
  child.on("error", (err) => closeCb?.(err));
  child.on("close", (code) =>
    closeCb?.(code ? formatSidecarExitError(code, stderrBuf) : undefined),
  );

  return {
    send: (line) => {
      child.stdin.write(line);
    },
    onLine: (cb) => {
      lineCb = cb;
    },
    onClose: (cb) => {
      closeCb = cb;
    },
    close: () => {
      child.kill();
    },
  };
}
