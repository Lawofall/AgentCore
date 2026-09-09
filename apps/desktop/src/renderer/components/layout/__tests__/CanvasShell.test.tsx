// @vitest-environment jsdom
import { CanvasShell } from "@/components/layout/CanvasShell";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  cleanup();
});

describe("CanvasShell", () => {
  it("renders back IconButton, title slot, status, actions, banner, and canvas", () => {
    const onBack = vi.fn();
    render(
      <CanvasShell
        backAriaLabel="返回工作流列表"
        onBack={onBack}
        title={<input aria-label="工作流标题" defaultValue="竞品调研" />}
        status="v2"
        actions={<button type="button">保存</button>}
        banner={<p>校验未通过</p>}
      >
        <div data-testid="canvas">画布</div>
      </CanvasShell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "返回工作流列表" }));
    expect(onBack).toHaveBeenCalledTimes(1);
    expect(screen.getByDisplayValue("竞品调研")).toBeTruthy();
    expect(screen.getByText("v2")).toBeTruthy();
    expect(screen.getByRole("button", { name: "保存" })).toBeTruthy();
    expect(screen.getByText("校验未通过")).toBeTruthy();
    expect(screen.getByTestId("canvas")).toBeTruthy();
  });
});
