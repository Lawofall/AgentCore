import {
  type FileArtifact,
  fileArtifactsFromDeliveryStatus,
  fileArtifactsFromProcess,
  hasChangePreviews,
  mergeArtifacts,
  resolveFileArtifactsForCard,
  splitExportedSources,
} from "@/lib/fileArtifacts";
import type {
  DeliveryArtifact,
  DeliveryPromotion,
  DeliveryStatusPayload,
  ProcessStep,
} from "@/types/events";
import { describe, expect, it } from "vitest";

function toolStep(
  tool_name: string,
  args: Record<string, unknown>,
  status: "success" | "error" = "success",
): ProcessStep {
  return {
    kind: "tool",
    id: `t-${tool_name}`,
    tool_name,
    arguments: args,
    result: null,
    status,
  };
}

describe("fileArtifacts change previews (A1)", () => {
  it("str_replace carries edit preview", () => {
    const arts = fileArtifactsFromProcess([
      toolStep("str_replace", {
        path: "src/a.ts",
        old_string: "const x = 1",
        new_string: "const x = 2",
      }),
    ]);
    expect(arts).toHaveLength(1);
    expect(arts[0].change).toEqual({
      kind: "edit",
      oldText: "const x = 1",
      newText: "const x = 2",
    });
    expect(hasChangePreviews(arts)).toBe(true);
  });

  it("file_write / file_append carry write preview", () => {
    const write = fileArtifactsFromProcess([
      toolStep("file_write", { path: "a.md", content: "hello" }),
    ]);
    expect(write[0].change).toEqual({
      kind: "write",
      content: "hello",
      mode: "overwrite",
    });
    const append = fileArtifactsFromProcess([
      toolStep("file_append", { path: "a.md", content: "\nmore" }),
    ]);
    expect(append[0].change).toEqual({
      kind: "write",
      content: "\nmore",
      mode: "append",
    });
  });

  it("file_delete / file_move carry meta preview", () => {
    const arts = fileArtifactsFromProcess([
      toolStep("file_delete", { path: "gone.ts" }),
      toolStep("file_move", {
        source: "old.ts",
        destination: "new.ts",
      }),
    ]);
    expect(arts.find((a) => a.path === "gone.ts")?.change).toEqual({
      kind: "delete",
    });
    expect(arts.find((a) => a.path === "new.ts")?.change).toEqual({
      kind: "move",
      fromPath: "old.ts",
    });
  });

  it("mergeArtifacts keeps last op per path", () => {
    expect(
      fileArtifactsFromProcess([
        toolStep("file_write", { path: "a.ts", content: "1" }),
        toolStep("str_replace", {
          path: "a.ts",
          old_string: "1",
          new_string: "2",
        }),
      ]).map((a) => a.op),
    ).toEqual(["edit"]);
  });

  it("hasChangePreviews is false when no change", () => {
    const bare: FileArtifact[] = [{ path: "x.ts", name: "x.ts", op: "write" }];
    expect(hasChangePreviews(bare)).toBe(false);
  });

  it("failed tool steps are skipped", () => {
    const arts = fileArtifactsFromProcess([
      toolStep("file_write", { path: "a.md", content: "x" }, "error"),
    ]);
    expect(arts).toHaveLength(0);
  });

  it("unknown tools skipped", () => {
    const arts = fileArtifactsFromProcess([
      toolStep("web_search", { query: "x" }),
    ]);
    expect(arts).toHaveLength(0);
  });

  it("mergeArtifacts flattens sources", () => {
    const arts = mergeArtifacts(
      fileArtifactsFromProcess([
        toolStep("file_write", { path: "a.md", content: "a" }),
      ]),
      fileArtifactsFromProcess([
        toolStep("file_write", { path: "b.md", content: "b" }),
      ]),
    );
    expect(arts.map((a) => a.path).sort()).toEqual(["a.md", "b.md"]);
  });
});

