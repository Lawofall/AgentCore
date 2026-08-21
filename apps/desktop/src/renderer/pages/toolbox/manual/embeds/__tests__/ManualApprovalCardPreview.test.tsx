// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { ManualApprovalCardPreview } from "../ManualApprovalCardPreview";

afterEach(cleanup);

function renderPreview() {
  return render(
    <MemoryRouter>
      <ManualApprovalCardPreview />
    </MemoryRouter>,
  );
}

describe("ManualApprovalCardPreview", () => {
  it("renders without crashing", () => {
    renderPreview();
    expect(screen.getByText("Agent 请求执行")).toBeTruthy();
    expect(screen.getByText("写入文件")).toBeTruthy();
    expect(screen.getByText("允许一次")).toBeTruthy();
    expect(screen.getByText("拒绝")).toBeTruthy();
  });

  it("允许一次后换成痕迹，再试一次回到可点卡", () => {
    renderPreview();
    fireEvent.click(screen.getByRole("button", { name: "允许一次" }));

    expect(screen.getByTestId("manual-approval-demo-trace").textContent).toBe(
      "已允许 · 写入文件",
    );
    expect(screen.queryByText("Agent 请求执行")).toBeNull();
    expect(screen.getByText("演示，不会发给团队")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "再试一次" }));
    expect(screen.getByText("Agent 请求执行")).toBeTruthy();
    expect(screen.getByRole("button", { name: "允许一次" })).toBeTruthy();
  });

  it("拒绝后显示已拒绝痕迹", () => {
    renderPreview();
    fireEvent.click(screen.getByRole("button", { name: "拒绝" }));
    expect(screen.getByTestId("manual-approval-demo-trace").textContent).toBe(
      "已拒绝 · 写入文件",
    );
  });
});
