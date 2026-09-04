// @vitest-environment jsdom
import {
  GitChangesSection,
  canDiscardChange,
  groupGitChangesByDir,
  isUntrackedChange,
  primaryStatusChar,
  shortDirLabel,
  statusCharClass,
  statusSummaryParts,
} from "@/components/workspace/GitChangesSection";
import type { PresentGitRepoStatus } from "@/lib/gitRepoStatus";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/stores/sidePanel", () => ({
  useSidePanelStore: (
    sel: (s: { openFileTab: (rel: string, name: string) => void }) => unknown,
  ) => sel({ openFileTab: vi.fn() }),
}));

describe("GitChangesSection status helpers", () => {
  it("primaryStatusChar picks staged / unstaged / untracked letter", () => {
    expect(primaryStatusChar("M ")).toBe("M");
    expect(primaryStatusChar(" M")).toBe("M");
    expect(primaryStatusChar("A ")).toBe("A");
    expect(primaryStatusChar(" D")).toBe("D");
    expect(primaryStatusChar("??")).toBe("?");
    expect(primaryStatusChar("R ")).toBe("R");
  });

  it("statusCharClass maps industry colors", () => {
    expect(statusCharClass("M")).toContain("warning");
    expect(statusCharClass("A")).toContain("success");
    expect(statusCharClass("?")).toContain("success");
    expect(statusCharClass("D")).toContain("destructive");
    expect(statusCharClass("R")).toContain("primary");
  });

  it("canDiscardChange only for unstaged tracked files", () => {
    expect(canDiscardChange({ path: "a.ts", code: " M" }, false)).toBe(true);
    expect(canDiscardChange({ path: "a.ts", code: "M " }, true)).toBe(false);
    expect(canDiscardChange({ path: "a.ts", code: "??" }, false)).toBe(false);
  });

  it("isUntrackedChange detects ??", () => {
    expect(isUntrackedChange({ path: "a.ts", code: "??" })).toBe(true);
    expect(isUntrackedChange({ path: "a.ts", code: " M" })).toBe(false);
  });

  it("groupGitChangesByDir groups by parent and sorts root first", () => {
    const groups = groupGitChangesByDir([
      { path: "apps/a.ts", code: " M" },
      { path: "root.ts", code: "??" },
      { path: "apps/b.ts", code: " M" },
      { path: "docs/c.md", code: "M " },
    ]);
    expect(groups.map((g) => g.dir)).toEqual(["", "apps", "docs"]);
    expect(groups[0]?.entries.map((e) => e.path)).toEqual(["root.ts"]);
    expect(groups[1]?.entries.map((e) => e.path)).toEqual([
      "apps/a.ts",
      "apps/b.ts",
    ]);
  });

  it("shortDirLabel keeps last 1–2 segments", () => {
    expect(shortDirLabel("")).toBe("仓根");
    expect(shortDirLabel("apps")).toBe("apps");
    expect(shortDirLabel("apps/desktop")).toBe("apps/desktop");
    expect(shortDirLabel("apps/desktop/src/renderer")).toBe("src/renderer");
  });

  it("statusSummaryParts aggregates by primary letter in SCM order", () => {
    expect(
      statusSummaryParts([
        { path: "a.ts", code: " M" },
        { path: "b.ts", code: "M " },
        { path: "c.ts", code: "??" },
        { path: "d.ts", code: " D" },
        { path: "e.ts", code: "??" },
      ]),
    ).toEqual([
      { ch: "M", n: 2 },
      { ch: "D", n: 1 },
      { ch: "?", n: 2 },
    ]);
  });
});

function expectHoverOnlyCluster(el: Element | null) {
  expect(el).toBeTruthy();
  const cls = el?.className ?? "";
  expect(cls).toMatch(/\bhidden\b/);
  expect(cls).toContain("group-hover:flex");
  expect(cls).toContain("group-focus-within:flex");
  expect(cls).not.toMatch(/opacity-0/);
}

const dirtyUnstaged: PresentGitRepoStatus = {
  present: true,
  branch: "main",
  dirty: true,
  ahead: 0,
  behind: 0,
  staged: [],
  unstaged: [
    { path: "apps/a.ts", code: " M" },
    { path: "new.ts", code: "??" },
  ],
  conflicted: [],
};

describe("GitChangesSection hover-only chrome", () => {
  afterEach(cleanup);

  it("idle discard/delete and dir actions are hidden; stage stays in flow", () => {
    render(
      <GitChangesSection
        rootId="r1"
        status={dirtyUnstaged}
        onRefresh={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /未暂存/ }));

    expectHoverOnlyCluster(screen.getByLabelText("丢弃改动").parentElement);
    expectHoverOnlyCluster(
      screen.getByLabelText("删除未跟踪文件").parentElement,
    );
    for (const btn of screen.getAllByLabelText("暂存本组")) {
      expectHoverOnlyCluster(btn.parentElement);
    }

    const stageButtons = screen.getAllByLabelText("暂存");
    expect(stageButtons.length).toBeGreaterThan(0);
    for (const btn of stageButtons) {
      expect(btn.className).not.toMatch(/\bhidden\b/);
      expect(btn.parentElement?.className).not.toMatch(/\bhidden\b/);
    }
  });
});