describe("fileArtifacts from delivery_status.artifacts", () => {
  it("maps accepted+rejected and ignores tool lists", () => {
    const status = {
      execution_id: "e1",
      state: "partial",
      summary: "x",
      delivered_files: ["ok.md"],
      gaps: [],
      actions: [],
      artifacts: [
        { path: "ok.md", status: "accepted" },
        {
          path: "bad.md",
          status: "rejected",
          reason: "citations_unverified",
          detail: "缺 #rN",
          workspace_id: "folder:proj-1",
        },
      ],
    } as DeliveryStatusPayload;
    const fromDelivery = fileArtifactsFromDeliveryStatus(status);
    expect(fromDelivery).not.toBeNull();
    if (fromDelivery == null) return;
    expect(fromDelivery).toEqual([
      { path: "ok.md", name: "ok.md", acceptance: "accepted" },
      {
        path: "bad.md",
        name: "bad.md",
        acceptance: "rejected",
        acceptanceReason: "citations_unverified",
        acceptanceDetail: "缺 #rN",
        workspaceId: "folder:proj-1",
      },
    ]);
    expect(resolveFileArtifactsForCard(status).map((a) => a.path)).toEqual([
      "ok.md",
      "bad.md",
    ]);
  });

  it("missing artifacts field yields empty card list (no tool fallback)", () => {
    const status = {
      execution_id: "e1",
      state: "delivered",
      summary: "x",
      delivered_files: ["a.md"],
      gaps: [],
      actions: [],
    } as DeliveryStatusPayload;
    expect(fileArtifactsFromDeliveryStatus(status)).toBeNull();
    expect(resolveFileArtifactsForCard(status)).toEqual([]);
  });

  it("empty artifacts array yields empty list", () => {
    const status = {
      execution_id: "e1",
      state: "blocked",
      summary: "x",
      delivered_files: [],
      gaps: [],
      actions: [],
      artifacts: [],
    } as DeliveryStatusPayload;
    expect(fileArtifactsFromDeliveryStatus(status)).toEqual([]);
    expect(resolveFileArtifactsForCard(status)).toEqual([]);
  });

  it("carries self-reported kind / derived_from onto the card rows", () => {
    const status = {
      execution_id: "e1",
      state: "delivered",
      summary: "已交付 2 个文件",
      delivered_files: ["起诉状.md", "起诉状.docx"],
      gaps: [],
      actions: [],
      artifacts: [
        { path: "起诉状.md", status: "accepted", kind: "md" },
        {
          path: "起诉状.docx",
          status: "accepted",
          kind: "docx",
          derived_from: "起诉状.md",
        },
      ],
    } as DeliveryStatusPayload;
    expect(resolveFileArtifactsForCard(status)).toEqual([
      {
        path: "起诉状.md",
        name: "起诉状.md",
        acceptance: "accepted",
        kind: "md",
      },
      {
        path: "起诉状.docx",
        name: "起诉状.docx",
        acceptance: "accepted",
        kind: "docx",
        derivedFrom: "起诉状.md",
      },
    ]);
  });
});

