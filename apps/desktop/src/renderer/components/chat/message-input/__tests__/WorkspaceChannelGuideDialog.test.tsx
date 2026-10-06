// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { WorkspaceChannelGuideDialog } from "../WorkspaceChannelGuideDialog";

afterEach(() => {
  cleanup();
});

const dialogText = () => screen.getByRole("dialog").textContent ?? "";

describe("WorkspaceChannelGuideDialog", () => {
  it("讲清这次聊哪：默认在这台电脑跑，云是选项", () => {
    render(
      <WorkspaceChannelGuideDialog
        open
        onOpenChange={() => {}}
        showLocalTraditional
      />,
    );
    expect(screen.getByText("在哪工作：怎么选")).toBeTruthy();
    expect(screen.getByText("这次聊哪")).toBeTruthy();
    expect(screen.queryByText("我的文件")).toBeNull();
    expect(dialogText()).toContain("点云图标的接着聊");
    expect(dialogText()).toContain("点硬盘图标的改电脑上那份");
    expect(dialogText()).toContain("文件和运行在这台电脑");
    expect(dialogText()).toContain("不是离线");
    expect(dialogText()).toContain("接着改同一份");
    expect(screen.getByRole("button", { name: "知道了" })).toBeTruthy();
  });

  it("入口名与「在哪工作」菜单逐字一致", () => {
    render(
      <WorkspaceChannelGuideDialog
        open
        onOpenChange={() => {}}
        showLocalTraditional
      />,
    );
    for (const label of ["本地对话", "云端对话"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(screen.queryByText("直接改这个文件夹")).toBeNull();
    expect(screen.queryByText("先在云上做，原件先不动")).toBeNull();
  });

  it("不再讲本机文件夹的第二问", () => {
    render(
      <WorkspaceChannelGuideDialog
        open
        onOpenChange={() => {}}
        showLocalTraditional
      />,
    );
    expect(screen.queryByText("电脑上的文件夹怎么用")).toBeNull();
    expect(screen.queryByText("先在云上做，原件先不动")).toBeNull();
  });

  it("本地对话明说不是离线", () => {
    render(
      <WorkspaceChannelGuideDialog
        open
        onOpenChange={() => {}}
        showLocalTraditional
      />,
    );
    const local = screen.getByText("本地对话");
    const localDd = local.closest("div")?.querySelector("dd");
    expect(localDd?.textContent).toMatch(/对话仍在云上/);
    expect(localDd?.textContent).toMatch(/不是离线/);
  });

  it("桌面只有本地对话被标「推荐」", () => {
    render(
      <WorkspaceChannelGuideDialog
        open
        onOpenChange={() => {}}
        showLocalTraditional
      />,
    );
    expect(screen.getAllByText("推荐")).toHaveLength(1);
  });

  it("没有本机盘时只讲云不同步到电脑", () => {
    render(
      <WorkspaceChannelGuideDialog
        open
        onOpenChange={() => {}}
        showLocalTraditional={false}
      />,
    );
    expect(screen.getByText("这次聊哪")).toBeTruthy();
    expect(screen.queryByText("本地对话")).toBeNull();
    expect(screen.queryByText("我的文件")).toBeNull();
    expect(dialogText()).not.toContain("硬盘图标");
    expect(dialogText()).toContain("不会自动同步到你电脑");
    expect(screen.queryByText("打开本机文件夹")).toBeNull();
    expect(screen.queryByText("直接改这个文件夹")).toBeNull();
    expect(screen.queryByText("先在云上做，原件先不动")).toBeNull();
    expect(dialogText()).not.toContain("离线模式");
    expect(screen.queryByText("推荐")).toBeNull();
  });

  it("不把菜单目录和怎么选再抄一遍", () => {
    render(
      <WorkspaceChannelGuideDialog
        open
        onOpenChange={() => {}}
        showLocalTraditional
      />,
    );
    expect(screen.queryByRole("heading", { name: "怎么选" })).toBeNull();
    expect(screen.queryByText("新建或加入…")).toBeNull();
    expect(screen.queryByText("从本机加入")).toBeNull();
  });

  // 防回潮：这份文案曾直接抄自内部设计文档，把实现词和防回潮对照写法漏给了用户。
  const BANNED = [
    "ModeControl",
    "Composer",
    "sidecar",
    "云桌",
    "过桥",
    "本机传统",
    "遗留",
    "后台云端",
    "通道",
    "合回",
    "云协作",
    "≠",
  ];

  it.each([true, false])(
    "不出现代码符号与内部黑话（showLocalTraditional=%s）",
    (showLocalTraditional) => {
      render(
        <WorkspaceChannelGuideDialog
          open
          onOpenChange={() => {}}
          showLocalTraditional={showLocalTraditional}
        />,
      );
      const text = dialogText().toLowerCase();
      for (const word of BANNED) {
        expect(
          text.includes(word.toLowerCase()),
          `文案里不该出现「${word}」`,
        ).toBe(false);
      }
    },
  );
});
