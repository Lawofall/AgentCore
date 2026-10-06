// @vitest-environment jsdom
import { PromptOverview } from "@/components/tools/PromptOverview";
import {
  type PromptCatalogItem,
  type PromptRail,
  type PromptRailFolder,
  mineCatalogId,
  skillCatalogId,
  toolCatalogId,
} from "@/lib/promptCatalog";
import { PROMPT_DRAG_MIME } from "@/lib/promptCatalogDrag";
import type { CapabilityTool } from "@/services/capabilities";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import type { ComponentProps, ReactNode } from "react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

function emptyRail(over: Partial<PromptRail> = {}): PromptRail {
  return {
    constitution: [],
    alwaysMine: [],
    pathMine: [],
    folders: [],
    official: [],
    tools: [],
    ...over,
  };
}

function sharedItem(text: string): PromptCatalogItem {
  return {
    id: "shared",
    kind: "shared",
    group: "factory",
    label: "全员共享准则",
    depth: 0,
    text,
  };
}

function mineItem(over: {
  id: string;
  label: string;
  content?: string;
  applyMode?: "always" | "on_demand";
}): Extract<PromptCatalogItem, { kind: "mine" }> {
  const content = over.content ?? "";
  const applyMode = over.applyMode ?? "on_demand";
  return {
    id: mineCatalogId(over.id),
    kind: "mine",
    group: "mine",
    label: over.label,
    depth: 0,
    mineId: over.id,
    description: "",
    content,
    version: "v1",
    applyMode,
    aiMaintained: false,
    listable: applyMode !== "always",
    disputed: false,
    alwaysChars: applyMode === "always" ? content.length : null,
    parentId: null,
  };
}

function capTool(name: string, resident: boolean): CapabilityTool {
  const summaries: Record<string, string> = {
    read: "读工作区文件",
    host: "这台电脑。",
  };
  return {
    name,
    face: "file",
    resident,
    summary: summaries[name] ?? name,
    blurb:
      name === "read"
        ? "打开文本、代码或图片，看里面写了什么"
        : "看本机屏幕、键鼠和已打开的应用",
    description: name,
    parameters: {},
    approval: "never",
    available_to: ["ceo", "worker"],
  };
}

function toolItem(
  name: string,
  resident: boolean,
): Extract<PromptCatalogItem, { kind: "tool" }> {
  return {
    id: toolCatalogId(name),
    kind: "tool",
    group: "factory",
    label: name,
    depth: 0,
    parentId: null,
    tool: capTool(name, resident),
  };
}

const otherFolder: PromptRailFolder = {
  id: "virtual:其他",
  name: "其他",
  source: "other",
  documentId: null,
  items: [],
};

function filledRail(): PromptRail {
  return emptyRail({
    constitution: [sharedItem("aaaa")],
    alwaysMine: [
      mineItem({
        id: "rule",
        label: "短约束",
        content: "hellohello",
        applyMode: "always",
      }),
    ],
    folders: [
      {
        id: "folder:other",
        name: "其他",
        source: "other",
        documentId: null,
        items: [
          mineItem({ id: "d1", label: "合同审查", applyMode: "on_demand" }),
        ],
      },
    ],
    official: [
      {
        id: skillCatalogId("thin_skill"),
        kind: "skill",
        group: "factory",
        label: "薄技能",
        depth: 0,
        tocGroup: "编排",
        parentId: null,
        skill: {
          name: "thin_skill",
          summary: "薄技能",
          body: "thin-body",
          group: "编排",
          blurb: "写一条按需薄技能",
        },
      },
    ],
    tools: [toolItem("read", true), toolItem("host", true)],
  });
}

function renderOverview(
  over: Partial<ComponentProps<typeof PromptOverview>> = {},
) {
  const noop = () => {};
  return render(
    <PromptOverview
      rail={filledRail()}
      selectedId={null}
      dropDest={null}
      otherFolder={otherFolder}
      renamingFolderId={null}
      busy={false}
      onOpenItem={vi.fn()}
      onCreateMine={vi.fn()}
      onSubmitRenameFolder={vi.fn()}
      onCancelRenameFolder={vi.fn()}
      onAcceptAlwaysDrag={noop}
      onDropAlways={noop}
      onAcceptFolderDrag={noop}
      onDropFolder={noop}
      onRejectDrag={noop}
      {...over}
    />,
  );
}

