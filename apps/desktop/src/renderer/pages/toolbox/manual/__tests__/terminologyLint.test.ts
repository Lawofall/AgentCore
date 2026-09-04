import { describe, expect, it } from "vitest";
import { collectAllUserVisibleCorpus } from "../gates/collectCorpus";
import { findRetiredTermHits } from "../gates/retiredTerms";

describe("manual terminology lint", () => {
  it("content + scenarioData 用户可见文案不含未豁免的退役词", () => {
    const corpus = collectAllUserVisibleCorpus();
    expect(corpus.length).toBeGreaterThan(20);

    const failures: string[] = [];
    for (const piece of corpus) {
      for (const hit of findRetiredTermHits(piece.text)) {
        failures.push(
          `${piece.where}: 退役词「${hit.term}」——${hit.reason}；片段「${hit.snippet}」`,
        );
      }
    }

    expect(failures, failures.join("\n")).toEqual([]);
  });

  it("协作桌「邀请成员」豁免（名册，不是 worker）", () => {
    const hits = findRetiredTermHits(
      "在自己的云文件夹上邀请成员。对方在「与我共享」看见这张桌。",
    );
    expect(hits.filter((h) => h.term === "成员")).toEqual([]);
  });

  it("定义性提及（口语也叫「热修」）豁免生效", () => {
    const hits = findRetiredTermHits(
      "CEO 唤回原队员接着改（口语有时叫「热修」），不是从零重来。",
    );
    expect(hits.filter((h) => h.term === "热修")).toEqual([]);
  });

  it("裸用退役词仍命中", () => {
    const hits = findRetiredTermHits("点中止按钮结束回合");
    expect(hits.some((h) => h.term === "中止")).toBe(true);
  });
});
