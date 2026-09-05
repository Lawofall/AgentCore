// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CatalogTile } from "../catalog-tile";

afterEach(cleanup);

describe("CatalogTile", () => {
  it("shows title, description and optional badge", () => {
    render(
      <CatalogTile
        icon={<span>icon</span>}
        colorVar="--tools"
        title="商店"
        description="一键安装技能"
        badge={<span>已装</span>}
      />,
    );
    expect(screen.getByText("商店")).toBeTruthy();
    expect(screen.getByText("一键安装技能")).toBeTruthy();
    expect(screen.getByText("已装")).toBeTruthy();
  });

  it("invokes onClick from the tile button", () => {
    const onClick = vi.fn();
    render(
      <CatalogTile
        icon={<span>icon</span>}
        colorVar="--tools"
        title="合同审查"
        onClick={onClick}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "合同审查" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