describe("历史 delivery_status.promoted（promote_product 已撤销）", () => {
  const WORKROOM = "AgentCore/文档/工作稿";

  function status(
    artifacts: DeliveryArtifact[],
    promoted?: DeliveryPromotion[],
  ): DeliveryStatusPayload {
    return {
      execution_id: "e1",
      state: "delivered",
      summary: "x",
      delivered_files: [],
      gaps: [],
      actions: [],
      artifacts,
      ...(promoted ? { promoted } : {}),
    };
  }

  it("行已按后端改写记 to：认出成品并留下旧路径，未归位的原样", () => {
    const arts = resolveFileArtifactsForCard(
      status(
        [
          { path: "起诉状.docx", status: "accepted" },
          { path: `${WORKROOM}/取证清单.md`, status: "accepted" },
        ],
        [{ from: `${WORKROOM}/起诉状.docx`, to: "起诉状.docx" }],
      ),
    );
    expect(arts).toEqual([
      {
        path: "起诉状.docx",
        name: "起诉状.docx",
        acceptance: "accepted",
        promotedFrom: `${WORKROOM}/起诉状.docx`,
      },
      {
        path: `${WORKROOM}/取证清单.md`,
        name: "取证清单.md",
        acceptance: "accepted",
      },
    ]);
  });

  it("旧路径快照（未经改写）同样认，行改挂到新路径上", () => {
    const arts = resolveFileArtifactsForCard(
      status(
        [{ path: `${WORKROOM}/起诉状.docx`, status: "accepted" }],
        [{ from: `${WORKROOM}/起诉状.docx`, to: "起诉状.docx" }],
      ),
    );
    expect(arts[0].path).toBe("起诉状.docx");
    expect(arts[0].promotedFrom).toBe(`${WORKROOM}/起诉状.docx`);
  });

  it("跨回合再归位：旧行保留，链式 X→Y→Z 只留一行且认最近一跳", () => {
    const arts = resolveFileArtifactsForCard(
      status(
        [{ path: "报告/年度总结.md", status: "accepted" }],
        [
          { from: `${WORKROOM}/年度总结.md`, to: "年度总结.md" },
          { from: "年度总结.md", to: "报告/年度总结.md" },
        ],
      ),
    );
    expect(arts).toHaveLength(1);
    expect(arts[0].path).toBe("报告/年度总结.md");
    expect(arts[0].promotedFrom).toBe("年度总结.md");
  });

  it("零归位（空数组 / 缺字段）：一件成品都不标", () => {
    const rows: DeliveryArtifact[] = [
      { path: `${WORKROOM}/round.md`, status: "accepted" },
    ];
    for (const s of [status(rows, []), status(rows)]) {
      expect(resolveFileArtifactsForCard(s)[0].promotedFrom).toBeUndefined();
    }
  });

  it("只有 accepted 可归位：同路径的 rejected 行不标成品", () => {
    const arts = resolveFileArtifactsForCard(
      status(
        [{ path: `${WORKROOM}/报告.md`, status: "rejected" }],
        [{ from: `${WORKROOM}/报告.md`, to: "报告.md" }],
      ),
    );
    expect(arts[0].path).toBe(`${WORKROOM}/报告.md`);
    expect(arts[0].promotedFrom).toBeUndefined();
  });

  it("源也归位时 derivedFrom 跟着改，中间稿折叠不断链", () => {
    // 后端只改写 artifacts[].path，derived_from 仍指旧路径——不一并改就认不出自己的源。
    const arts = resolveFileArtifactsForCard(
      status(
        [
          { path: "报告.md", status: "accepted", kind: "md" },
          {
            path: "报告.docx",
            status: "accepted",
            kind: "docx",
            derived_from: `${WORKROOM}/报告.md`,
          },
        ],
        [
          { from: `${WORKROOM}/报告.md`, to: "报告.md" },
          { from: `${WORKROOM}/报告.docx`, to: "报告.docx" },
        ],
      ),
    );
    expect(arts.map((a) => a.derivedFrom)).toEqual([undefined, "报告.md"]);
    const { primary, intermediate } = splitExportedSources(arts);
    expect(primary.map((a) => a.path)).toEqual(["报告.docx"]);
    expect(intermediate.map((a) => a.path)).toEqual(["报告.md"]);
  });

  it("空 from / to 的畸形行跳过，不误标成品", () => {
    const arts = resolveFileArtifactsForCard(
      status(
        [{ path: "报告.md", status: "accepted" }],
        [{ from: "", to: "报告.md" }],
      ),
    );
    expect(arts[0].promotedFrom).toBeUndefined();
  });
});

