import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

export async function runPowerShell(
  script: string,
  timeoutMs = 12_000,
): Promise<string> {
  const { stdout } = await execFileAsync(
    "powershell.exe",
    ["-NoProfile", "-NonInteractive", "-Command", script],
    {
      timeout: timeoutMs,
      windowsHide: true,
      encoding: "utf8",
      maxBuffer: 2_000_000,
    },
  );
  return (stdout || "").trim();
}

/** Multiline / here-string safe — avoids -Command quoting breakage. */
export async function runPowerShellEncoded(
  script: string,
  timeoutMs = 12_000,
  opts: { sta?: boolean; interactive?: boolean } = {},
): Promise<string> {
  const encoded = Buffer.from(script, "utf16le").toString("base64");
  const args = ["-NoProfile"];
  if (!opts.interactive) args.push("-NonInteractive");
  if (opts.sta) args.push("-STA");
  args.push("-EncodedCommand", encoded);
  const { stdout } = await execFileAsync("powershell.exe", args, {
    timeout: timeoutMs,
    windowsHide: !opts.interactive,
    encoding: "utf8",
    maxBuffer: 2_000_000,
  });
  return (stdout || "").trim();
}

export function parseJsonArray(raw: string): unknown[] {
  if (!raw) return [];
  const parsed = JSON.parse(raw) as unknown;
  return Array.isArray(parsed) ? parsed : [parsed];
}
