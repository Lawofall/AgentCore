import type { TeamNote } from "@/stores/execution";
import { describe, expect, it } from "vitest";
import { teamNotesDefaultExpanded } from "../teamNotesDefaults";

function note(
  status: TeamNote["status"] = "active",
  overrides: Partial<TeamNote> = {},
): TeamNote {
  return {
    noteId: "n1",
    runId: "r1",
    agentId: "a1",
    role: "撰写员",
    kind: "decision",
    text: "用 camelCase",
    ts: 1,
    status,
    supersedes: null,
    ...overrides,
  };
}

describe("teamNotesDefaultExpanded", () => {
  it("returns false when there are no notes and the wall was not raised", () => {
    expect(teamNotesDefaultExpanded("running", [])).toBe(false);
    expect(teamNotesDefaultExpanded("running", [], false)).toBe(false);
  });

  it("expands a raised empty wall while running", () => {
    expect(teamNotesDefaultExpanded("running", [], true)).toBe(true);
  });

  it("stays collapsed for a raised empty wall once the turn settles", () => {
    expect(teamNotesDefaultExpanded("completed", [], true)).toBe(false);
  });

  it("expands while running with an active note", () => {
    expect(teamNotesDefaultExpanded("running", [note("active")])).toBe(true);
  });

  it("stays collapsed while running if every note is stale", () => {
    expect(
      teamNotesDefaultExpanded("running", [
        note("superseded", { noteId: "n1" }),
        note("voided", { noteId: "n2" }),
      ]),
    ).toBe(false);
  });

  it("stays collapsed for completed / stopped turns even with active notes", () => {
    expect(teamNotesDefaultExpanded("completed", [note("active")])).toBe(false);
    expect(teamNotesDefaultExpanded("failed", [note("active")])).toBe(false);
    expect(teamNotesDefaultExpanded("cancelled", [note("active")])).toBe(false);
    expect(teamNotesDefaultExpanded("paused", [note("active")])).toBe(false);
  });
});
