// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { ManualCheckpointCardPreview } from "../ManualCheckpointCardPreview";

afterEach(cleanup);

function renderPreview() {
  return render(
    <MemoryRouter>
      <ManualCheckpointCardPreview />
    </MemoryRouter>,
  );
}

describe("ManualCheckpointCardPreview", () => {
  it("renders without crashing", () => {
    renderPreview();
    expect(screen.getByText("需要你拍板")).toBeTruthy();
    expect(screen.queryByText(/试点范围定多大？/)).toBeNull();
    expect(screen.getByText("第一批放行范围")).toBeTruthy();
    expect(screen.getByText("先做一个试点（推荐）")).toBeTruthy();
    expect(screen.getByText("提交")).toBeTruthy();
    expect(screen.getByText("取消")).toBeTruthy();
  });

  it("提交后换成结算记录，再试一次回到可点卡", () => {
    renderPreview();
    fireEvent.click(screen.getByText("同业务线 3 个试点"));
    fireEvent.click(screen.getByRole("button", { name: "提交" }));

    expect(screen.queryByText("已按你的决定继续")).toBeNull();
    expect(screen.getByText(/同业务线 3 个试点/)).toBeTruthy();
    expect(screen.queryByText(/我的答复/)).toBeNull();
    expect(screen.queryByRole("button", { name: "提交" })).toBeNull();
    expect(screen.getByText("演示，不会发给团队")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "再试一次" }));
    expect(screen.getByText("需要你拍板")).toBeTruthy();
    expect(screen.getByRole("button", { name: "提交" })).toBeTruthy();
  });

  it("取消后显示已取消本回合，可再试", () => {
    renderPreview();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    expect(screen.getByText("已取消本回合")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "提交" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "再试一次" }));
    expect(screen.getByText("需要你拍板")).toBeTruthy();
  });
});
