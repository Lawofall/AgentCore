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
import type { CapabilityTool } from "@/services/capabilities";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

function emptyRail(over: Partial<PromptRail> = {}): PromptRail {
  return {
    constitution: [],
    memory: [],
    alwaysMine: [],
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

function identityItem(ceoIdentity: string): PromptCatalogItem {
  return {
    id: "identity",
    kind: "identity",
    group: "factory",
    label: "角色身份",
    depth: 0,
    ceoIdentity,
    nestedIdentity: "",
    leafIdentity: "",
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
    memoryKind: null,
    listable: applyMode !== "always",
    disputed: false,
    alwaysChars: applyMode === "always" ? content.length : null,
    parentId: null,
  };
}

function capTool(name: string, resident: boolean): CapabilityTool {
  return {
    name,
    face: "file",
    resident,
    summary: name,
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
    constitution: [sharedItem("aaaa"), identityItem("xxxxxxxxxxxx")],
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
        tocGroup: "",
        parentId: null,
        skill: {
          name: "thin_skill",
          summary: "薄技能",
          body: "thin-body",
          group: "",
        },
      },
    ],
    tools: [toolItem("file_read", true), toolItem("host", false)],
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
      connectors={[]}
      connectorError={null}
      showConnectors={false}
      creatingFolder={false}
      busy={false}
      installedCopyIds={new Set()}
      onOpenItem={vi.fn()}
      onOpenUpdates={vi.fn()}
      onCreateMine={vi.fn()}
      onStartCreateFolder={vi.fn()}
      onSubmitCreateFolder={vi.fn()}
      onCancelCreateFolder={vi.fn()}
      onAddConnector={null}
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
  it("常驻叶子直接铺成货架卡，点开对应条目", () => {
    const onOpenItem = vi.fn();
    renderOverview({ onOpenItem });
    fireEvent.click(screen.getByRole("button", { name: "全员共享准则" }));
    fireEvent.click(screen.getByRole("button", { name: "角色身份" }));
    fireEvent.click(screen.getByRole("button", { name: "短约束" }));
    expect(onOpenItem.mock.calls.map((call) => call[0])).toEqual([
      "shared",
      "identity",
      mineCatalogId("rule"),
    ]);
    expect(
      within(screen.getByTestId("prompt-rail-always")).queryByText("file_read"),
    ).toBeNull();
    expect(screen.queryByTestId("prompt-resident-bar")).toBeNull();
    expect(screen.getByTestId("prompt-overview").textContent).not.toMatch(/%/);
  });

  it("空核也留卡，点得开", () => {
    const onOpenItem = vi.fn();
    renderOverview({
      rail: emptyRail({
        constitution: [sharedItem(""), identityItem("")],
      }),
      onOpenItem,
    });
    fireEvent.click(screen.getByRole("button", { name: "全员共享准则" }));
    fireEvent.click(screen.getByRole("button", { name: "角色身份" }));
    expect(onOpenItem.mock.calls.map((call) => call[0])).toEqual([
      "shared",
      "identity",
    ]);
    expect(screen.queryByTestId("prompt-rail-official")).toBeNull();
    expect(screen.queryByTestId("prompt-rail-tools")).toBeNull();
  });

  it("按需叶子铺在概览上，夹只当区标题，最近学到走开弹窗入口", () => {
    const onOpenItem = vi.fn();
    const onOpenUpdates = vi.fn();
    renderOverview({ onOpenItem, onOpenUpdates });
    expect(screen.getByRole("heading", { name: "其他" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "合同审查" })).toBeTruthy();
    expect(
      within(screen.getByTestId("prompt-rail-on-demand")).getByRole("heading", {
        name: "官方",
      }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "薄技能" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "合同审查" }));
    fireEvent.click(screen.getByRole("button", { name: "薄技能" }));
    fireEvent.click(
      within(screen.getByTestId("prompt-rail-tools")).getByRole("button", {
        name: "host",
      }),
    );
    fireEvent.click(
      within(screen.getByTestId("prompt-overview-updates")).getByRole(
        "button",
        { name: "最近学到" },
      ),
    );
    expect(onOpenItem.mock.calls.map((call) => call[0])).toEqual([
      mineCatalogId("d1"),
      skillCatalogId("thin_skill"),
      toolCatalogId("host"),
    ]);
    expect(onOpenUpdates).toHaveBeenCalled();
    expect(screen.queryByTestId("memory-updates-view")).toBeNull();
    expect(screen.getByTestId("prompt-overview").textContent).not.toMatch(/%/);
  });

  it("按需先铺我的夹再官方；工具与连接器跟在两轨后面", () => {
    renderOverview({
      showConnectors: true,
      connectors: [
        { id: "connector:fs", label: "Filesystem", description: "npx" },
      ],
      onAddConnector: vi.fn(),
    });
    const always = screen.getByTestId("prompt-rail-always");
    const onDemand = screen.getByTestId("prompt-rail-on-demand");
    const create = screen.getByTestId("prompt-rail-create");
    const folders = screen.getByTestId("my-skills");
    const official = screen.getByTestId("prompt-rail-official");
    const tools = screen.getByTestId("prompt-rail-tools");
    const connectors = screen.getByTestId("prompt-rail-connectors");
    expect(
      always.compareDocumentPosition(onDemand) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      onDemand.compareDocumentPosition(tools) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      create.compareDocumentPosition(folders) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      folders.compareDocumentPosition(official) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(onDemand.contains(connectors)).toBe(false);
    expect(tools.contains(connectors)).toBe(true);
    expect(
      connectors.compareDocumentPosition(
        screen.getByTestId("prompt-rail-tools-file"),
      ) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      within(create).getByRole("button", { name: "新建条目" }),
    ).toBeTruthy();
    expect(within(create).getByRole("button", { name: "新建夹" })).toBeTruthy();
    expect(tools.querySelector(".overflow-x-auto")).toBeNull();
    expect(official.querySelector(".overflow-x-auto")).toBeNull();
    expect(tools.querySelector(".grid")).toBeTruthy();
    expect(official.querySelector(".grid")).toBeTruthy();
    expect(
      onDemand.contains(screen.getByTestId("prompt-overview-updates")),
    ).toBe(false);
    expect(screen.getByRole("heading", { name: "常驻" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "按需" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "工具" })).toBeTruthy();
    expect(screen.getByText("每回合都带着")).toBeTruthy();
    expect(screen.getByText("用到才翻")).toBeTruthy();
  });

  it("空夹是拖放空卡，不是通栏虚线", () => {
    renderOverview({
      rail: emptyRail({
        folders: [
          {
            id: "folder:law",
            name: "法律合规",
            source: "user",
            documentId: "law",
            items: [],
          },
        ],
      }),
    });
    expect(screen.getByText("拖到这里")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "拖到这里" })).toBeNull();
  });
});
