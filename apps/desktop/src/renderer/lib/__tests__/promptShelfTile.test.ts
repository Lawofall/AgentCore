import {
  type PromptCatalogItem,
  mineCatalogId,
  skillCatalogId,
  toolCatalogId,
} from "@/lib/promptCatalog";
import {
  promptConnectorShelfCopy,
  promptItemShelfCopy,
  promptMineShelfOpts,
  promptReadHeaderChips,
} from "@/lib/promptShelfTile";
import type { CapabilityTool } from "@/services/capabilities";
import type { SkillStoreListing } from "@/services/skillStore";
import { describe, expect, it } from "vitest";

function sharedItem(): PromptCatalogItem {
  return {
    id: "shared",
    kind: "shared",
    group: "factory",
    label: "全员共享准则",
    depth: 0,
    text: "you are the constitution",
  };
}

function mineItem(over: {
  label: string;
  description?: string;
  content?: string;
  aiMaintained?: boolean;
  disputed?: boolean;
  applyMode?: "always" | "on_demand";
}): Extract<PromptCatalogItem, { kind: "mine" }> {
  return {
    id: mineCatalogId("x"),
    kind: "mine",
    group: "mine",
    label: over.label,
    depth: 0,
    mineId: "x",
    description: over.description ?? "",
    content: over.content ?? "",
    version: "v1",
    applyMode: over.applyMode ?? "on_demand",
    aiMaintained: over.aiMaintained ?? false,
    listable: true,
    disputed: over.disputed ?? false,
    alwaysChars: null,
    parentId: null,
  };
}

function skillItem(over: {
  summary: string;
  blurb?: string;
  group?: string;
  audience?: string[];
  requiresTools?: string[];
}): Extract<PromptCatalogItem, { kind: "skill" }> {
  return {
    id: skillCatalogId("staffing"),
    kind: "skill",
    group: "factory",
    label: over.summary,
    depth: 0,
    tocGroup: over.group ?? "",
    parentId: null,
    skill: {
      name: "staffing",
      summary: over.summary,
      body: "how",
      group: over.group ?? "",
      blurb: over.blurb ?? "",
      audience: over.audience,
      requires_tools: over.requiresTools ?? [],
    },
  };
}

function toolItem(over: {
  resident: boolean;
  approval?: CapabilityTool["approval"];
  availableTo?: string[];
  blurb?: string;
}): Extract<PromptCatalogItem, { kind: "tool" }> {
  const tool: CapabilityTool = {
    name: "read",
    face: "file",
    resident: over.resident,
    summary: "读工作区文件",
    blurb: over.blurb ?? "打开文本、代码或图片，看里面写了什么",
    description: "读工作区文件",
    parameters: {},
    approval: over.approval ?? "never",
    available_to: over.availableTo ?? ["ceo", "worker"],
  };
  return {
    id: toolCatalogId(tool.name),
    kind: "tool",
    group: "factory",
    label: tool.name,
    depth: 0,
    parentId: null,
    tool,
  };
}

function listing(over: Partial<SkillStoreListing>): SkillStoreListing {
  return {
    id: "listing-1",
    name: "合同审查",
    description: "审合同时用",
    author: "me",
    version: "1",
    group: "legal",
    installed: false,
    hasUpdate: false,
    documentId: "x",
    installDocumentId: null,
    status: "published",
    ...over,
  };
}

