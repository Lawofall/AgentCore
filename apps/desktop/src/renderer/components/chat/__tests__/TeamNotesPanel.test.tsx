// @vitest-environment jsdom
import type { TeamNote } from "@/stores/execution";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { TeamNotesPanel } from "../TeamNotesPanel";

afterEach(cleanup);

function note(overrides: Partial<TeamNote> = {}): TeamNote {
  return {
    noteId: "n1",
    runId: "r1",
    agentId: "a1",
    role: "后端",
    kind: "decision",
    text: "POST /items",
    ts: 1,
    status: "active",
    supersedes: null,
    ...overrides,
  };
}

describe("TeamNotesPanel", () => {
  it("renders nothing when the wall was never raised and there are no notes", () => {
    const { container } = render(<TeamNotesPanel notes={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows an empty state when the wall is up but still has no notes", () => {
    render(<TeamNotesPanel notes={[]} noteWall expanded />);
    expect(screen.getByText("团队便签")).toBeTruthy();
    expect(screen.getByText("对齐点会贴在这里")).toBeTruthy();
  });

  it("lists notes when the wall has posts", () => {
    render(<TeamNotesPanel notes={[note()]} expanded />);
    expect(screen.getByText("POST /items")).toBeTruthy();
    expect(screen.queryByText("对齐点会贴在这里")).toBeNull();
  });
});
