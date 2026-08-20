// `pnpm conformance` entry for the desktop fold (前端技术与架构 §十 SSE 与协议一致性).
// Collected only by vitest.conformance.config.ts (tsx cannot load import.meta.env).
import { turnVerdictFromProjected } from "@/lib/turnOutcome";
import { runConformance } from "@agentcore/protocol-conformance";
import { expect, it } from "vitest";
import { foldToProjectedTurn } from "./conformanceFold";

it("desktop fold + turnVerdict against golden fixtures", () => {
  const { failed } = runConformance({
    name: "desktop",
    fold: foldToProjectedTurn,
    verdict: turnVerdictFromProjected,
  });
  expect(failed).toBe(0);
});
