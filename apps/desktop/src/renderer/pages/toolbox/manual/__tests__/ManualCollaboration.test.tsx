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
  "workflow",
  "automation",
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
    expect(document.getElementById("autonomy")?.textContent).toMatch(/自主度/);
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
    expect(screen.getByText("全放行（推荐）")).toBeTruthy();
    expect(screen.getByText(/设为新会话默认/)).toBeTruthy();
    expect(screen.getByText("中途插手")).toBeTruthy();
    expect(screen.getByText("记忆与偏好")).toBeTruthy();
    expect(sectionText("progress")).toMatch(/唯一的常驻视图/);
    expect(sectionText("progress")).toMatch(/拍板就在聊天里/);
    expect(screen.queryByText("设置 · 权限配方")).toBeNull();
    expect(screen.queryByText(/ask_user/)).toBeNull();
    expect(screen.queryByText(/plan_review/)).toBeNull();
    expect(screen.queryByText(/run_redirect/)).toBeNull();
  });

  it("renders workflow section: toolbox design main path, canvas primitives, official templates", () => {
    render(
      <MemoryRouter initialEntries={["/toolbox/manual/collaboration"]}>
        <ManualCollaboration />
      </MemoryRouter>,
    );

    const text = sectionText("workflow");
    expect(text).toMatch(/主路径：去工具箱设计/);
    expect(text).toMatch(/新建工作流/);
    expect(text).toMatch(/画布上能摆什么/);
    expect(text).toMatch(/队员步骤/);
    expect(text).toMatch(/等人关卡/);
    expect(text).toMatch(/结构锁定/);
    expect(text).toMatch(/不再由 CEO 即兴组队/);
    expect(text).toMatch(/复制一份成你自己的工作流/);
    // 「模板」只用于工作流页的官方模板
    expect(text).toMatch(/官方模板/);
    expect(text).not.toMatch(/系统模板/);
    expect(text).not.toMatch(/存为工作流/);
    expect(text).not.toMatch(/从满意的那一轮存起/);
    expect(text).not.toMatch(/回合状态条/);
  });

  it("renders automation section: triggers, inbox, system tasks, workflow binding", () => {
    render(
      <MemoryRouter initialEntries={["/toolbox/manual/collaboration"]}>
        <ManualCollaboration />
      </MemoryRouter>,
    );

    const text = sectionText("automation");
    expect(text).toMatch(/定时（每天 \/ 每周 \/ 自定义 cron）/);
    expect(text).toMatch(/Webhook/);
    expect(text).toMatch(/收件箱/);
    expect(text).toMatch(/系统任务/);
    expect(text).toMatch(/立即触发/);
    expect(text).toMatch(/云端文件夹/);
    expect(text).toMatch(/绑一张工作流（可选）/);
    expect(text).toMatch(/在工具箱里设计好再绑上/);
    expect(text).not.toMatch(/存为工作流/);
    expect(text).toMatch(/重新触发/);
    // 内部词 / 退役词不得外泄；自动化页的预制件不叫「模板」
    expect(text).not.toMatch(/站立任务/);
    expect(text).not.toMatch(/系统模板/);
    expect(text).not.toMatch(/重跑/);
  });

  it("relates the two: workflow = how to split, automation = when to run", () => {
    render(
      <MemoryRouter initialEntries={["/toolbox/manual/collaboration"]}>
        <ManualCollaboration />
      </MemoryRouter>,
    );

    expect(sectionText("workflow")).toMatch(
      /工作流管「活儿怎么拆」.*管「什么时候跑」/,
    );
    expect(sectionText("automation")).toMatch(
      /绑了就按图跑.*不绑就按目标文案让 CEO 即兴组队/,
    );
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
