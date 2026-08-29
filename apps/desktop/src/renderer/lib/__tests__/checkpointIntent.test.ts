import { describe, expect, it } from "vitest";
import { parseCheckpointIntent } from "../checkpointIntent";

describe("parseCheckpointIntent", () => {
  it("keeps the three chrome intents", () => {
    expect(parseCheckpointIntent("decision")).toBe("decision");
    expect(parseCheckpointIntent("organize_plan")).toBe("organize_plan");
    expect(parseCheckpointIntent("daily_review")).toBe("daily_review");
  });

  it("folds leftover wire names and unknowns into decision", () => {
    expect(parseCheckpointIntent("kickoff")).toBe("decision");
    expect(parseCheckpointIntent("proposal_pick")).toBe("decision");
    expect(parseCheckpointIntent("risk_ack")).toBe("decision");
    expect(parseCheckpointIntent(undefined)).toBe("decision");
    expect(parseCheckpointIntent(null)).toBe("decision");
    expect(parseCheckpointIntent("other")).toBe("decision");
  });
});
