import { describe, expect, it } from "vitest";
import { MESSAGE_ACTION_REVEAL_CLASS } from "../messageActionReveal";

describe("MESSAGE_ACTION_REVEAL_CLASS", () => {
  it("is visible below md and hover-gated from md up", () => {
    expect(MESSAGE_ACTION_REVEAL_CLASS).toContain("opacity-100");
    expect(MESSAGE_ACTION_REVEAL_CLASS).toContain("md:opacity-0");
    expect(MESSAGE_ACTION_REVEAL_CLASS).toContain("md:group-hover:opacity-100");
    expect(MESSAGE_ACTION_REVEAL_CLASS).toContain(
      "md:focus-within:opacity-100",
    );
    expect(MESSAGE_ACTION_REVEAL_CLASS).toContain("duration-fast");
    expect(MESSAGE_ACTION_REVEAL_CLASS).toContain(
      "motion-reduce:transition-none",
    );
  });
});
