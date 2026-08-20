import type { MemoryUpdateItem } from "@/stores/conversation";
import { describe, expect, it, vi } from "vitest";
import {
  canDisputeMemoryItem,
  canMoveMemoryItem,
  memoryScopeOverview,
  memoryScopePillLabel,
} from "../MemoryUpdateItemRow";

vi.mock("@/hooks/useFolders", () => ({
  getFolders: () => [{ id: "F1", name: "AgentCore" }],
}));

describe("memory scope labels", () => {
  it("labels global and named folder", () => {
    expect(memoryScopePillLabel("global")).toBe("全局");
    expect(memoryScopePillLabel("project", "F1")).toBe("本文件夹 · AgentCore");
    expect(memoryScopePillLabel("project", "missing")).toBe("本文件夹");
  });

  it("builds card scope overview across layers", () => {
    expect(
      memoryScopeOverview([
        { scope: "global" },
        { scope: "project", projectId: "F1" },
      ]),
    ).toBe("全局 + 本文件夹 · AgentCore");
  });
});

describe("canMoveMemoryItem", () => {
  const base: MemoryUpdateItem = {
    action: "add",
    file: "画像",
    section: "关于用户的事实",
    content: "事实",
    target: "global/profile",
    scope: "global",
  };

  it("allows global→project for movable profile facts", () => {
    expect(canMoveMemoryItem(base, "to_project", "F1")).toBe(true);
  });

  it("blocks 纠正记录 / 偏好 / remove / missing project", () => {
    expect(
      canMoveMemoryItem({ ...base, section: "纠正记录" }, "to_project", "F1"),
    ).toBe(false);
    expect(
      canMoveMemoryItem({ ...base, file: "偏好" }, "to_project", "F1"),
    ).toBe(false);
    expect(
      canMoveMemoryItem({ ...base, action: "remove" }, "to_project", "F1"),
    ).toBe(false);
    expect(canMoveMemoryItem(base, "to_project", null)).toBe(false);
  });

  it("blocks 项目约束 → global", () => {
    expect(
      canMoveMemoryItem(
        {
          ...base,
          scope: "project",
          section: "项目约束",
          projectId: "F1",
          target: "project/F1/profile",
        },
        "to_global",
        "F1",
      ),
    ).toBe(false);
  });
});

describe("canDisputeMemoryItem", () => {
  const base: MemoryUpdateItem = {
    action: "add",
    file: "画像",
    section: "关于用户的事实",
    content: "事实",
    target: "global/profile",
    scope: "global",
  };

  it("offers rejection where a move is impossible (no folder, 偏好, 纠正记录)", () => {
    // The scope invariants gating a move answer「这行能放哪层」— not「用户能不能说它错了」.
    expect(canDisputeMemoryItem(base)).toBe(true);
    expect(canDisputeMemoryItem({ ...base, file: "偏好" })).toBe(true);
    expect(canDisputeMemoryItem({ ...base, section: "纠正记录" })).toBe(true);
  });

  it("skips rows that hold no remembered content", () => {
    expect(canDisputeMemoryItem({ ...base, action: "remove" })).toBe(false);
    expect(canDisputeMemoryItem({ ...base, action: "quota_denied" })).toBe(
      false,
    );
    expect(canDisputeMemoryItem({ ...base, action: "quota_holder" })).toBe(
      false,
    );
    expect(canDisputeMemoryItem({ ...base, content: "  " })).toBe(false);
  });
});
