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
    expect(screen.getByText("共享空间")).toBeTruthy();
    expect(screen.getByText(/不是第三种文件夹/)).toBeTruthy();
    expect(screen.getByText(/模式条/)).toBeTruthy();
    expect(screen.getAllByText(/文件夹即工作区/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/项目即工作区/)).toBeNull();
    expect(screen.getByText(/右坞终端/)).toBeTruthy();
    expect(screen.getByText(/右坞浏览器/)).toBeTruthy();
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
      screen.getByText(
        /读文件；git status \/ diff \/ log \/ fetch \/ show \/ blame/,
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(/git add \/ commit \/ push \/ pull \/ 建分支 \/ 切分支/),
    ).toBeTruthy();
    expect(screen.getByText(/开 PR（GitHub）/)).toBeTruthy();
    expect(screen.getByText(/force push/)).toBeTruthy();
    expect(screen.getByText(/reset \/ clean/)).toBeTruthy();
    expect(
      screen.getByText(
        /普通 push \/ 开 PR 会先弹确认；force \/ 推保护分支仍禁止/,
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/推送远端请你在本地终端手动完成/)).toBeNull();
  });

  it("renders feedback FAQ and privacy context", () => {
    renderReference();
    expect(screen.getByText("怎么给产品提意见？")).toBeTruthy();
    expect(
      screen.getAllByText(/不含文件夹里的文件内容/).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("反馈附带的上下文")).toBeTruthy();
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
    expect(screen.getByText(/MCP（本机连接器）/)).toBeTruthy();
    expect(screen.getByText(/A2A（规划中）/)).toBeTruthy();
    expect(screen.getByText(/其他创作工具（即将上线）/)).toBeTruthy();
    expect(screen.getByText(/白板（画布可用）/)).toBeTruthy();
    expect(screen.getByText("用的什么模型？")).toBeTruthy();
    expect(screen.getByText(/平台代付，开箱即可对话/)).toBeTruthy();
    expect(screen.getByText(/OpenAI \/ DeepSeek \/ Kimi \/ 智谱/)).toBeTruthy();
    expect(screen.getByText("画布和白板有什么区别？")).toBeTruthy();
  });

  it("links duplicate FAQ answers to collaboration chapter", () => {
    renderReference();
    expect(screen.getByText("怎么强制多人干？")).toBeTruthy();
    expect(screen.getByText("怎么下任务")).toBeTruthy();
    expect(screen.getByText("检查点怎么答？")).toBeTruthy();
    expect(screen.getByText("检查点与审批")).toBeTruthy();
    expect(screen.getByText("跑偏了 / 中途想改方向？")).toBeTruthy();
    expect(screen.getByText("中途插手")).toBeTruthy();
    expect(screen.queryByText(/ask_user/)).toBeNull();
    expect(screen.queryByText(/plan_review/)).toBeNull();
  });

  it("renders settings rows including memory", () => {
    renderReference();
    expect(screen.getByText("全局设定")).toBeTruthy();
    expect(screen.queryByText("设置 · 自主度")).toBeNull();
  });

  it("answers how workflows and automations relate", () => {
    renderReference();
    expect(screen.getByText("工作流和自动化有什么区别？")).toBeTruthy();
    expect(screen.getByText("电脑关着，定时任务还会跑吗？")).toBeTruthy();
    expect(screen.getByText(/去工具箱新建或套官方模板/)).toBeTruthy();
    expect(screen.getAllByText(/自动化 · 收件箱/).length).toBeGreaterThan(0);
  });

  it("renders glossary terms aligned with product glossary", () => {
    renderReference();
    expect(screen.getByText("队员")).toBeTruthy();
    expect(screen.getByText("放行")).toBeTruthy();
    expect(screen.getByText("已停止")).toBeTruthy();
    expect(screen.getByText("重新生成")).toBeTruthy();
    expect(screen.getByText("带现场续派（同人接续）")).toBeTruthy();
    expect(screen.getByText("辩论室")).toBeTruthy();
    expect(screen.getByText("接续链")).toBeTruthy();
    expect(screen.getAllByText("画布").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("白板").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("自主度").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("工作流").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("系统任务")).toBeTruthy();
    expect(screen.getAllByText("收件箱").length).toBeGreaterThanOrEqual(1);
  });
});
