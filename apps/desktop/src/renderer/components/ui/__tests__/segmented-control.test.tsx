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
  { value: "ceo", label: "主 Agent" },
  { value: "nested", label: "可再委派的队员" },
  { value: "leaf", label: "叶子队员" },
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
        aria-label="角色身份"
        value="ceo"
        onChange={vi.fn()}
        items={THREE}
      />,
    );
    const list = screen.getByRole("tablist", { name: "角色身份" });
    expect(list.className).toContain("overflow-x-auto");
    expect(screen.getByRole("tab", { name: "主 Agent" }).className).toContain(
      "shrink-0",
    );
  });

  it("forwards id and aria-controls onto each tab", () => {
    render(
      <SegmentedControl
        aria-label="角色身份"
        value="ceo"
        onChange={vi.fn()}
        items={THREE.map((item) => ({
          ...item,
          id: `role-tab-${item.value}`,
          "aria-controls": "role-identity-panel",
        }))}
      />,
    );
    const tab = screen.getByRole("tab", { name: "主 Agent" });
    expect(tab.id).toBe("role-tab-ceo");
    expect(tab.getAttribute("aria-controls")).toBe("role-identity-panel");
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
        useState<(typeof THREE)[number]["value"]>("ceo");
      return (
        <SegmentedControl
          aria-label="角色身份"
          value={value}
          onChange={setValue}
          items={THREE}
        />
      );
    }
    render(<Harness />);
    fireEvent.click(screen.getByRole("tab", { name: "叶子队员" }));
    expect(
      screen
        .getByRole("tab", { name: "叶子队员" })
        .getAttribute("aria-selected"),
    ).toBe("true");
    expect(
      screen
        .getByRole("tab", { name: "主 Agent" })
        .getAttribute("aria-selected"),
    ).toBe("false");
  });
});
