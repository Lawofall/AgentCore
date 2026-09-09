import { listingCopy } from "@/pages/toolbox/market/listingCopy";
import { describe, expect, it } from "vitest";

describe("listingCopy", () => {
  it("keeps a human name as the title", () => {
    expect(
      listingCopy({ name: "合同审查", description: "审合同时用" }),
    ).toEqual({
      title: "合同审查",
      subtitle: "审合同时用",
      ident: null,
    });
  });

  it("promotes the catalog line when the name is a consult id", () => {
    expect(
      listingCopy({
        name: "legal_answer_brief",
        description: "民事答辩状",
      }),
    ).toEqual({
      title: "民事答辩状",
      subtitle: "",
      ident: "legal_answer_brief",
    });
  });

  it("does not swap when the ident has no catalog line", () => {
    expect(
      listingCopy({ name: "legal_answer_brief", description: "" }),
    ).toEqual({
      title: "legal_answer_brief",
      subtitle: "",
      ident: null,
    });
  });
});
