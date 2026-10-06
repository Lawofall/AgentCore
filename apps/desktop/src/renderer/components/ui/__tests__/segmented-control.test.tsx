// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SegmentedControl } from "../segmented-control";

afterEach(cleanup);

const TWO = [
  { value: "login", label: "登录" },
  { value: "register", label: "注册" },
] as const;

const THREE = [
  { value: "low", label: "低" },
  { value: "mid", label: "中" },
  { value: "high", label: "高" },
] as const;

describe("SegmentedControl", () => {
  it("lifts the selected segment as a card, not an underline", () => {
    render(
      <SegmentedControl
        aria-label="登录或注册"
        value="register"
        onChange={vi.fn()}
        items={TWO}
      />,
    );
    const list = screen.getByRole("tablist", { name: "登录或注册" });
    expect(list.className).toContain("bg-muted");
    expect(list.className).not.toContain("border-b");
    const selected = screen.getByRole("tab", { name: "注册" });
    expect(selected.getAttribute("aria-selected")).toBe("true");
    expect(selected.className).toContain("bg-card");
    expect(selected.className).toContain("shadow-raised");
    expect(selected.className).not.toContain("bg-accent");
    expect(selected.querySelector('span[aria-hidden="true"]')).toBeNull();
    expect(
      screen.getByRole("tab", { name: "登录" }).getAttribute("aria-selected"),
    ).toBe("false");
  });

  it("keeps three items on a horizontally scrollable track", () => {
    render(
      <SegmentedControl
        aria-label="强度"
        value="low"
        onChange={vi.fn()}
        items={THREE}
      />,
    );
    const list = screen.getByRole("tablist", { name: "强度" });
    expect(list.className).toContain("overflow-x-auto");
    expect(screen.getByRole("tab", { name: "低" }).className).toContain(
      "shrink-0",
    );
  });

  it("forwards id and aria-controls onto each tab", () => {
    render(
      <SegmentedControl
        aria-label="强度"
        value="low"
        onChange={vi.fn()}
        items={THREE.map((item) => ({
          ...item,
          id: `level-tab-${item.value}`,
          "aria-controls": "level-panel",
        }))}
      />,
    );
    const tab = screen.getByRole("tab", { name: "低" });
    expect(tab.id).toBe("level-tab-low");
    expect(tab.getAttribute("aria-controls")).toBe("level-panel");
  });

  it("notifies onChange when a tab is clicked", () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl
        aria-label="登录或注册"
        value="login"
        onChange={onChange}
        items={TWO}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "注册" }));
    expect(onChange).toHaveBeenCalledWith("register");
  });

  it("moves aria-selected after a controlled update", () => {
    function Harness() {
      const [value, setValue] =
        useState<(typeof THREE)[number]["value"]>("low");
      return (
        <SegmentedControl
          aria-label="强度"
          value={value}
          onChange={setValue}
          items={THREE}
        />
      );
    }
    render(<Harness />);
    fireEvent.click(screen.getByRole("tab", { name: "高" }));
    expect(
      screen.getByRole("tab", { name: "高" }).getAttribute("aria-selected"),
    ).toBe("true");
    expect(
      screen.getByRole("tab", { name: "低" }).getAttribute("aria-selected"),
    ).toBe("false");
  });

  it("fits every segment on one line when asked", () => {
    render(
      <SegmentedControl
        aria-label="强度"
        value="low"
        onChange={vi.fn()}
        items={THREE}
        fit
      />,
    );
    const list = screen.getByRole("tablist", { name: "强度" });
    expect(list.className).toContain("overflow-hidden");
    expect(list.className).not.toContain("overflow-x-auto");
    expect(screen.getByRole("tab", { name: "低" }).className).toContain(
      "truncate",
    );
  });
});
