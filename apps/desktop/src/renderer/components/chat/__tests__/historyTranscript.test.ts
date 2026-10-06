import { describe, expect, it } from "vitest";

import {
  parseHistoryTranscript,
  presentToolReceipt,
} from "@/components/chat/historyTranscript";

describe("parseHistoryTranscript", () => {
  it("returns null for the older prose mirror", () => {
    expect(parseHistoryTranscript("用户：你好\n\nCEO：在。")).toBeNull();
  });

  it("reads length-prefixed records and keeps the raw tool bytes", () => {
    const page = JSON.stringify({
      url: "https://example.com/a",
      title: "例",
      content: "甲\n乙",
    });
    const body = `@@user 3\n看一下\n@@tool ${page.length} web_fetch\n${page}\n`;
    expect(parseHistoryTranscript(body)).toEqual([
      { role: "user", name: "", text: "看一下" },
      { role: "tool", name: "web_fetch", text: page },
    ]);
  });

  it("keeps a trailing ideographic stop on the emoji turn", () => {
    const content = "看到了👍。";
    const next = "下一";
    const body = `@@assistant ${[...content].length}\n${content}\n@@user ${[...next].length}\n${next}\n`;
    expect(parseHistoryTranscript(body)).toEqual([
      { role: "assistant", name: "", text: content },
      { role: "user", name: "", text: next },
    ]);
  });

  it("dumps the remainder when the code-point length runs past the body", () => {
    expect(parseHistoryTranscript("@@assistant 5\n好。\n")).toEqual([
      { role: "other", name: "", text: "好。\n" },
    ]);
  });
});

describe("presentToolReceipt", () => {
  it("decodes a page JSON content field into real newlines", () => {
    const face = presentToolReceipt(
      JSON.stringify({
        url: "https://example.com/a",
        title: "例",
        content: "甲\n乙",
        note: "未读完",
      }),
    );
    expect(face).toEqual({
      kind: "page",
      title: "例",
      url: "https://example.com/a",
      body: "甲\n乙",
      note: "未读完",
    });
  });

  it("keeps a preface and pretty-prints a roster object", () => {
    const face = presentToolReceipt(
      `共 1 个文件夹：\n${JSON.stringify({ folders: [{ name: "桌" }] })}`,
    );
    expect(face.kind).toBe("code");
    if (face.kind !== "code") return;
    expect(face.caption).toBe("共 1 个文件夹：");
    expect(face.code).toContain('"name": "桌"');
  });

  it("leaves a short plain receipt as text", () => {
    expect(presentToolReceipt("（空目录）")).toEqual({
      kind: "text",
      text: "（空目录）",
    });
  });
});
