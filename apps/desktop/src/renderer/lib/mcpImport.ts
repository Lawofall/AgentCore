/**
 * One-shot import of Cursor / Claude Desktop / VS Code MCP JSON into plug drafts.
 * Not a third source of truth — the user pastes, we fill the form.
 */

export type ImportedMcpStdio = {
  transport: "stdio";
  name: string;
  command: string;
  args: string[];
  env?: Record<string, string>;
};

export type ImportedMcpHttp = {
  transport: "http";
  name: string;
  url: string;
  bearer?: string;
};

export type ImportedMcpServer = ImportedMcpStdio | ImportedMcpHttp;

export type McpImportResult = {
  servers: ImportedMcpServer[];
  errors: string[];
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringMap(value: unknown): Record<string, string> | undefined {
  const rec = asRecord(value);
  if (!rec) return undefined;
  const out: Record<string, string> = {};
  for (const [key, item] of Object.entries(rec)) {
    if (typeof item === "string") out[key] = item;
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

function bearerFromHeaders(headers: unknown): string | undefined {
  const rec = asRecord(headers);
  if (!rec) return undefined;
  for (const [key, item] of Object.entries(rec)) {
    if (key.toLowerCase() !== "authorization") continue;
    if (typeof item !== "string") continue;
    const raw = item.trim();
    const match = /^Bearer\s+(.+)$/i.exec(raw);
    return (match?.[1] ?? raw).trim() || undefined;
  }
  return undefined;
}

function parseOne(name: string, value: unknown): ImportedMcpServer | string {
  const rec = asRecord(value);
  if (!rec) return `${name}: 不是对象`;
  const url = [rec.url, rec.serverUrl, rec.httpUrl].find(
    (item) => typeof item === "string" && item.trim(),
  ) as string | undefined;
  if (url?.trim()) {
    return {
      transport: "http",
      name: name.trim() || "MCP",
      url: url.trim(),
      bearer: bearerFromHeaders(rec.headers),
    };
  }
  const command = typeof rec.command === "string" ? rec.command.trim() : "";
  if (!command) return `${name}: 需要 command 或 url`;
  const args = Array.isArray(rec.args)
    ? rec.args.map((item) => String(item))
    : [];
  return {
    transport: "stdio",
    name: name.trim() || command,
    command,
    args,
    env: stringMap(rec.env),
  };
}

function parseMap(map: Record<string, unknown>): McpImportResult {
  const servers: ImportedMcpServer[] = [];
  const errors: string[] = [];
  for (const [name, value] of Object.entries(map)) {
    const parsed = parseOne(name, value);
    if (typeof parsed === "string") errors.push(parsed);
    else servers.push(parsed);
  }
  return { servers, errors };
}

/** Parse Claude / Cursor `mcpServers` or VS Code `servers` JSON. */
export function parseMcpImportJson(raw: string): McpImportResult {
  const text = raw.trim();
  if (!text) return { servers: [], errors: ["空内容"] };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { servers: [], errors: ["不是合法 JSON"] };
  }
  if (Array.isArray(parsed)) {
    const servers: ImportedMcpServer[] = [];
    const errors: string[] = [];
    parsed.forEach((item, index) => {
      const rec = asRecord(item);
      const name =
        (typeof rec?.name === "string" && rec.name.trim()) ||
        `server-${index + 1}`;
      const one = parseOne(name, item);
      if (typeof one === "string") errors.push(one);
      else servers.push(one);
    });
    return { servers, errors };
  }
  const rec = asRecord(parsed);
  if (!rec) return { servers: [], errors: ["JSON 根必须是对象"] };
  const nested = asRecord(rec.mcpServers) ?? asRecord(rec.servers);
  if (nested) return parseMap(nested);
  if (rec.command || rec.url || rec.serverUrl || rec.httpUrl) {
    const one = parseOne(typeof rec.name === "string" ? rec.name : "MCP", rec);
    return typeof one === "string"
      ? { servers: [], errors: [one] }
      : { servers: [one], errors: [] };
  }
  return parseMap(rec);
}
