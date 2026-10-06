import { execFileSync } from "node:child_process";

/** Desktop electron-vite origin. Listed in the API CORS allowlist; do not walk. */
export const DESKTOP_DEV_PORT = 5173;

const LISTEN_FOREIGN = new Set(["0.0.0.0:0", "[::]:0", "*:*"]);

function localPort(addr) {
  if (addr.startsWith("[")) {
    const mark = addr.lastIndexOf("]:");
    if (mark === -1) return null;
    return parsePort(addr.slice(mark + 2));
  }
  const mark = addr.lastIndexOf(":");
  if (mark === -1) return null;
  return parsePort(addr.slice(mark + 1));
}

function parsePort(text) {
  if (!/^[0-9]+$/.test(text)) return null;
  const port = Number(text);
  return Number.isInteger(port) ? port : null;
}

function isListening(state, foreign) {
  if (LISTEN_FOREIGN.has(foreign)) return true;
  if (state === "侦听") return true;
  return state.toUpperCase() === "LISTENING";
}

/**
 * PIDs listening on `port` from `netstat -ano -p tcp`.
 * Matches the port as a whole token so 15173 is not 5173.
 * Treats foreign `0.0.0.0:0` / `[::]:0` as listening so a localized
 * state word still counts.
 */
export function parseWindowsNetstat(text, port) {
  const pids = [];
  for (const line of String(text).split(/\r?\n/)) {
    const parts = line.trim().split(/\s+/);
    if (parts.length < 5 || parts[0].toUpperCase() !== "TCP") continue;
    if (localPort(parts[1]) !== port) continue;
    if (!isListening(parts[3], parts[2])) continue;
    const pid = Number(parts[4]);
    if (!Number.isInteger(pid) || pid <= 0 || pids.includes(pid)) continue;
    pids.push(pid);
  }
  return pids;
}

/** PIDs from `lsof -Fp` (one `p<pid>` line per process). */
export function parseLsofPidOutput(text) {
  const pids = [];
  for (const line of String(text).split(/\r?\n/)) {
    const match = /^p(\d+)$/.exec(line.trim());
    if (!match) continue;
    const pid = Number(match[1]);
    if (!pids.includes(pid)) pids.push(pid);
  }
  return pids;
}

/** Image name from `tasklist /FO CSV /NH`, or null when the row is absent. */
export function parseTasklistCsv(text) {
  for (const line of String(text).split(/\r?\n/)) {
    const match = /^"([^"]+)"/.exec(line.trim());
    if (match) return match[1];
  }
  return null;
}

function assertPort(port) {
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`invalid port: ${port}`);
  }
}

function run(command, args) {
  try {
    return execFileSync(command, args, {
      encoding: "utf8",
      windowsHide: true,
      timeout: 5000,
    });
  } catch (err) {
    if (err && typeof err.stdout === "string" && err.stdout.length > 0) {
      return err.stdout;
    }
    return null;
  }
}

export function findListeningPids(port, platform = process.platform) {
  assertPort(port);
  if (platform === "win32") {
    const text = run("netstat", ["-ano", "-p", "tcp"]);
    if (text == null) return null;
    return parseWindowsNetstat(text, port);
  }
  const text = run("lsof", ["-nP", `-iTCP:${port}`, "-sTCP:LISTEN", "-Fp"]);
  if (text == null) return null;
  return parseLsofPidOutput(text);
}

export function processName(pid, platform = process.platform) {
  if (!Number.isInteger(pid) || pid <= 0) return null;
  if (platform === "win32") {
    const text = run("tasklist", [
      "/FI",
      `PID eq ${pid}`,
      "/FO",
      "CSV",
      "/NH",
    ]);
    if (text == null) return null;
    return parseTasklistCsv(text);
  }
  const text = run("ps", ["-p", String(pid), "-o", "comm="]);
  if (text == null) return null;
  const name = text.trim();
  return name || null;
}

export function formatPortBusy(port, occupants) {
  const who = occupants
    .map((item) =>
      item.name ? `${item.name}（pid ${item.pid}）` : `pid ${item.pid}`,
    )
    .join("、");
  return (
    `桌面开发端口 ${port} 已被占用：${who}。` +
    `electron-vite 固定使用 ${port}，不会改到别的端口。先停掉占用进程再启动。`
  );
}

/**
 * Throw when something is already listening on the desktop dev port.
 * A failed lookup (netstat/lsof missing) returns without throwing; Vite
 * `strictPort` still refuses the bind.
 *
 * @param {number} [port]
 * @param {{ findListeningPids?: typeof findListeningPids, processName?: typeof processName }} [deps]
 */
export function assertDesktopDevPortFree(port = DESKTOP_DEV_PORT, deps = {}) {
  const find = deps.findListeningPids ?? findListeningPids;
  const nameOf = deps.processName ?? processName;
  const pids = find(port);
  if (pids == null || pids.length === 0) return;
  const occupants = pids.map((pid) => ({ pid, name: nameOf(pid) }));
  throw new Error(formatPortBusy(port, occupants));
}
