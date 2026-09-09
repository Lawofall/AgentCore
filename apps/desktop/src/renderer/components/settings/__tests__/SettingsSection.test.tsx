// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SettingsSection, SettingsStack } from "../SettingsSection";

describe("SettingsSection", () => {
  it("renders heading, description, action and content", () => {
    render(
      <SettingsSection
        title="登录设备"
        description="查看当前账号的活跃登录。"
        action={<button type="button">退出其他所有设备</button>}
      >
        <p>内容</p>
      </SettingsSection>,
    );
    expect(screen.getByRole("heading", { name: "登录设备" })).toBeTruthy();
    expect(screen.getByText("查看当前账号的活跃登录。")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "退出其他所有设备" }),
    ).toBeTruthy();
    expect(screen.getByText("内容")).toBeTruthy();
  });

  it("uses a level-2 heading so subpages keep one document outline", () => {
    render(<SettingsSection title="主题">内容</SettingsSection>);
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("主题");
  });

  it("offers the two title sizes and a danger tone", () => {
    const { container, rerender } = render(
      <SettingsSection title="主题" titleSize="base" />,
    );
    expect(container.querySelector("h2")?.className).toContain("text-base");

    rerender(<SettingsSection title="危险区域" tone="danger" />);
    expect(container.querySelector("h2")?.className).toContain(
      "text-destructive",
    );
  });

  it("draws the top hairline only when divider is set", () => {
    const { container, rerender } = render(
      <SettingsSection title="软件更新" divider />,
    );
    expect(container.querySelector("section")?.className).toContain("border-t");

    rerender(<SettingsSection title="软件更新" />);
    expect(container.querySelector("section")?.className).not.toContain(
      "border-t",
    );
  });

  it("omits the content wrapper when the section is header-only", () => {
    const { container } = render(<SettingsSection title="法律与合规" />);
    expect(container.querySelectorAll("section > div")).toHaveLength(1);
  });

  it("keeps the action when the page header already names the block", () => {
    render(
      <SettingsSection action={<button type="button">新建</button>}>
        列表
      </SettingsSection>,
    );
    expect(screen.queryByRole("heading")).toBeNull();
    expect(screen.getByRole("button", { name: "新建" })).toBeTruthy();
    expect(screen.getByText("列表")).toBeTruthy();
  });
});

describe("SettingsStack", () => {
  it("owns the subpage rhythm below the header", () => {
    const { container } = render(
      <SettingsStack>
        <SettingsSection title="A" />
      </SettingsStack>,
    );
    const stack = container.firstElementChild as HTMLElement;
    expect(stack.className).toContain("mt-6");
    expect(stack.className).toContain("space-y-8");
  });
});
