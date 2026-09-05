import { describe, expect, it } from "vitest";
import { ASK_COMMENCE_MOCK } from "../askCommenceMock";
import { ASK_COMMENCE_SCENES } from "../askCommenceScenes";

describe("ASK_COMMENCE_SCENES", () => {
  it("exposes five unique layout variants", () => {
    expect(ASK_COMMENCE_SCENES).toHaveLength(5);
    const ids = ASK_COMMENCE_SCENES.map((s) => s.id);
    expect(new Set(ids).size).toBe(5);
    expect(ids).toEqual([
      "ask-commence-v1",
      "ask-commence-v2",
      "ask-commence-v3",
      "ask-commence-v4",
      "ask-commence-v5",
    ]);
  });

  it("each scene has title + intent", () => {
    for (const s of ASK_COMMENCE_SCENES) {
      expect(s.title.length).toBeGreaterThan(0);
      expect(s.intent.length).toBeGreaterThan(0);
    }
  });

  it("shared mock carries ask clarification fields", () => {
    expect(ASK_COMMENCE_MOCK.question.length).toBeGreaterThan(0);
    expect(ASK_COMMENCE_MOCK.questions.length).toBeGreaterThan(0);
    expect(ASK_COMMENCE_MOCK.questions.every((q) => q.kind === "choice")).toBe(
      true,
    );
  });

  it("v2 scene keeps the retired Brief+Choose reference", () => {
    const v2 = ASK_COMMENCE_SCENES.find((s) => s.id === "ask-commence-v2");
    expect(v2).toBeDefined();
    expect(v2?.title.toLowerCase()).toMatch(/brief|choose|v2/i);
  });

  it("v5 scene mounts the production generic clarify body", () => {
    const v5 = ASK_COMMENCE_SCENES.find((s) => s.id === "ask-commence-v5");
    expect(v5).toBeDefined();
    expect(v5?.intent).toMatch(/现生产|AskDecisionBody/);
  });
});
