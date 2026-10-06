// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ASSEMBLY_CARD_GRID_CLASS,
  CATALOG_GRID_CLASS,
  CatalogTile,
} from "../catalog-tile";

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

  it("reserves two description lines even when copy is missing", () => {
    const { container } = render(
      <CatalogTile icon={<span>icon</span>} colorVar="--tools" title="商店" />,
    );
    const slot = container.querySelector("[data-slot=description]");
    expect(slot).toBeTruthy();
    expect(slot?.className).toContain("min-h-[2lh]");
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

  it("keeps an accessory button from opening the tile", () => {
    const onClick = vi.fn();
    const onAccessory = vi.fn();
    render(
      <CatalogTile
        icon={<span>icon</span>}
        colorVar="--tools"
        title="改文件"
        onClick={onClick}
        accessory={
          <button type="button" onClick={onAccessory}>
            开关
          </button>
        }
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "开关" }));
    expect(onAccessory).toHaveBeenCalledTimes(1);
    expect(onClick).not.toHaveBeenCalled();
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
    expect(CATALOG_GRID_CLASS).toContain("auto-fill");
    expect(CATALOG_GRID_CLASS).toContain("240px");
    expect(ASSEMBLY_CARD_GRID_CLASS).toContain("200px");
  });

  it("compact card keeps one line and a side control", () => {
    const onClick = vi.fn();
    const onAccessory = vi.fn();
    render(
      <CatalogTile
        density="compact"
        title="改文件"
        description="在这个文件夹里写、改、删文件。"
        onClick={onClick}
        accessory={
          <button type="button" onClick={onAccessory}>
            开关
          </button>
        }
      />,
    );
    const description = screen.getByText("在这个文件夹里写、改、删文件。");
    expect(description.className).toContain("truncate");
    expect(description.className).not.toContain("line-clamp-2");
    fireEvent.click(screen.getByRole("button", { name: "开关" }));
    expect(onAccessory).toHaveBeenCalledTimes(1);
    expect(onClick).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "改文件" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("description placement keeps the title clear of the mark", () => {
    render(
      <CatalogTile
        density="compact"
        accessoryPlacement="description"
        title="把数据文件整理成打开扫得懂的表"
        description="用户要把数据文件交成可打开的表时才查阅。"
        accessory={<span>官方</span>}
        onClick={() => {}}
      />,
    );
    const title = screen.getByRole("heading", {
      name: "把数据文件整理成打开扫得懂的表",
    });
    const description = screen.getByText(
      "用户要把数据文件交成可打开的表时才查阅。",
    );
    expect(description.className).toContain("line-clamp-2");
    expect(description.className).not.toContain("truncate");
    expect(title.nextElementSibling?.textContent).toContain("官方");
    expect(title.parentElement?.contains(description)).toBe(true);
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
