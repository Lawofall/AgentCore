// @vitest-environment jsdom
import { Switch } from "@/components/ui/Switch";
import { cardVariantClass } from "@/components/ui/card";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SettingRow } from "../SettingRow";

describe("SettingRow", () => {
  it("static row hosts a control and is not a button", () => {
    const onCheckedChange = vi.fn();
    const { container } = render(
      <SettingRow
        label="通知"
        description="新消息时在桌面提醒。"
        control={
          <Switch checked onCheckedChange={onCheckedChange} label="通知" />
        }
      />,
    );
    // The row itself is a div — only the Switch inside it is interactive.
    expect(container.firstElementChild?.tagName).toBe("DIV");
    fireEvent.click(screen.getByRole("switch"));
    expect(onCheckedChange).toHaveBeenCalledWith(false);
  });

  it("read-only value row mutes the label and shows the value", () => {
    render(<SettingRow label="客户端版本" value="0.9.0" />);
    expect(screen.getByText("客户端版本").className).toContain(
      "text-muted-foreground",
    );
    expect(screen.getByText("0.9.0").className).toContain("text-foreground");
  });

  it("select row exposes pressed state and a check when chosen", () => {
    const onClick = vi.fn();
    const { container, rerender } = render(
      <SettingRow
        variant="select"
        label="仅好友"
        description="只有已同意的好友可以发起私信。"
        selected
        onClick={onClick}
      />,
    );
    const row = screen.getByRole("button", { name: /仅好友/ });
    expect(row.getAttribute("aria-pressed")).toBe("true");
    expect(container.querySelector("svg")).toBeTruthy();
    fireEvent.click(row);
    expect(onClick).toHaveBeenCalledTimes(1);

    rerender(<SettingRow variant="select" label="仅好友" onClick={onClick} />);
    expect(
      screen
        .getByRole("button", { name: /仅好友/ })
        .getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("nav row is a plain button with a trailing chevron", () => {
    const onClick = vi.fn();
    render(<SettingRow variant="nav" label="已拉黑" onClick={onClick} />);
    const row = screen.getByRole("button", { name: /已拉黑/ });
    expect(row.getAttribute("aria-pressed")).toBeNull();
    fireEvent.click(row);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("does not fire while disabled", () => {
    const onClick = vi.fn();
    render(
      <SettingRow variant="nav" label="已拉黑" disabled onClick={onClick} />,
    );
    const row = screen.getByRole("button", {
      name: /已拉黑/,
    }) as HTMLButtonElement;
    expect(row.disabled).toBe(true);
    fireEvent.click(row);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("clickable card rows reuse Card's interactive chrome", () => {
    render(<SettingRow variant="nav" label="已拉黑" onClick={() => {}} />);
    const cls = screen.getByRole("button", { name: /已拉黑/ }).className;
    for (const token of cardVariantClass.interactive.split(" ")) {
      expect(cls).toContain(token);
    }
  });

  it("selected card rows drop the hover tint for the chosen chrome", () => {
    render(
      <SettingRow variant="select" label="浅色" selected onClick={() => {}} />,
    );
    const cls = screen.getByRole("button", { name: /浅色/ }).className;
    expect(cls).toContain("border-primary/40");
    expect(cls).toContain("bg-primary/5");
    expect(cls).not.toContain("hover:bg-accent/40");
  });

  it("list rows can carry a hairline; bare rows carry no chrome", () => {
    const { container, rerender } = render(
      <SettingRow surface="list" label="今日成本" value="¥1.20" divider />,
    );
    let cls = (container.firstElementChild as HTMLElement).className;
    expect(cls).toContain("border-t");
    expect(cls).toContain("px-4");

    rerender(<SettingRow surface="bare" label="API 版本" value="0.9.0" />);
    cls = (container.firstElementChild as HTMLElement).className;
    expect(cls).not.toContain("rounded-xl");
    expect(cls).not.toContain("px-4");
  });

  it("renders a leading slot", () => {
    render(
      <SettingRow
        variant="select"
        label="跟随系统"
        leading={<span data-testid="icon" />}
        onClick={() => {}}
      />,
    );
    expect(screen.getByTestId("icon")).toBeTruthy();
  });
});
