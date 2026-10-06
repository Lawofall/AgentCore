/**
 * Local browser 只读 console / load-error 环形缓冲（纯逻辑，无 electron）。
 * 与 sandbox driver 语义对齐：硬上限 + 截断，禁止回传超大 blob / 疑似密钥值。
 */

export const CONSOLE_MAX_MESSAGES = 80;
export const CONSOLE_MAX_ERRORS = 40;
export const CONSOLE_MAX_TEXT = 500;
export const CONSOLE_MAX_STACK = 1500;

const _SECRET_RE =
  /(password|passwd|pwd|token|secret|authorization)\s*[:=]\s*\S+/gi;

export type ConsoleMessageEntry = {
  level: string;
  text: string;
  timestamp: number;
};

export type ConsoleErrorEntry = {
  message: string;
  stack?: string;
  timestamp: number;
};

export type ConsoleBufferSnapshot = {
  messages: ConsoleMessageEntry[];
  errors: ConsoleErrorEntry[];
  truncated: {
    messages_dropped: number;
    errors_dropped: number;
  };
};

/**
 * Electron 宿主打进页面 console 的安全警告（无 CSP / unsafe-eval）。
 * 不是页面脚本的错误；留在缓冲里会被模型当成页面故障。
 */
export function isElectronHostSecurityWarning(text: string): boolean {
  return text.includes("Electron Security Warning");
}

function looksLikeBlob(text: string): boolean {
  if (text.length < 400) return false;
  if (text.length > 4_000) return true;
  // Base64 / data-URL payloads tend to be long runs without whitespace.
  const sample = text.slice(0, 240);
  if (/\s/.test(sample)) return false;
  const compact = sample.replace(/\s+/g, "");
  return /^[A-Za-z0-9+/=_-]+$/.test(compact) && compact.length >= 200;
}

/** 截断 + 轻量脱敏（疑似 password=/token= 值；超大 blob）。 */
export function scrubConsoleText(
  raw: unknown,
  maxLen = CONSOLE_MAX_TEXT,
): string {
  let t = String(raw ?? "");
  t = t.replace(_SECRET_RE, (_m, key: string) => `${key}=[redacted]`);
  if (looksLikeBlob(t)) {
    return `${t.slice(0, 80)}…[truncated blob]`;
  }
  if (t.length <= maxLen) return t;
  return `${t.slice(0, Math.max(0, maxLen - 1))}…`;
}

export function normalizeConsoleLevel(level: unknown): string {
  if (typeof level === "string" && level.trim())
    return level.trim().toLowerCase();
  if (typeof level === "number" && Number.isFinite(level)) {
    // Electron legacy: 0=verbose, 1=info, 2=warning, 3=error
    return (["verbose", "info", "warning", "error"] as const)[level] ?? "info";
  }
  return "info";
}

export class ConsoleRingBuffer {
  private readonly messages: ConsoleMessageEntry[] = [];
  private readonly errors: ConsoleErrorEntry[] = [];
  private messagesDropped = 0;
  private errorsDropped = 0;

  pushMessage(
    level: unknown,
    text: unknown,
    timestamp: number = Date.now() / 1000,
  ): void {
    if (isElectronHostSecurityWarning(String(text ?? ""))) return;
    const entry: ConsoleMessageEntry = {
      level: normalizeConsoleLevel(level),
      text: scrubConsoleText(text),
      timestamp,
    };
    if (this.messages.length >= CONSOLE_MAX_MESSAGES) {
      this.messages.shift();
      this.messagesDropped += 1;
    }
    this.messages.push(entry);
  }

  pushError(
    message: unknown,
    stack?: unknown,
    timestamp: number = Date.now() / 1000,
  ): void {
    const entry: ConsoleErrorEntry = {
      message: scrubConsoleText(message),
      timestamp,
    };
    if (stack != null && String(stack).trim()) {
      entry.stack = scrubConsoleText(stack, CONSOLE_MAX_STACK);
    }
    if (this.errors.length >= CONSOLE_MAX_ERRORS) {
      this.errors.shift();
      this.errorsDropped += 1;
    }
    this.errors.push(entry);
  }

  snapshot(): ConsoleBufferSnapshot {
    return {
      messages: this.messages.map((m) => ({ ...m })),
      errors: this.errors.map((e) => ({ ...e })),
      truncated: {
        messages_dropped: this.messagesDropped,
        errors_dropped: this.errorsDropped,
      },
    };
  }
}
