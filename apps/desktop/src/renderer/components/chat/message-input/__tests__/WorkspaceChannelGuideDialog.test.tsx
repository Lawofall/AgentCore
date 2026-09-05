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
    expect(dialogText()).toContain("点硬盘图标的，会再问怎么用");
    expect(dialogText()).toContain("硬盘图标那一行");
    expect(dialogText()).toContain("文件和运行都在这台电脑");
    expect(dialogText()).toContain("不是离线");
    expect(screen.getByText(/日常在这台电脑写、跑 → 本地对话/)).toBeTruthy();
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
    for (const label of [
      "本地对话",
      "云端对话",
      "新建或加入…",
      "直接改这个文件夹",
      "复制到云上当新家",
      "先在云上做，原件先不动",
    ]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(dialogText()).toContain("从本机加入");
  });

  it("导入说清是复制一份、原件不再跟着变", () => {
    render(
      <WorkspaceChannelGuideDialog
        open
        onOpenChange={() => {}}
        showLocalTraditional
      />,
    );
    expect(screen.getByText(/复制一份上来/)).toBeTruthy();
    expect(screen.getByText(/原件不会跟着变/)).toBeTruthy();
  });

  it("先在云上做与复制到云上互斥，无盘不出现", () => {
    render(
      <WorkspaceChannelGuideDialog
        open
        onOpenChange={() => {}}
        showLocalTraditional
      />,
    );
    const borrow = screen.getByText("先在云上做，原件先不动");
    const borrowDd = borrow.closest("div")?.querySelector("dd");
    expect(borrowDd?.textContent).toMatch(/这一单在云上做/);
    expect(borrowDd?.textContent).toMatch(/原件先不动/);
    expect(borrowDd?.textContent).toMatch(/写不写回/);
    expect(borrowDd?.textContent).toMatch(/不是复制上来当云上那份家/);
    expect(borrowDd?.textContent).not.toMatch(/复制一份上来/);
    expect(
      screen.getByText(
        /这一单想在云上做、电脑上的原件先不动 → 先在云上做，原件先不动/,
      ),
    ).toBeTruthy();
    expect(dialogText()).not.toContain("上面四个");
    expect(dialogText()).not.toContain("上面五个");

    cleanup();
    render(
      <WorkspaceChannelGuideDialog
        open
        onOpenChange={() => {}}
        showLocalTraditional={false}
      />,
    );
    expect(screen.queryByText("先在云上做，原件先不动")).toBeNull();
    expect(screen.queryByText("从本机加入")).toBeNull();
  });

  it("直接改这个文件夹明说不是离线模式", () => {
    render(
      <WorkspaceChannelGuideDialog
        open
        onOpenChange={() => {}}
        showLocalTraditional
      />,
    );
    const local = screen.getByText(/不是离线模式/);
    expect(local.textContent).toMatch(/从本机加入/);
    expect(local.textContent).toMatch(/联网/);
    expect(local.textContent).toMatch(/对话记录也仍然存在云上/);
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

  it("没有本机盘时只讲云", () => {
    render(
      <WorkspaceChannelGuideDialog
        open
        onOpenChange={() => {}}
        showLocalTraditional={false}
      />,
    );
    expect(screen.getByText("这次聊哪")).toBeTruthy();
    expect(screen.getByText("云端对话")).toBeTruthy();
    expect(screen.queryByText("本地对话")).toBeNull();
    expect(screen.getByText("新建文件夹")).toBeTruthy();
    expect(screen.queryByText("我的文件")).toBeNull();
    expect(dialogText()).toContain("点列表里的文件夹接着聊");
    expect(dialogText()).not.toContain("硬盘图标");
    expect(screen.getByText("推荐")).toBeTruthy();
    expect(screen.getByText(/不会自动同步到你电脑/)).toBeTruthy();
    expect(screen.queryByText("打开本机文件夹")).toBeNull();
    expect(screen.queryByText("直接改这个文件夹")).toBeNull();
    expect(screen.queryByText("先在云上做，原件先不动")).toBeNull();
    expect(dialogText()).not.toContain("离线模式");
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
