// @ts-check
/**
 * 为本机平台拉取内嵌 ripgrep（产品 AI grep）。
 *
 * 产物：
 * - `apps/desktop/resources/rg/rg[.exe]`（gitignore）：主进程 opGrep 与 sidecar 共用
 * - `apps/server/bin/rg[.exe]`：仅 pytest / 本地 API；sidecar 不读这里
 *
 * 也可直接：`python apps/server/scripts/fetch_ripgrep.py --install-desktop`
 */
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const desktopDir = resolve(here, "..");
const serverDir = resolve(desktopDir, "..", "server");
const fetchScript = join(serverDir, "scripts", "fetch_ripgrep.py");
const isWin = process.platform === "win32";

function runPython(args) {
  const venvPy = isWin
    ? join(serverDir, ".venv", "Scripts", "python.exe")
    : join(serverDir, ".venv", "bin", "python");
  if (existsSync(venvPy)) {
    console.log(`$ ${venvPy} ${args.join(" ")}`);
    execFileSync(venvPy, args, { cwd: serverDir, stdio: "inherit" });
  } else {
    console.log(`$ uv run python ${args.join(" ")}`);
    execFileSync("uv", ["run", "python", ...args], {
      cwd: serverDir,
      stdio: "inherit",
    });
  }
}

function main() {
  if (!existsSync(fetchScript)) {
    throw new Error(`missing ${fetchScript}`);
  }
  runPython([fetchScript, "--install-desktop"]);
  runPython([fetchScript, "--install-server"]);
}

main();
