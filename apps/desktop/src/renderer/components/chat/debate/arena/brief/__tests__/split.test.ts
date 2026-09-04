import { describe, expect, it } from "vitest";
import { splitFactDisplay, splitLeaning, splitValueCall } from "../split";

describe("splitLeaning", () => {
  it("拆站队、命题与反转", () => {
    const out = splitLeaning(
      "倾向反方：AI 人格论尚未证成必要性；若未来实证证明能闭合缺口，则翻向正方。",
    );
    expect(out.stanceLabel).toBe("倾向反方");
    expect(out.stanceSide).toBe("con");
    expect(out.thesis).toBe("AI 人格论尚未证成必要性");
    expect(out.reversal).toMatch(/^若未来实证/);
  });

  it("只有站队短句时不造空命题", () => {
    const out = splitLeaning("倾向正方");
    expect(out.stanceLabel).toBe("倾向正方");
    expect(out.stanceSide).toBe("pro");
    expect(out.thesis).toBe("");
    expect(out.reversal).toBeNull();
  });

  it("按辩手名上色", () => {
    const out = splitLeaning("倾向加速派。若成本不可接受，则翻向审慎派。", [
      { name: "加速派", key: "pro", stance: "pro" },
    ]);
    expect(out.stanceLabel).toBe("倾向加速派");
    expect(out.stanceSide).toBe("pro");
    expect(out.reversal).toMatch(/^若成本/);
  });
});

describe("splitValueCall", () => {
  it("问句与对照分支拆开", () => {
    const out = splitValueCall(
      "无过错时损失如何分担？选社会共担→站正方；选部署者兜底→站反方。",
    );
    expect(out.question).toBe("无过错时损失如何分担？");
    expect(out.mappings).toEqual(["社会共担 → 站正方", "部署者兜底 → 站反方"]);
  });

  it("没有对照时整句当问句", () => {
    expect(splitValueCall("要不要牺牲速度")).toEqual({
      question: "要不要牺牲速度",
      mappings: [],
    });
  });
});

describe("splitFactDisplay", () => {
  it("去掉台账机器串，留下人话与状态", () => {
    const out = splitFactDisplay(
      "EU 责任框架是否存在缺口（#e12/#e15, tier=unknown待评）【待核实】",
    );
    expect(out.body).toBe("EU 责任框架是否存在缺口");
    expect(out.statusLabels).toEqual(["待核实"]);
  });
});