describe("PromptOverview", () => {
  it("必带叶子铺成矮卡，右上角标必带，点开对应条目", () => {
    const onOpenItem = vi.fn();
    renderOverview({ onOpenItem });
    const shelf = screen.getByTestId("prompt-rail-shelf");
    fireEvent.click(within(shelf).getByRole("button", { name: "短约束" }));
    expect(onOpenItem.mock.calls.map((call) => call[0])).toEqual([
      mineCatalogId("rule"),
    ]);
    expect(within(shelf).getByText("必带")).toBeTruthy();
    expect(
      within(screen.getByTestId("prompt-rail-constitution")).queryByRole(
        "button",
        { name: "短约束" },
      ),
    ).toBeNull();
    expect(screen.queryByRole("heading", { name: "必带" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "按需" })).toBeNull();
    expect(screen.queryByRole("button", { name: "读工作区文件" })).toBeNull();
    expect(screen.queryByTestId("prompt-tile-tool:read")).toBeNull();
    expect(screen.queryByTestId("prompt-resident-bar")).toBeNull();
    expect(screen.getByTestId("prompt-overview").textContent).not.toMatch(/%/);
  });

  it("不拖的时候不留必带虚线，拖过才出现落点", () => {
    renderOverview({
      rail: emptyRail(),
    });
    expect(screen.queryByRole("heading", { name: "必带" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "按需" })).toBeNull();
    expect(screen.queryByText("0 条")).toBeNull();
    expect(screen.queryByText("拖一条进来，下一回合就会带上。")).toBeNull();
    expect(screen.queryByText("还没有夹。")).toBeNull();
    expect(screen.queryByTestId("prompt-rail-always")).toBeNull();
    expect(screen.queryByTestId("prompt-rail-official")).toBeNull();
    expect(screen.queryByTestId("prompt-rail-tools")).toBeNull();
    expect(screen.queryByTestId("prompt-rail-how")).toBeNull();
    expect(screen.queryByTestId("prompt-rail-constitution")).toBeNull();
    expect(screen.queryByTestId("prompt-rail-factory")).toBeNull();
    expect(screen.queryByTestId("prompt-rail-always-tools")).toBeNull();
    expect(screen.queryByTestId("prompt-rail-on-demand-tools")).toBeNull();
    fireEvent.dragOver(screen.getByTestId("prompt-rail-shelf"), {
      dataTransfer: { types: [PROMPT_DRAG_MIME] },
    });
    const strip = screen.getByText("松手后下一回合会带上");
    expect(strip.className).toContain("min-h-11");
    expect(strip.className).not.toContain("justify-center");
    expect(strip.className).not.toContain("text-center");
  });

  it("没归夹的按需铺在根上，不画其他", () => {
    const onOpenItem = vi.fn();
    renderOverview({ onOpenItem });
    expect(screen.queryByRole("heading", { name: "其他" })).toBeNull();
    expect(screen.queryByText("其他")).toBeNull();
    const shelf = screen.getByTestId("prompt-rail-shelf");
    expect(
      within(shelf).getByRole("button", { name: "合同审查" }),
    ).toBeTruthy();
    expect(within(shelf).queryByText("按需")).toBeNull();
    fireEvent.click(within(shelf).getByRole("button", { name: "合同审查" }));
    expect(onOpenItem.mock.calls.map((call) => call[0])).toEqual([
      mineCatalogId("d1"),
    ]);
    expect(screen.queryByTestId("memory-updates-view")).toBeNull();
    expect(screen.getByTestId("prompt-overview").textContent).not.toMatch(/%/);
  });

  it("出厂卡和必带、散落按需在同一张货架", () => {
    renderOverview();
    const shelf = screen.getByTestId("prompt-rail-shelf");
    const factory = screen.getByTestId("prompt-rail-factory");
    expect(screen.queryByTestId("prompt-rail-official")).toBeNull();
    expect(screen.queryByTestId("prompt-rail-tools")).toBeNull();
    expect(screen.queryByTestId("prompt-rail-how")).toBeNull();
    expect(screen.queryByTestId("prompt-rail-on-demand")).toBeNull();
    expect(screen.queryByTestId("prompt-rail-on-demand-tools")).toBeNull();
    expect(shelf.contains(factory)).toBe(true);
    expect(
      within(factory).getByRole("button", { name: "写一条按需薄技能" }),
    ).toBeTruthy();
    expect(within(factory).getByText("薄技能")).toBeTruthy();
    expect(within(factory).getByText("官方")).toBeTruthy();
    expect(within(factory).queryByText("合同审查")).toBeNull();
    expect(within(factory).queryByText("短约束")).toBeNull();
    expect(factory.querySelector("[draggable='true']")).toBeNull();
    expect(within(shelf).getByText("必带")).toBeTruthy();
    expect(within(shelf).queryByText("按需")).toBeNull();
    expect(screen.queryByRole("heading", { name: "工具" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "文件" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "出厂" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "出厂项" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "官方" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "必带" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "按需" })).toBeNull();
    expect(screen.queryByRole("button", { name: "薄技能" })).toBeNull();
    expect(screen.queryByRole("button", { name: "读工作区文件" })).toBeNull();
    expect(screen.queryByRole("button", { name: "这台电脑。" })).toBeNull();
    expect(screen.queryByRole("button", { name: "新建夹" })).toBeNull();
    expect(screen.queryByTestId("prompt-overview-updates")).toBeNull();
    expect(screen.queryByRole("heading", { name: "连接器" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "手上的工具" })).toBeNull();
    expect(
      screen.queryByText("打开文本、代码或图片，看里面写了什么"),
    ).toBeNull();
    expect(screen.getByText("写一条按需薄技能")).toBeTruthy();
    expect(screen.queryByText("每回合都带着")).toBeNull();
    expect(screen.queryByText("用到才翻")).toBeNull();
    const constitution = screen.getByTestId("prompt-rail-constitution");
    expect(
      constitution.compareDocumentPosition(shelf) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("出厂开关不占货架，点卡只打开这条", () => {
    const onOpenItem = vi.fn();
    renderOverview({
      onOpenItem,
      factoryControl: {
        present: true,
        canToggle: true,
        onPresentChange: vi.fn(),
      },
    });
    const shelf = screen.getByTestId("prompt-rail-shelf");
    expect(screen.queryByRole("heading", { name: "出厂" })).toBeNull();
    expect(within(shelf).queryByRole("switch", { name: "出厂" })).toBeNull();
    fireEvent.click(
      within(shelf).getByRole("button", { name: "写一条按需薄技能" }),
    );
    expect(onOpenItem.mock.calls.map((call) => call[0])).toEqual([
      skillCatalogId("thin_skill"),
    ]);
    expect(within(shelf).queryByText("thin_skill")).toBeNull();
  });

  it("出厂卡标题独占一行，说明停在第一句", () => {
    renderOverview({
      rail: emptyRail({
        official: [
          {
            id: skillCatalogId("data_file_landing"),
            kind: "skill",
            group: "factory",
            label: "data_file_landing",
            depth: 0,
            tocGroup: "交付",
            parentId: null,
            skill: {
              name: "data_file_landing",
              summary:
                "用户要把数据文件交成可打开的表时才查阅。成篇、做页面、问产品不查阅。",
              body: "how",
              group: "交付",
              blurb: "把数据文件整理成打开扫得懂的表",
            },
          },
        ],
      }),
    });
    const factory = screen.getByTestId("prompt-rail-factory");
    const title = within(factory).getByRole("heading", {
      name: "把数据文件整理成打开扫得懂的表",
    });
    expect(
      within(factory).getByText("用户要把数据文件交成可打开的表时才查阅。"),
    ).toBeTruthy();
    expect(factory.textContent).not.toContain("成篇");
    expect(title.nextElementSibling?.textContent).toContain("官方");
    expect(screen.queryByText("0 条")).toBeNull();
  });

  it("空夹在根上是一格，点进去才写拖到这里", () => {
    function Harness({
      children,
    }: {
      children: (
        openId: string | null,
        open: (id: string) => void,
        close: () => void,
      ) => ReactNode;
    }) {
      const [openId, setOpenId] = useState<string | null>(null);
      return <>{children(openId, setOpenId, () => setOpenId(null))}</>;
    }
    const rail = emptyRail({
      folders: [
        {
          id: "folder:law",
          name: "法律合规",
          source: "user",
          documentId: "law",
          items: [
            mineItem({ id: "d1", label: "合同审查", applyMode: "on_demand" }),
          ],
        },
      ],
    });
    render(
      <Harness>
        {(openId, open, close) => (
          <PromptOverview
            rail={rail}
            selectedId={null}
            dropDest={null}
            otherFolder={otherFolder}
            busy={false}
            openFolderId={openId}
            onOpenFolder={open}
            onCloseFolder={close}
            onOpenItem={vi.fn()}
            onCreateMine={vi.fn()}
            onSubmitRenameFolder={vi.fn()}
            onCancelRenameFolder={vi.fn()}
            onAcceptAlwaysDrag={() => {}}
            onDropAlways={() => {}}
            onAcceptFolderDrag={() => {}}
            onDropFolder={() => {}}
            onRejectDrag={() => {}}
          />
        )}
      </Harness>,
    );
    expect(screen.queryByText("拖到这里")).toBeNull();
    expect(screen.queryByRole("button", { name: "合同审查" })).toBeNull();
    expect(screen.getByText("1 条")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "法律合规" }));
    expect(screen.getByRole("button", { name: "合同审查" })).toBeTruthy();
    expect(screen.queryByText("拖到这里")).toBeNull();
    expect(screen.queryByText("按需")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "返回" }));
    expect(screen.queryByRole("button", { name: "合同审查" })).toBeNull();
    expect(screen.getByRole("button", { name: "法律合规" })).toBeTruthy();
  });

  it("空夹点进去写拖到这里，不是按钮", () => {
    function Harness() {
      const [openId, setOpenId] = useState<string | null>("folder:law");
      return (
        <PromptOverview
          rail={emptyRail({
            folders: [
              {
                id: "folder:law",
                name: "法律合规",
                source: "user",
                documentId: "law",
                items: [],
              },
            ],
          })}
          selectedId={null}
          dropDest={null}
          otherFolder={otherFolder}
          busy={false}
          openFolderId={openId}
          onOpenFolder={setOpenId}
          onCloseFolder={() => setOpenId(null)}
          dragging
          onOpenItem={vi.fn()}
          onCreateMine={vi.fn()}
          onSubmitRenameFolder={vi.fn()}
          onCancelRenameFolder={vi.fn()}
          onAcceptAlwaysDrag={() => {}}
          onDropAlways={() => {}}
          onAcceptFolderDrag={() => {}}
          onDropFolder={() => {}}
          onRejectDrag={() => {}}
        />
      );
    }
    render(<Harness />);
    expect(screen.getByText("拖到这里")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "拖到这里" })).toBeNull();
    expect(screen.getByText("松手后下一回合会带上")).toBeTruthy();
    expect(screen.getByText("松手后不归夹")).toBeTruthy();
    expect(screen.queryByText("0 条")).toBeNull();
  });

  it("必带矮卡不塞准则正文", () => {
    renderOverview();
    expect(screen.queryByText("aaaa")).toBeNull();
    expect(
      within(screen.getByTestId("prompt-rail-shelf")).queryByRole("button", {
        name: "全员共享准则",
      }),
    ).toBeNull();
    expect(screen.getByText("每回合都在的工作宪法")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "薄技能" })).toBeNull();
    expect(screen.queryByTestId("prompt-rail-official")).toBeNull();
    expect(screen.queryByTestId("prompt-tile-tool:read")).toBeNull();
    expect(screen.queryByText("本机")).toBeNull();
  });

  it("准则矮卡在出厂上面，点开读准则，不铺工具大卡", () => {
    const onOpenItem = vi.fn();
    renderOverview({ onOpenItem });
    fireEvent.click(screen.getByRole("button", { name: "全员共享准则" }));
    expect(onOpenItem.mock.calls.map((call) => call[0])).toEqual(["shared"]);
    const constitution = screen.getByTestId("prompt-rail-constitution");
    expect(within(constitution).getByText("每回合都在的工作宪法")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "准则" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "必带" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "按需" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "提示词" })).toBeNull();
    expect(screen.queryByTestId("prompt-rail-tools")).toBeNull();
    expect(screen.queryByTestId("prompt-tile-tool:read")).toBeNull();
    expect(screen.queryByText("aaaa")).toBeNull();
    const shelf = screen.getByTestId("prompt-rail-shelf");
    expect(
      constitution.compareDocumentPosition(shelf) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      within(shelf).getByRole("button", { name: "写一条按需薄技能" }),
    ).toBeTruthy();
  });

  it("空基座不留准则卡", () => {
    renderOverview({
      rail: emptyRail({
        constitution: [sharedItem("")],
      }),
    });
    expect(screen.queryByRole("button", { name: "全员共享准则" })).toBeNull();
    expect(screen.queryByText("每回合都在的工作宪法")).toBeNull();
    expect(screen.queryByTestId("prompt-rail-constitution")).toBeNull();
    expect(screen.queryByTestId("prompt-rail-tools")).toBeNull();
  });
});
