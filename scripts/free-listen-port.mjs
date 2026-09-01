#!/usr/bin/env node
/**
 * Free listen ports occupied by this repo's Vite / smoke leftovers.
 *
 * Only kills listeners whose command line mentions AgentCore **and** looks like
 * vite.webapp / smoke-webapp / vite — never blind-kills arbitrary node.
 *
 *   pnpm ports:free                 # defaults: 5174 5175 5199
 *   pnpm ports:free -- 5174 5175
 *   node scripts/free-listen-port.mjs 5174
 *
 * Import: `import { freeListenPorts } from "./free-listen-port.mjs"`
 */
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

/** Desktop / smoke / preview ports commonly left behind after a crashed run. */
export const DEFAULT_PORTS = [5174, 5175, 5199];

/** Must appear in cmdline (repo path / package name). */
const REPO_RE = /agentcore/i;

/**
 * Vite / smoke fingerprints — require at least one alongside REPO_RE.
 * Keep tight: bare "node" alone is never enough.
 */
const SCRIPT_RE =
  /smoke-webapp|vite\.webapp|vite\.web\.config|vite\.config|[\\/]vite[\\/]|[\\/]vite\.js\b|node_modules[\\/]vite\b/i;

/**
 * @param {number[]} ports
 * @returns {{ port: number, pid: number, cmd: string }[]}
 */
export function freeListenPorts(ports = DEFAULT_PORTS) {
  const unique = [
    ...new Set(ports.map(Number).filter((p) => Number.isInteger(p) && p > 0)),
  ];
  /** @type {{ port: number, pid: number, cmd: string }[]} */
  const killed = [];

  for (const port of unique) {
    const pids = listListenerPids(port);
    if (pids.length === 0) {
      console.log(`[ports:free] :${port} — free`);
      continue;
    }

    for (const pid of pids) {
      if (pid === process.pid || pid <= 0) continue;
      const cmd = getCommandLine(pid);
      if (!isRepoViteListener(cmd)) {
        console.log(
          `[ports:free] :${port} pid=${pid} — skip (not AgentCore vite/smoke):\n  ${truncate(cmd)}`,
        );
        continue;
      }
      if (killPid(pid)) {
        console.log(
          `[ports:free] :${port} killed pid=${pid}\n  ${truncate(cmd)}`,
        );
        killed.push({ port, pid, cmd });
      } else {
        console.warn(
          `[ports:free] :${port} pid=${pid} — kill failed\n  ${truncate(cmd)}`,
        );
      }
    }
  }

  return killed;
}

/**
 * @param {string} cmd
 */
function isRepoViteListener(cmd) {
  if (!cmd) return false;
  return REPO_RE.test(cmd) && SCRIPT_RE.test(cmd);
}

/**
 * @param {string} s
 * @param {number} [max]
 */
function truncate(s, max = 240) {
  const one = (s || "(no cmdline)").replace(/\s+/g, " ").trim();
  return one.length > max ? `${one.slice(0, max)}…` : one;
}

/**
 * @param {number} port
 * @returns {number[]}
 */
function listListenerPids(port) {
  return process.platform === "win32"
    ? listListenerPidsWin(port)
    : listListenerPidsUnix(port);
}

/**
 * netstat is the most reliable Win listener → PID source (no admin required).
 * @param {number} port
 */
function listListenerPidsWin(port) {
  // No `-p TCP`: that filter drops IPv6 listeners (`[::1]:5174`), so a
  // dual-stack / v6-only Vite looks "free" and the next strictPort bind fails.
  const r = spawnSync("netstat", ["-ano"], {
    encoding: "utf8",
    windowsHide: true,
  });
  if (r.error || r.status !== 0) {
    console.warn(
      `[ports:free] netstat failed: ${r.error?.message ?? r.stderr ?? r.status}`,
    );
    return [];
  }
  const pids = new Set();
  // TCP    0.0.0.0:5174    0.0.0.0:0    LISTENING    12345
  // TCP    [::]:5174       [::]:0       LISTENING    12345
  const re = new RegExp(`:${port}\\s+\\S+\\s+LISTENING\\s+(\\d+)`, "i");
  for (const line of (r.stdout ?? "").split(/\r?\n/)) {
    const m = line.match(re);
    if (m) pids.add(Number(m[1]));
  }
  return [...pids];
}

