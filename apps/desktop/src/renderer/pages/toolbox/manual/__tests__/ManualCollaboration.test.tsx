// @vitest-environment jsdom
import { ManualCollaboration } from "@/pages/toolbox/manual/ManualCollaboration";
import { collaborationChapter } from "@/pages/toolbox/manual/content/collaboration";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

/** 节 id 挂在标题上，正文在其外层 `<section>`。 */
function sectionText(id: string): string {
  return document.getElementById(id)?.closest("section")?.textContent ?? "";
}

const SECTION_IDS = [
  "briefing",
  "progress",
  "checkpoint",
  "autonomy",
  "debate",
  "control",
  "memory",
  "prompts",
] as const;

describe("ManualCollaboration", () => {
  it("renders content-driven sections with stable deep-link ids", () => {
    render(
      <MemoryRouter initialEntries={["/toolbox/manual/collaboration"]}>
        <ManualCollaboration />
      </MemoryRouter>,
    );

    for (const id of SECTION_IDS) {
      expect(document.getElementById(id)).toBeTruthy();
    }
    expect(document.getElementById("debate")?.textContent).toMatch(/辩论室/);
    expect(sectionText("debate")).toMatch(/点了名就开跑/);
    expect(sectionText("debate")).not.toMatch(/CEO 会开一场辩论/);
    expect(sectionText("debate")).not.toMatch(/三种形态/);
    expect(sectionText("debate")).not.toMatch(/站队/);
    expect(sectionText("debate")).not.toMatch(/掌舵/);
    expect(sectionText("control")).not.toMatch(/「出结论」/);
    expect(sectionText("control")).toMatch(/自己出结论/);
    expect(sectionText("autonomy")).not.toMatch(/组团卡/);
    expect(sectionText("checkpoint")).not.toMatch(/组团卡/);
    expect(document.body.textContent).not.toMatch(/拒开工/);
    expect(document.getElementById("autonomy")?.textContent).toMatch(
      /对话边界/,
    );
    expect(document.getElementById("control")?.textContent).toMatch(/中途插手/);
    expect(document.getElementById("checkpoint")?.textContent).toMatch(
      /检查点与审批/,
    );
    expect(document.getElementById("collab-overview")).toBeNull();
    expect(document.getElementById("roles")).toBeNull();
    expect(document.getElementById("continuation")).toBeNull();

    expect(screen.queryByText(/后续规划/)).toBeNull();
    expect(screen.getByText(/角色由 CEO 临时分配/)).toBeTruthy();
    expect(screen.getAllByText(/带现场续派/).length).toBeGreaterThan(0);
    expect(screen.getByText("本文件夹（网页推荐）")).toBeTruthy();
    expect(screen.getByText("这台电脑（桌面推荐）")).toBeTruthy();
    expect(screen.queryByText(/设为新会话默认/)).toBeNull();
    expect(sectionText("autonomy")).toMatch(/人选的那档会记住/);
    expect(screen.getByText("中途插手")).toBeTruthy();
    expect(screen.getByText("规矩与旧对话")).toBeTruthy();
    expect(sectionText("memory")).toMatch(/可复用的编制/);
    expect(sectionText("memory")).toMatch(/常驻/);
    expect(sectionText("memory")).toMatch(/按需/);
    expect(sectionText("memory")).toMatch(/@ 点名/);
    expect(sectionText("memory")).toMatch(/怎么写才会被翻开/);
    expect(screen.getAllByText("怎么写提示词").length).toBeGreaterThan(0);
    expect(sectionText("prompts")).toMatch(/干什么、什么时候/);
    expect(sectionText("prompts")).toMatch(/一句话介绍/);
    expect(sectionText("prompts")).toMatch(/没有斜杠菜单/);
    expect(sectionText("checkpoint")).not.toMatch(/工作流等人关卡/);
    expect(screen.queryByText("设为定时")).toBeNull();
    expect(sectionText("progress")).toMatch(/唯一的常驻视图/);
    expect(sectionText("progress")).toMatch(/拍板就在聊天里/);
    expect(screen.queryByText("设置 · 权限配方")).toBeNull();
    expect(screen.queryByText(/ask_user/)).toBeNull();
    expect(screen.queryByText(/plan_review/)).toBeNull();
    expect(screen.queryByText(/run_redirect/)).toBeNull();
  });

  it("does not keep a workflow or automations section", () => {
    render(
      <MemoryRouter initialEntries={["/toolbox/manual/collaboration"]}>
        <ManualCollaboration />
      </MemoryRouter>,
    );

    expect(document.getElementById("workflow")).toBeNull();
    expect(document.getElementById("automation")).toBeNull();
    expect(screen.queryByText("设为定时")).toBeNull();
    expect(screen.queryByText(/Webhook/)).toBeNull();
  });

  it("preserves section order and stays text-only (embeds belong to mechanism)", () => {
    expect(collaborationChapter.sections.map((s) => s.id)).toEqual([
      ...SECTION_IDS,
    ]);

    const embedKeys = collaborationChapter.sections.flatMap((s) =>
      s.blocks.filter((b) => b.type === "embed").map((b) => b.key),
    );
    expect(embedKeys).toEqual([]);
  });
});
