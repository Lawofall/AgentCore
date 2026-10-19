import {
  buildFactoryGuide,
  filterFactoryGuide,
  flattenFactoryGuide,
} from "@/components/tools/factoryGuide";
import { skillCatalogId, toolCatalogId } from "@/lib/promptCatalog";
import type { Capabilities } from "@/services/capabilities";
import { describe, expect, it } from "vitest";

const base: Capabilities = {
  guidelines: {
    shared_base: "准则",
    worker_leaf: "",
    worker_captain: "",
    ceo_addon: "",
    ceo: "",
  },
  skills: [
    {
      name: "page_ui",
      summary: "页面观感",
      body: "ui-body",
      group: "交付",
      blurb: "页面长什么样",
    },
    {
      name: "orphan",
      summary: "未分组 HOW",
      body: "orphan-body",
      group: "",
      blurb: "",
    },
  ],
  tools: [
    {
      name: "read",
      face: "file",
      resident: true,
      summary: "读工作区文件",
      blurb: "打开文本、代码或图片，看里面写了什么",
      description: "读工作区文件",
      description_prefer: "",
      parameters: {},
      approval: "never",
      available_to: ["ceo", "worker"],
    },
    {
      name: "host",
      face: "host_browser",
      resident: true,
      summary: "这台电脑。",
      blurb: "看本机屏幕、键鼠和已打开的应用",
      description: "这台电脑。",
      description_prefer: "",
      parameters: {},
      approval: "grantable",
      available_to: ["worker"],
    },
  ],
};

describe("buildFactoryGuide", () => {
  it("官方 HOW 按决策组、出厂工具按能力面，HOW 在工具前面", () => {
    const sections = buildFactoryGuide(base);
    expect(sections.map((row) => row.title)).toEqual([
      "交付",
      "用法",
      "文件",
      "本机 · 浏览器",
    ]);
    expect(sections[0].items.map((row) => row.id)).toEqual([
      skillCatalogId("page_ui"),
    ]);
    expect(sections[1].items.map((row) => row.id)).toEqual([
      skillCatalogId("orphan"),
    ]);
    expect(sections[2].items.map((row) => row.id)).toEqual([
      toolCatalogId("read"),
    ]);
    expect(flattenFactoryGuide(sections).map((row) => row.id)).toEqual([
      skillCatalogId("page_ui"),
      skillCatalogId("orphan"),
      toolCatalogId("read"),
      toolCatalogId("host"),
    ]);
  });

  it("搜索命中标题或协议名", () => {
    const sections = buildFactoryGuide(base);
    expect(
      filterFactoryGuide(sections, "host").map((row) => row.title),
    ).toEqual(["本机 · 浏览器"]);
    expect(
      filterFactoryGuide(sections, "页面").flatMap((row) =>
        row.items.map((item) => item.id),
      ),
    ).toEqual([skillCatalogId("page_ui")]);
    expect(filterFactoryGuide(sections, "没有这个")).toEqual([]);
  });
});