/**
 * @param {number} port
 */
function listListenerPidsUnix(port) {
  const lsof = spawnSync(
    "lsof",
    ["-nP", `-iTCP:${port}`, "-sTCP:LISTEN", "-t"],
    { encoding: "utf8" },
  );
  if (lsof.status === 0 && (lsof.stdout ?? "").trim()) {
    return [
      ...new Set(
        (lsof.stdout ?? "")
          .trim()
          .split(/\r?\n/)
          .map((s) => Number(s.trim()))
          .filter((n) => Number.isInteger(n) && n > 0),
      ),
    ];
  }

  // ss fallback (Linux without lsof)
  const ss = spawnSync("ss", ["-lptn", `sport = :${port}`], {
    encoding: "utf8",
  });
  if (ss.status === 0 && ss.stdout) {
    const pids = new Set();
    for (const m of ss.stdout.matchAll(/pid=(\d+)/g)) {
      pids.add(Number(m[1]));
    }
    return [...pids];
  }

  if (lsof.error || (lsof.status !== 0 && ss.status !== 0)) {
    console.warn(
      `[ports:free] could not list listeners on :${port} (need lsof or ss)`,
    );
  }
  return [];
}

/**
 * @param {number} pid
 */
function getCommandLine(pid) {
  if (process.platform === "win32") return getCommandLineWin(pid);
  return getCommandLineUnix(pid);
}

/**
 * @param {number} pid
 */
function getCommandLineWin(pid) {
  const r = spawnSync(
    "powershell.exe",
    [
      "-NoProfile",
      "-NonInteractive",
      "-Command",
      `(Get-CimInstance Win32_Process -Filter "ProcessId=${pid}").CommandLine`,
    ],
    { encoding: "utf8", windowsHide: true },
  );
  const out = (r.stdout ?? "").trim();
  if (out) return out;
  // Fallback: ExecutablePath only (weaker — may skip if path lacks AgentCore)
  const r2 = spawnSync(
    "powershell.exe",
    [
      "-NoProfile",
      "-NonInteractive",
      "-Command",
      `(Get-CimInstance Win32_Process -Filter "ProcessId=${pid}").ExecutablePath`,
    ],
    { encoding: "utf8", windowsHide: true },
  );
  return (r2.stdout ?? "").trim();
}

/**
 * @param {number} pid
 */
function getCommandLineUnix(pid) {
  try {
    return readFileSync(`/proc/${pid}/cmdline`, "utf8")
      .replace(/\0/g, " ")
      .trim();
  } catch {
    const r = spawnSync("ps", ["-p", String(pid), "-o", "args="], {
      encoding: "utf8",
    });
    return (r.stdout ?? "").trim();
  }
}

/**
 * @param {number} pid
 */
function killPid(pid) {
  if (process.platform === "win32") {
    const r = spawnSync("taskkill", ["/PID", String(pid), "/F"], {
      encoding: "utf8",
      windowsHide: true,
    });
    return r.status === 0;
  }
  try {
    process.kill(pid, "SIGTERM");
  } catch {
    return false;
  }
  try {
    process.kill(pid, 0);
    process.kill(pid, "SIGKILL");
  } catch {
    // ESRCH = already gone — success
  }
  return true;
}

function isDirectRun() {
  const entry = process.argv[1];
  if (!entry) return false;
  try {
    return import.meta.url === pathToFileURL(resolve(entry)).href;
  } catch {
    return false;
  }
}

function main() {
  const args = process.argv.slice(2).filter((a) => a !== "--");
  const ports =
    args.length > 0
      ? args.map(Number).filter((n) => Number.isInteger(n) && n > 0)
      : DEFAULT_PORTS;
  if (args.length > 0 && ports.length === 0) {
    console.error("usage: node scripts/free-listen-port.mjs [port...]");
    process.exit(2);
  }
  freeListenPorts(ports);
}

if (isDirectRun()) {
  main();
}
