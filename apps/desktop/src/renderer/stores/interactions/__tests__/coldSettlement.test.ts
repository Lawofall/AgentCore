import { afterEach, describe, expect, it } from "vitest";
import {
  checkpointIdIfColdResolved,
  clearColdServerSettled,
  collectMessageJournalEvents,
  isColdCheckpointSettled,
  noteColdServerSettled,
  settledColdIdsFromEvents,
} from "../coldSettlement";

afterEach(() => {
  clearColdServerSettled();
});

describe("coldSettlement criterion", () => {
  it("reads cold *_resolved ids from journal events", () => {
    expect(
      checkpointIdIfColdResolved("team_preview_resolved", {
        checkpoint_id: "tp1",
      }),
    ).toBe("tp1");
    expect(
      checkpointIdIfColdResolved("checkpoint_resolved", {
        checkpoint_id: "cp1",
      }),
    ).toBe("cp1");
    expect(
      checkpointIdIfColdResolved("approval_resolved", { approval_id: "a1" }),
    ).toBeNull();

    const ids = settledColdIdsFromEvents([
      { type: "team_preview_required", payload: { checkpoint_id: "tp1" } },
      { type: "team_preview_resolved", payload: { checkpoint_id: "tp1" } },
      { type: "approval_resolved", payload: { approval_id: "a1" } },
    ]);
    expect([...ids]).toEqual(["tp1"]);
  });

  it("collects journal events across messages", () => {
    const events = collectMessageJournalEvents([
      {
        runs: {
          events: [
            {
              type: "team_preview_resolved",
              payload: { checkpoint_id: "tp1" },
            },
          ],
        },
      },
      { runs: { events: [] } },
      {},
    ]);
    expect(settledColdIdsFromEvents(events).has("tp1")).toBe(true);
  });

  it("is settled when journal, noted id, or entry is terminal", () => {
    expect(
      isColdCheckpointSettled({
        checkpointId: "tp1",
        journalSettledIds: new Set(["tp1"]),
      }),
    ).toBe(true);
    expect(
      isColdCheckpointSettled({
        checkpointId: "tp1",
        entry: { status: "pending" },
      }),
    ).toBe(false);

    noteColdServerSettled("tp1");
    expect(isColdCheckpointSettled({ checkpointId: "tp1" })).toBe(true);

    expect(
      isColdCheckpointSettled({
        checkpointId: "tp2",
        entry: { status: "resolved" },
      }),
    ).toBe(true);
    expect(
      isColdCheckpointSettled({
        checkpointId: "tp3",
        entry: { status: "submitting", settledByReceipt: true },
      }),
    ).toBe(true);
  });
});
