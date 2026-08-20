import { describe, expect, it } from "vitest";
import {
  recoveryMomentRecoveryClause,
  recoveryMomentResetClause,
} from "./recoveryMomentCopy";

describe("recovery moment clauses", () => {
  it("pins the two shared suffix sentences", () => {
    expect(recoveryMomentRecoveryClause("8 月 15 日 00:00")).toBe(
      "额度将于 8 月 15 日 00:00 恢复。",
    );
    expect(recoveryMomentResetClause("8 月 15 日 00:00")).toBe(
      "额度将于 8 月 15 日 00:00 重置。",
    );
  });
});
