import { describe, expect, it } from "vitest";
import { scanStem, settledAskBlocks, settledAskFace } from "./askSettledFace";

const SHOT_PROMPT =
  "这次要协作图帮你做出什么？（一句话说清要什么、给谁用、做到哪一档即可）";

describe("scanStem", () => {
  it("keeps the first sentence and drops the parenthetical hint", () => {
    expect(scanStem(SHOT_PROMPT)).toBe("这次要协作图帮你做出什么？");
  });
});

describe("settledAskFace", () => {
  it("puts the short stem and reply on the scan line, full prompt only in the body", () => {
    const face = settledAskFace({
      question: "批次总述",
      prompts: [SHOT_PROMPT],
      note: `${SHOT_PROMPT}：我测试`,
      selected: [],
    });
    expect(face.stem).toBe("这次要协作图帮你做出什么？");
    expect(face.answer).toBe("我测试");
    expect(face.answersInHeader).toBe(true);
    expect(face.lead).toBe("");

    const blocks = settledAskBlocks(face, true);
    expect(blocks).toEqual([{ prompt: SHOT_PROMPT, picks: [], reply: "" }]);
    expect(blocks.some((block) => block.prompt === "批次总述")).toBe(false);
    expect(blocks.some((block) => block.reply === "我测试")).toBe(false);
  });

  it("joins short replies and pairs each question when there are several", () => {
    const face = settledAskFace({
      question: "关于论文有几个方向想先跟你对齐",
      prompts: [],
      note: "就按这个方案开做：\n· 定位？：综述型\n· 读者？：公开发表\n· 篇幅？：精简干货",
      selected: [],
    });
    expect(face.stem).toBe("关于论文有几个方向想先跟你对齐");
    expect(face.answer).toBe("综述型 · 公开发表 · 精简干货");
    expect(face.answersInHeader).toBe(true);
    const blocks = settledAskBlocks(face, true);
    expect(blocks.map((block) => block.prompt)).toEqual([
      "定位？",
      "读者？",
      "篇幅？",
    ]);
    expect(blocks.map((block) => block.reply)).toEqual([
      "综述型",
      "公开发表",
      "精简干货",
    ]);
    expect(JSON.stringify(blocks)).not.toContain("就按这个方案开做");
  });

  it("folds a long multi-reply scan line to 共 N 题", () => {
    const long = "这是一段超过预算的答复需要展开才能看全";
    const face = settledAskFace({
      question: "总题",
      prompts: ["甲？", "乙？"],
      note: `甲？：${long}\n乙？：${long}`,
      selected: [],
    });
    expect(face.answer).toBe("共 2 题");
    expect(face.answersInHeader).toBe(false);
    const blocks = settledAskBlocks(face, true);
    expect(blocks.map((block) => block.reply)).toEqual([long, long]);
  });

  it("keeps a single long reply on the scan line and in the body", () => {
    const long = "甲".repeat(40);
    const face = settledAskFace({
      question: "这一题的问句",
      prompts: [],
      note: "",
      selected: [long],
    });
    expect(face.answer).toBe(long);
    expect(face.answersInHeader).toBe(false);
    expect(settledAskBlocks(face, true)).toEqual([
      { prompt: "", picks: [long], reply: "" },
    ]);
  });

  it("has nothing to expand when the stem and reply already fit", () => {
    const face = settledAskFace({
      question: "这一题的问句",
      prompts: ["这一题的问句"],
      note: "",
      selected: ["方案 C：外包试点"],
    });
    expect(face.stem).toBe("这一题的问句");
    expect(face.answer).toBe("方案 C：外包试点");
    expect(settledAskBlocks(face, true)).toEqual([]);
  });

  it("moves the question into the body when an outcome label owns the header", () => {
    const face = settledAskFace({
      question: "这一题的问句",
      prompts: [],
      note: "",
      selected: [],
    });
    expect(settledAskBlocks(face, false)).toEqual([
      { prompt: "这一题的问句", picks: [], reply: "" },
    ]);
  });
});
