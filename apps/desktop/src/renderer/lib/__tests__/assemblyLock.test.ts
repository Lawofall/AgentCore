import {
  assemblyIsChatOnly,
  assemblyPickerBlurb,
  isToolboxCapsule,
  officialHint,
} from "@/lib/assemblyLock";
import { describe, expect, it } from "vitest";

describe("assemblyLock", () => {
  it("极简只在输入框写说明，不进工具箱胶囊", () => {
    expect(assemblyPickerBlurb("standard")).toBe("能改文件、跑命令、上网。");
    expect(assemblyPickerBlurb("full")).toBe("含辩论、搜旧对话。");
    expect(assemblyPickerBlurb("chat")).toBe("这场只聊天。");
    expect(assemblyPickerBlurb(null)).toBeUndefined();
    expect(isToolboxCapsule({ kind: "system", recipe: "standard" })).toBe(true);
    expect(isToolboxCapsule({ kind: "system", recipe: "full" })).toBe(true);
    expect(isToolboxCapsule({ kind: "system", recipe: "chat" })).toBe(false);
    expect(isToolboxCapsule({ kind: "user", recipe: null })).toBe(true);
    expect(officialHint({ kind: "system", recipe: "standard" })).toBe(
      "标准不能改。要自己关工具，点新建。",
    );
    expect(officialHint({ kind: "system", recipe: "full" })).toBe(
      "完整不能改。要自己关工具，点新建。",
    );
    expect(officialHint({ kind: "system", recipe: "chat" })).toBe(
      "这场只聊天，不能改。要自己关工具，点新建。",
    );
    expect(officialHint({ kind: "user" })).toBeNull();
    expect(assemblyIsChatOnly({ recipe: "chat" })).toBe(true);
    expect(assemblyIsChatOnly({ recipe: "standard" })).toBe(false);
    expect(assemblyIsChatOnly({ recipe: "full" })).toBe(false);
    expect(assemblyIsChatOnly(null)).toBe(false);
  });
});
