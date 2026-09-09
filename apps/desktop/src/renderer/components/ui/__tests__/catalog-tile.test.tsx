// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CATALOG_GRID_CLASS, CatalogTile } from "../catalog-tile";

afterEach(cleanup);

describe("CatalogTile", () => {
  it("shows identity, description, accessory and tags", () => {
    render(
      <CatalogTile
        icon={<span>icon</span>}
        colorVar="--tools"
        title="商店"
        subtitle="作者甲"
        description="一键安装技能"
        accessory={<span>已装</span>}
        tags={<span>技能</span>}
      />,
    );
    expect(screen.getByText("商店")).toBeTruthy();
    expect(screen.getByText("作者甲")).toBeTruthy();
    expect(screen.getByText("一键安装技能")).toBeTruthy();
    expect(screen.getByText("已装")).toBeTruthy();
    expect(screen.getByText("技能")).toBeTruthy();
  });

  it("keeps document order: title, description, tags, footer", () => {
    render(
      <CatalogTile
        icon={<span>icon</span>}
        colorVar="--tools"
        title="合同审查"
        description="审合同"
        tags={<span>官方模板</span>}
        footer={<span>次要动作</span>}
      />,
    );
    const title = screen.getByText("合同审查");
    const description = screen.getByText("审合同");
    const tags = screen.getByText("官方模板");
    const footer = screen.getByText("次要动作");
    expect(
      title.compareDocumentPosition(description) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      description.compareDocumentPosition(tags) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      tags.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
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

  it("renders extra copy under the title", () => {
    render(
      <CatalogTile
        icon={<span>icon</span>}
        colorVar="--tools"
        title="web_search"
        description="联网检索"
      >
        <span>展开区</span>
      </CatalogTile>,
    );
    expect(screen.getByText("web_search")).toBeTruthy();
    expect(screen.getByText("联网检索")).toBeTruthy();
    expect(screen.getByText("展开区")).toBeTruthy();
  });

  it("sizes shelf columns to fill the canvas", () => {
    expect(CATALOG_GRID_CLASS).toContain("1fr");
  });

  it("muted tiles are not buttons even with onClick", () => {
    const onClick = vi.fn();
    render(
      <CatalogTile
        icon={<span>icon</span>}
        colorVar="--tools"
        title="文档"
        muted
        onClick={onClick}
      />,
    );
    expect(screen.queryByRole("button", { name: "文档" })).toBeNull();
    expect(screen.getByText("文档")).toBeTruthy();
    fireEvent.click(screen.getByText("文档"));
    expect(onClick).not.toHaveBeenCalled();
  });
});
