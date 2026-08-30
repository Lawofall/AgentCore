// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EmptyHint } from "../empty-hint";

describe("EmptyHint", () => {
  it("renders title and optional hint as a status", () => {
    render(<EmptyHint title="还没有文件夹" hint="用右上角「+」建一个" />);
    const status = screen.getByRole("status");
    expect(status.textContent).toContain("还没有文件夹");
    expect(status.textContent).toContain("用右上角「+」建一个");
  });

  it("omits hint and hosts an action when given", () => {
    render(
      <EmptyHint
        title="还没有任务"
        action={<button type="button">新建任务</button>}
      />,
    );
    expect(screen.getByRole("status").textContent).toBe("还没有任务新建任务");
    expect(screen.getByRole("button", { name: "新建任务" })).toBeTruthy();
  });
});
