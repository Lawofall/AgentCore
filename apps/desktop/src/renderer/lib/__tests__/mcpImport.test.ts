import { describe, expect, it } from "vitest";
import { parseMcpImportJson } from "../mcpImport";

describe("parseMcpImportJson", () => {
  it("reads Cursor / Claude mcpServers stdio and url", () => {
    const out = parseMcpImportJson(`{
      "mcpServers": {
        "fs": { "command": "npx", "args": ["-y", "pkg"] },
        "linear": {
          "url": "https://mcp.linear.app/mcp",
          "headers": { "Authorization": "Bearer lin_xxx" }
        }
      }
    }`);
    expect(out.errors).toEqual([]);
    expect(out.servers).toEqual([
      {
        transport: "stdio",
        name: "fs",
        command: "npx",
        args: ["-y", "pkg"],
      },
      {
        transport: "http",
        name: "linear",
        url: "https://mcp.linear.app/mcp",
        bearer: "lin_xxx",
      },
    ]);
  });

  it("reads VS Code servers map", () => {
    const out = parseMcpImportJson(`{
      "servers": {
        "gh": { "command": "docker", "args": ["run", "gh"] }
      }
    }`);
    expect(out.servers[0]).toMatchObject({
      transport: "stdio",
      name: "gh",
      command: "docker",
    });
  });

  it("rejects empty and invalid JSON", () => {
    expect(parseMcpImportJson("").errors[0]).toBe("空内容");
    expect(parseMcpImportJson("{").errors[0]).toBe("不是合法 JSON");
  });
});