describe("promptItemShelfCopy", () => {
  it("准则用产品简介，不把正文塞进卡面", () => {
    const shared = promptItemShelfCopy(sharedItem());
    expect(shared.description).toBe("每回合都在的工作宪法");
    expect(shared.description).not.toContain("you are the constitution");
    expect(shared.accessory).toEqual([{ label: "官方" }]);
  });

  it("官方 HOW 打官方徽标，不打决策时刻组，只标非全员观众", () => {
    const copy = promptItemShelfCopy(
      skillItem({
        summary: "团队拆法",
        blurb: "把任务拆成角色和并行，决定谁干什么",
        group: "编排",
        audience: ["ceo"],
      }),
    );
    expect(copy).toEqual({
      title: "团队拆法",
      description: "把任务拆成角色和并行，决定谁干什么",
      tags: ["CEO"],
      accessory: [{ label: "官方" }],
    });
    expect(
      promptItemShelfCopy(
        skillItem({ summary: "薄技能", blurb: "写一条按需薄技能" }),
      ).tags,
    ).toEqual([]);
    expect(
      promptItemShelfCopy(
        skillItem({
          summary: "正反辩论",
          blurb: "正反两边对碰",
          requiresTools: ["debate"],
        }),
      ).tags,
    ).toEqual(["工具"]);
  });

  it("简介与标题相同时留空槽，不复述", () => {
    const copy = promptItemShelfCopy(
      skillItem({ summary: "薄技能", blurb: "薄技能" }),
    );
    expect(copy.description).toBe("");
    expect(copy.tags).toEqual([]);
  });

  it("我的空介绍不编兜底句", () => {
    expect(
      promptItemShelfCopy(mineItem({ label: "合同审查" })).description,
    ).toBe("");
    expect(
      promptItemShelfCopy(mineItem({ label: "偏好", aiMaintained: true }))
        .description,
    ).toBe("");
  });

  it("夹里未上架不打我的；常驻打必带", () => {
    expect(
      promptItemShelfCopy(mineItem({ label: "合同审查" })).accessory,
    ).toEqual([]);
    expect(
      promptItemShelfCopy(mineItem({ label: "短约束", applyMode: "always" }))
        .accessory,
    ).toEqual([{ label: "必带" }]);
    expect(
      promptItemShelfCopy(mineItem({ label: "偏好", aiMaintained: true }))
        .accessory,
    ).toEqual([]);
  });

  it("已上架带场景组；市场装来带回场景组，有更新走右上", () => {
    const published = promptItemShelfCopy(mineItem({ label: "合同审查" }), {
      listingStatus: "published",
      sceneGroupLabel: "法律合规",
    });
    expect(published.accessory).toEqual([
      { label: "我的" },
      { label: "已上架" },
    ]);
    expect(published.tags).toEqual(["法律合规"]);
    const market = promptItemShelfCopy(mineItem({ label: "合同审查" }), {
      fromMarket: true,
      hasUpdate: true,
      sceneGroupLabel: "法律合规",
    });
    expect(market.accessory).toEqual([
      { label: "市场" },
      { label: "有更新", tone: "primary" },
    ]);
    expect(market.tags).toEqual(["法律合规"]);
  });

  it("货架不标已停用，简介仍用一句话介绍", () => {
    const copy = promptItemShelfCopy(
      mineItem({
        label: "旧规矩",
        description: "审合同时用",
        disputed: true,
      }),
    );
    expect(copy.description).toBe("审合同时用");
    expect(copy.accessory).toEqual([]);
    expect(
      promptItemShelfCopy(
        mineItem({
          label: "常驻规矩",
          disputed: true,
          applyMode: "always",
        }),
      ).accessory,
    ).toEqual([{ label: "必带" }]);
  });

  it("出厂工具标题用中文简介，底栏打工具，例外才叠加", () => {
    const copy = promptItemShelfCopy(toolItem({ resident: true }));
    expect(copy.title).toBe("读工作区文件");
    expect(copy.description).toBe("打开文本、代码或图片，看里面写了什么");
    expect(copy.tags).toEqual(["工具"]);
    expect(copy.accessory).toEqual([{ label: "官方" }]);
    expect(
      promptItemShelfCopy(toolItem({ resident: false })).accessory,
    ).toEqual([{ label: "官方" }]);
    expect(
      promptItemShelfCopy(
        toolItem({
          resident: true,
          approval: "grantable",
          availableTo: ["ceo"],
        }),
      ).tags,
    ).toEqual(["工具", "需审批", "CEO"]);
  });

  it("我的条目只打分组", () => {
    const copy = promptItemShelfCopy(
      mineItem({
        label: "合同审查",
        content: "---\napply: on_demand\ndescription: 审\n---\n怎么审",
      }),
      { sceneGroupLabel: "法律合规" },
    );
    expect(copy.tags).toEqual(["法律合规"]);
  });

  it("读卡标题旁为列出的工具补开场即用", () => {
    const resident = toolItem({ resident: true });
    expect(
      promptReadHeaderChips(promptItemShelfCopy(resident), resident),
    ).toEqual([{ label: "官方" }, { label: "开场即用" }, { label: "工具" }]);
    const connectorAction = toolItem({
      resident: false,
      approval: "grantable",
      availableTo: ["ceo"],
    });
    expect(
      promptReadHeaderChips(
        promptItemShelfCopy(connectorAction),
        connectorAction,
      ),
    ).toEqual([
      { label: "官方" },
      { label: "开场即用" },
      { label: "工具" },
      { label: "需审批" },
      { label: "CEO" },
    ]);
  });

  it("常驻满千字才标副标题", () => {
    expect(
      promptItemShelfCopy(sharedItem(), { alwaysChars: 999 }).subtitle,
    ).toBeUndefined();
    expect(
      promptItemShelfCopy(sharedItem(), { alwaysChars: 1000 }).subtitle,
    ).toBe("1000 字");
  });
});

describe("promptMineShelfOpts", () => {
  it("已装副本用安装记录的组；作者上架用 listing", () => {
    const item = mineItem({ label: "合同审查" });
    expect(
      promptMineShelfOpts(
        item,
        [],
        [
          listing({
            installDocumentId: "x",
            documentId: null,
            hasUpdate: true,
            group: "writing",
          }),
        ],
      ),
    ).toEqual({
      fromMarket: true,
      hasUpdate: true,
      listingStatus: null,
      sceneGroupLabel: "写作成稿",
    });
    expect(
      promptMineShelfOpts(item, [listing({ status: "published" })], []),
    ).toEqual({
      fromMarket: false,
      hasUpdate: false,
      listingStatus: "published",
      sceneGroupLabel: "法律合规",
    });
  });
});

describe("promptConnectorShelfCopy", () => {
  it("连接器不标本机，不把启动命令当简介", () => {
    expect(promptConnectorShelfCopy({ label: "Filesystem" })).toEqual({
      title: "Filesystem",
      description: "",
      tags: [],
      accessory: [],
    });
  });

  it("握手失败时简介写原因", () => {
    expect(
      promptConnectorShelfCopy({
        label: "GitHub",
        runtimeError: "GITHUB_TOKEN 未配置",
      }).description,
    ).toBe("GITHUB_TOKEN 未配置");
  });
});
