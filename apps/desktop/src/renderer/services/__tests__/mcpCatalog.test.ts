import {
  parseMcpListToolsValue,
  sanitizeMcpToolName,
} from "@/services/mcpCatalog";
import { describe, expect, it } from "vitest";

describe("sanitizeMcpToolName", () => {
  it("matches the Python mint: mcp_{server}_{tool}, bounded, no punctuation", () => {
    const name = sanitizeMcpToolName("my-server!", "list/files");
    expect(name.startsWith("mcp_")).toBe(true);
    expect(name.length).toBeLessThanOrEqual(64);
    expect(name).not.toContain("/");
    expect(name).not.toContain("!");
    expect(name).toBe("mcp_my-server__list_files");
  });
});

describe("parseMcpListToolsValue", () => {
  it("maps ready tools to grantable CEO+worker cards and keeps failed servers tool-less", () => {
    const parsed = parseMcpListToolsValue({
      servers: [
        {
          id: "fs",
          name: "Filesystem",
          status: "ready",
          tools: [
            {
              name: "read_file",
              description: "Read a file",
              inputSchema: {
                type: "object",
                properties: { path: { type: "string", description: "Path" } },
                required: ["path"],
              },
            },
          ],
        },
        {
          id: "gh",
          name: "GitHub",
          status: "failed",
          error: "GITHUB_TOKEN 未配置",
          tools: [{ name: "should_not_appear" }],
        },
      ],
    });
    expect(parsed).not.toBeNull();
    if (parsed == null) return;
    expect(parsed).toHaveLength(2);
    const [fs, gh] = parsed;
    expect(fs.status).toBe("ready");
    expect(fs.tools).toHaveLength(1);
    expect(fs.tools[0].name).toBe("mcp_fs_read_file");
    expect(fs.tools[0].approval).toBe("grantable");
    expect(fs.tools[0].available_to).toEqual(["ceo", "worker"]);
    expect(fs.tools[0].description).toContain("[MCP · Filesystem]");
    expect(gh.status).toBe("failed");
    expect(gh.tools).toEqual([]);
    expect(gh.error).toContain("GITHUB_TOKEN");
  });

  it("rejects a payload that is not { servers: [] }", () => {
    expect(parseMcpListToolsValue(null)).toBeNull();
    expect(parseMcpListToolsValue({})).toBeNull();
    expect(parseMcpListToolsValue({ servers: "nope" })).toBeNull();
  });
});