/** 已验收产物（可选带派生源）。 */
function accepted(path: string, derivedFrom?: string): FileArtifact {
  return {
    path,
    name: path,
    acceptance: "accepted" as const,
    ...(derivedFrom ? { derivedFrom } : {}),
  };
}

describe("splitExportedSources（口径同后端 fold_exported_sources）", () => {
  it("导出件主推、源 md 降为中间稿（都还在，只是分区）", () => {
    const { primary, intermediate } = splitExportedSources([
      accepted("起诉状.md"),
      accepted("起诉状.docx", "起诉状.md"),
    ]);
    expect(primary.map((a) => a.path)).toEqual(["起诉状.docx"]);
    expect(intermediate.map((a) => a.path)).toEqual(["起诉状.md"]);
  });

  it("一源多导：docx + pdf 都主推，只降级那一份源", () => {
    const { primary, intermediate } = splitExportedSources([
      accepted("报告.md"),
      accepted("报告.docx", "报告.md"),
      accepted("报告.pdf", "报告.md"),
    ]);
    expect(primary.map((a) => a.path)).toEqual(["报告.docx", "报告.pdf"]);
    expect(intermediate.map((a) => a.path)).toEqual(["报告.md"]);
  });

  it("没自报 derivedFrom 就不折叠——不按扩展名猜派生关系", () => {
    const { primary, intermediate } = splitExportedSources([
      accepted("报告.md"),
      accepted("报告.docx"),
    ]);
    expect(primary.map((a) => a.path)).toEqual(["报告.md", "报告.docx"]);
    expect(intermediate).toEqual([]);
  });

  it("导出件未过验收 → 源 md 是用户唯一能拿的东西，不得折叠", () => {
    const { primary, intermediate } = splitExportedSources([
      accepted("报告.md"),
      {
        path: "报告.docx",
        name: "报告.docx",
        acceptance: "rejected",
        derivedFrom: "报告.md",
      },
    ]);
    expect(primary.map((a) => a.path)).toEqual(["报告.md", "报告.docx"]);
    expect(intermediate).toEqual([]);
  });

  it("源不在清单 / 空清单 → 无可折叠，导出件照常主推", () => {
    expect(
      splitExportedSources([accepted("报告.docx", "报告.md")]).primary.map(
        (a) => a.path,
      ),
    ).toEqual(["报告.docx"]);
    expect(splitExportedSources([])).toEqual({ primary: [], intermediate: [] });
  });

  it("自报成环 / 自指：宁可不折叠，也不能把清单清空", () => {
    const cycle = splitExportedSources([
      accepted("a.docx", "b.docx"),
      accepted("b.docx", "a.docx"),
    ]);
    expect(cycle.primary.map((a) => a.path)).toEqual(["a.docx", "b.docx"]);
    expect(cycle.intermediate).toEqual([]);
    const selfRef = splitExportedSources([accepted("报告.docx", "报告.docx")]);
    expect(selfRef.primary.map((a) => a.path)).toEqual(["报告.docx"]);
    expect(selfRef.intermediate).toEqual([]);
  });

  it("工具源清单（无验收态）原样通过", () => {
    const tools: FileArtifact[] = [
      { path: "a.ts", name: "a.ts", op: "write" },
      { path: "b.ts", name: "b.ts", op: "edit" },
    ];
    expect(splitExportedSources(tools)).toEqual({
      primary: tools,
      intermediate: [],
    });
  });

  it("预览截图 derivedFrom HTML 不把源页面折成中间稿", () => {
    const { primary, intermediate } = splitExportedSources([
      accepted("site/index.html"),
      {
        path: "site/preview-desktop.jpg",
        name: "preview-desktop.jpg",
        acceptance: "accepted",
        kind: "image",
        derivedFrom: "site/index.html",
      },
    ]);
    expect(primary.map((a) => a.path)).toEqual([
      "site/index.html",
      "site/preview-desktop.jpg",
    ]);
    expect(intermediate).toEqual([]);
  });
});
