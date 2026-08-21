// @vitest-environment jsdom
import { CollapsibleUserText } from "@/components/CollapsibleUserText";
import { InterjectionBubbles } from "@/components/InterjectionBubbles";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

describe("CollapsibleUserText", () => {
  it("短文（无溢出）不显示展开按钮", () => {
    const spy = vi
      .spyOn(HTMLElement.prototype, "scrollHeight", "get")
      .mockReturnValue(40);
    const clientSpy = vi
      .spyOn(HTMLElement.prototype, "clientHeight", "get")
      .mockReturnValue(40);

    render(<CollapsibleUserText contentKey="hi">短消息</CollapsibleUserText>);

    expect(screen.queryByRole("button", { name: "展开全文" })).toBeNull();
    expect(screen.getByText("短消息")).toBeTruthy();

    spy.mockRestore();
    clientSpy.mockRestore();
  });

  it("溢出时默认夹住，可展开再收起", () => {
    const spy = vi
      .spyOn(HTMLElement.prototype, "scrollHeight", "get")
      .mockReturnValue(400);
    const clientSpy = vi
      .spyOn(HTMLElement.prototype, "clientHeight", "get")
      .mockReturnValue(100);

    render(
      <CollapsibleUserText contentKey="long">
        {"长文\n".repeat(40)}
      </CollapsibleUserText>,
    );

    const expand = screen.getByRole("button", { name: "展开全文" });
    expect(expand).toBeTruthy();
    expect(
      document.querySelector(".collapsible-user-text-body.is-clamped"),
    ).toBeTruthy();
    expect(document.querySelector(".collapsible-user-text-fade")).toBeTruthy();

    fireEvent.click(expand);
    expect(screen.getByRole("button", { name: "收起" })).toBeTruthy();
    expect(
      document.querySelector(".collapsible-user-text-body.is-clamped"),
    ).toBeNull();
    expect(document.querySelector(".collapsible-user-text-fade")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "收起" }));
    expect(screen.getByRole("button", { name: "展开全文" })).toBeTruthy();
    expect(
      document.querySelector(".collapsible-user-text-body.is-clamped"),
    ).toBeTruthy();

    spy.mockRestore();
    clientSpy.mockRestore();
  });
});

describe("InterjectionBubbles · 用户气泡折叠", () => {
  it("长插话正文走 CollapsibleUserText", () => {
    const spy = vi
      .spyOn(HTMLElement.prototype, "scrollHeight", "get")
      .mockReturnValue(400);
    const clientSpy = vi
      .spyOn(HTMLElement.prototype, "clientHeight", "get")
      .mockReturnValue(100);

    render(
      <InterjectionBubbles
        items={[
          {
            interjectionId: "ij-1",
            content: "插话长文\n".repeat(30),
            status: "acked",
          },
        ]}
      />,
    );

    expect(screen.getByRole("button", { name: "展开全文" })).toBeTruthy();
    expect(
      document.querySelector(
        "[data-testid='interjection-bubble-ij-1'] .collapsible-user-text",
      ),
    ).toBeTruthy();

    spy.mockRestore();
    clientSpy.mockRestore();
  });

  it("turnClosed + received 显示未被读取；进行中仍等待读取", () => {
    const { rerender } = render(
      <InterjectionBubbles
        items={[
          {
            interjectionId: "ij-r",
            content: "补充一句",
            status: "received",
          },
        ]}
      />,
    );
    expect(screen.getByText("已送达，等待主 Agent 读取")).toBeTruthy();

    rerender(
      <InterjectionBubbles
        turnClosed
        items={[
          {
            interjectionId: "ij-r",
            content: "补充一句",
            status: "received",
          },
        ]}
      />,
    );
    expect(screen.getByText("未被主 Agent 读取")).toBeTruthy();
    expect(screen.queryByText("已送达，等待主 Agent 读取")).toBeNull();
  });

  it("failed 徽标为未被处理，note 原样展示", () => {
    render(
      <InterjectionBubbles
        turnClosed
        items={[
          {
            interjectionId: "ij-f",
            content: "丢弃的插话",
            status: "failed",
            note: "你按了停止，这条插话未被读取，已丢弃",
          },
        ]}
      />,
    );
    expect(screen.getByText("未被处理")).toBeTruthy();
    expect(
      screen.getByText("你按了停止，这条插话未被读取，已丢弃"),
    ).toBeTruthy();
  });

  it("queued 走一行注记，不出用户气泡；note 仍保留", () => {
    render(
      <InterjectionBubbles
        items={[
          {
            interjectionId: "ij-q",
            content: "排队后出队会重复的话",
            status: "queued",
            note: "已转入下一回合排队",
          },
          {
            interjectionId: "ij-i",
            content: "已注入的插话",
            status: "injected",
          },
        ]}
      />,
    );

    expect(screen.getByText("将在下一条回复处理")).toBeTruthy();
    expect(screen.getByTestId("interjection-queued-note-ij-q")).toBeTruthy();
    const preview = document.querySelector(
      "[data-testid='interjection-queued-note-ij-q'] .interjection-queued-preview",
    );
    expect(preview?.getAttribute("title")).toBe("排队后出队会重复的话");
    expect(preview?.textContent).toBe("排队后出队会重复的话");
    expect(
      document.querySelector(
        "[data-testid='interjection-bubble-ij-q'] .bubble.user",
      ),
    ).toBeNull();
    expect(screen.getByText("已转入下一回合排队")).toBeTruthy();

    expect(
      document.querySelector(
        "[data-testid='interjection-bubble-ij-i'] .bubble.user",
      ),
    ).toBeTruthy();
    expect(screen.getByText("主 Agent 已看到")).toBeTruthy();
  });

  it("addressed 只留用户泡，不画徽章与 note", () => {
    render(
      <InterjectionBubbles
        items={[
          {
            interjectionId: "ij-addr",
            content: "停止",
            status: "addressed",
            note: "已在本回合停掉对应成员",
          },
        ]}
      />,
    );
    expect(screen.getByTestId("interjection-bubble-ij-addr")).toBeTruthy();
    expect(screen.getByText("停止")).toBeTruthy();
    expect(screen.queryByTestId("interjection-status-ij-addr")).toBeNull();
    expect(screen.queryByText("已纳入本回合合成")).toBeNull();
    expect(screen.queryByText("已在本回合停掉对应成员")).toBeNull();
  });

  it("插话气泡渲染点名芯片，不暗示已派单", () => {
    render(
      <InterjectionBubbles
        items={[
          {
            interjectionId: "ij-m",
            content: "请让研究员再核一遍成本。",
            status: "injected",
            agentMentions: [{ agentId: "agent_research", role: "研究员" }],
            attachments: [{ name: "成本表.xlsx" }],
          },
        ]}
      />,
    );
    const chip = screen.getByTestId("agent-mention-chip");
    expect(chip.textContent).toContain("点名");
    expect(chip.textContent).toContain("研究员");
    expect(chip.textContent).not.toMatch(/派单|已派/);
    expect(screen.getByText("成本表.xlsx")).toBeTruthy();
  });
});
