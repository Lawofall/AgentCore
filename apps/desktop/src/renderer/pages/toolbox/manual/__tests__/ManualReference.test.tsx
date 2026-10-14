// @vitest-environment jsdom
import { ManualReference } from "@/pages/toolbox/manual/ManualReference";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

function renderReference(initial = "/toolbox/manual/reference") {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <ManualReference />
    </MemoryRouter>,
  );
}

describe("ManualReference", () => {
  it("renders Phase 2 workspace dual-mode copy", () => {
    renderReference();
    expect(screen.getByText("工作区与文件")).toBeTruthy();
    expect(screen.getAllByText(/打开本机文件夹/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/我的文件/).length).toBeGreaterThan(0);
    expect(screen.getByText("协作桌")).toBeTruthy();
    expect(screen.queryByText(/不是第三种文件夹/)).toBeNull();
    expect(screen.queryByText("共享空间")).toBeNull();
    expect(screen.getAllByText(/与我共享/).length).toBeGreaterThan(0);
    expect(screen.getByText(/模式条/)).toBeTruthy();
    expect(screen.getAllByText(/文件夹即工作区/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/项目即工作区/)).toBeNull();
    expect(screen.getByText(/右坞终端/)).toBeTruthy();
    expect(screen.getAllByText(/右坞浏览器/).length).toBeGreaterThan(0);
    expect(screen.getByText(/统一浏览器/)).toBeTruthy();
    expect(screen.queryByText(/右坞团队浏览器/)).toBeNull();
    expect(screen.queryByText(/本地工作区不会出现/)).toBeNull();
  });

  it("renders Git boundary table in FAQ", () => {
    renderReference();
    expect(screen.getByText("Agent 对 Git / 代码能做什么？")).toBeTruthy();
    expect(screen.getByText("会做")).toBeTruthy();
    expect(screen.getByText("需你放行")).toBeTruthy();
    expect(screen.getByText("不会做")).toBeTruthy();
    expect(
      screen.getByText(/读文件；git status \/ diff \/ log \/ fetch/),
    ).toBeTruthy();
    expect(
      screen.getByText(/git add \/ commit \/ push \/ pull \/ 建分支 \/ 切分支/),
    ).toBeTruthy();
    expect(screen.getByText(/开 PR（GitHub）/)).toBeTruthy();
    expect(screen.getByText(/force push/)).toBeTruthy();
    expect(screen.getByText(/reset \/ clean/)).toBeTruthy();
    expect(screen.queryByText(/show \/ blame/)).toBeNull();
    expect(screen.queryByText(/stash push/)).toBeNull();
    expect(
      screen.getByText(
        /普通 push \/ 开 PR 会先弹确认；force \/ 推保护分支仍禁止/,
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/推送远端请你在本地终端手动完成/)).toBeNull();
  });

  it("points product feedback to the beta group and official site", () => {
    renderReference();
    expect(screen.getByText("怎么给产品提意见？")).toBeTruthy();
    expect(screen.getAllByText(/消息页内测群/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/fashitianxia\.xyz/).length).toBeGreaterThan(0);
    expect(screen.queryByText("设置 · 反馈")).toBeNull();
    expect(screen.queryByText("反馈附带的上下文")).toBeNull();
  });

  it("exposes preview markers for section deep links", () => {
    const { container } = renderReference(
      "/toolbox/manual/reference?s=workspace",
    );
    const root = container.querySelector(
      '[data-preview-manual="manual-reference"]',
    );
    expect(root?.getAttribute("data-preview-section")).toBe("workspace");
  });

  it("marks upcoming tools and BYOK model FAQ", () => {
    renderReference();
    expect(screen.getByText("MCP 在哪？")).toBeTruthy();
    expect(screen.getByText(/A2A（规划中）/)).toBeTruthy();
    expect(screen.queryByText(/其他创作工具（尚未开放）/)).toBeNull();
    expect(screen.queryByText(/可自由摆元素的无限画布/)).toBeNull();
    expect(screen.getByText("用的什么模型？")).toBeTruthy();
    expect(screen.getByText(/平台代付，开箱即可对话/)).toBeTruthy();
    expect(screen.getByText(/OpenAI \/ DeepSeek \/ Kimi \/ 智谱/)).toBeTruthy();
    expect(screen.queryByText("画布和白板有什么区别？")).toBeNull();
  });

  it("links duplicate FAQ answers to collaboration chapter", () => {
    renderReference();
    expect(screen.getByText("怎么强制多人干？")).toBeTruthy();
    expect(screen.getByText("怎么下任务")).toBeTruthy();
    expect(screen.getByText("检查点怎么答？")).toBeTruthy();
    expect(screen.getByText("检查点与审批")).toBeTruthy();
    expect(screen.getByText("跑偏了 / 中途想改方向？")).toBeTruthy();
    expect(screen.getByText("中途插手")).toBeTruthy();
    expect(screen.getByText("写的提示词怎么才会被用上？")).toBeTruthy();
    expect(screen.getByText("怎么写提示词")).toBeTruthy();
    expect(screen.queryByText(/ask_user/)).toBeNull();
    expect(screen.queryByText(/plan_review/)).toBeNull();
    expect(document.body.textContent).not.toMatch(/组团卡/);
    expect(document.body.textContent).not.toMatch(/拒开工/);
  });

  it("renders settings rows including memory", () => {
    renderReference();
    expect(screen.getByText("提示词")).toBeTruthy();
    expect(screen.queryByText("全局设定")).toBeNull();
    expect(screen.queryByText("设置 · 自主度")).toBeNull();
  });

  it("does not teach scheduling workflows", () => {
    renderReference();
    expect(screen.queryByText("工作流怎么定时跑？")).toBeNull();
    expect(screen.queryByText("电脑关着，定时任务还会跑吗？")).toBeNull();
    expect(screen.queryByText("工作流和自动化有什么区别？")).toBeNull();
    expect(screen.queryByText(/设为定时/)).toBeNull();
  });

  it("renders glossary terms aligned with product glossary", () => {
    renderReference();
    expect(screen.getByText("一段")).toBeTruthy();
    expect(screen.getByText("对着你的这扇窗")).toBeTruthy();
    expect(screen.getByText("放行")).toBeTruthy();
    expect(screen.getByText("已停止")).toBeTruthy();
    expect(screen.getByText("重新生成")).toBeTruthy();
    expect(screen.getByText("接着做")).toBeTruthy();
    expect(screen.getByText("辩论室")).toBeTruthy();
    expect(screen.getByText("接续链")).toBeTruthy();
    expect(screen.getAllByText("画布").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("白板")).toBeNull();
    expect(screen.getAllByText("文档").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("对话边界").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("工作流")).toBeNull();
    expect(screen.queryByText("系统任务")).toBeNull();
    expect(screen.queryByText("收件箱")).toBeNull();
  });
});
