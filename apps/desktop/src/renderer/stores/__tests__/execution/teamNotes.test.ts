import { describe, expect, it } from "vitest";
import { type RunFrame, projectExecution } from "../../execution";
import { plan, started } from "./fixtures";

// 团队便签墙 (§2.2 通): a worker broadcast a one-line decision / heads-up to its concurrent
// siblings via post_note — it folds TURN-LEVEL onto Execution.teamNotes (NOT onto a graph node),
// in post order, deduped by noteId. The same single-source frame fold the conformance golden pins
// cross-end (multi_agent_team_notes), tested here directly for order / dedup / turn-level shape.
describe("team note wall (§2.2 通)", () => {
  const note = (
    noteId: string,
    runId: string,
    agentId: string,
    role: string,
    noteKind: string,
    text: string,
    t: number,
    // 便签会过期 → supersession (§2.2): an amendment carries the noteId it 改写/作废s + the mode;
    // a fresh post leaves both null (the common case).
    supersedes: string | null = null,
    supersedeMode: "update" | "void" | null = null,
  ): RunFrame => ({
    t,
    kind: "team_note_posted",
    noteId,
    runId,
    agentId,
    role,
    noteKind,
    text,
    ts: t,
    supersedes,
    supersedeMode,
  });

  it("folds team notes turn-level in post order (never onto a node)", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      started("agent-2", "run-2", 2),
      note(
        "n1",
        "run-1",
        "agent-1",
        "后端",
        "decision",
        "POST /auth/session",
        3,
      ),
      note("n2", "run-2", "agent-2", "前端", "heads_up", "这个模块是异步的", 4),
    ];
    const exec = projectExecution(plan, frames, "running");
    expect(exec.teamNotes).toEqual([
      {
        noteId: "n1",
        runId: "run-1",
        agentId: "agent-1",
        role: "后端",
        kind: "decision",
        text: "POST /auth/session",
        ts: 3,
        status: "active",
        supersedes: null,
      },
      {
        noteId: "n2",
        runId: "run-2",
        agentId: "agent-2",
        role: "前端",
        kind: "heads_up",
        text: "这个模块是异步的",
        ts: 4,
        status: "active",
        supersedes: null,
      },
    ]);
    // Turn-level: a note never attaches to a graph node (run.escalations stays the per-run channel).
    expect(exec.runs.every((r) => r.escalations.length === 0)).toBe(true);
  });

  it("dedupes a re-delivered note by noteId (reload replay safety)", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      note("n1", "run-1", "agent-1", "后端", "decision", "字段名用 pwd", 2),
      note("n1", "run-1", "agent-1", "后端", "decision", "字段名用 pwd", 3),
    ];
    expect(projectExecution(plan, frames, "running").teamNotes).toHaveLength(1);
  });

  it("a turn with no notes carries an empty teamNotes", () => {
    const exec = projectExecution(
      plan,
      [started("agent-1", "run-1")],
      "running",
    );
    expect(exec.teamNotes).toEqual([]);
    expect(exec.noteWall).toBe(false);
  });

  it("folds noteWall from the plan skeleton (raised empty wall)", () => {
    const exec = projectExecution(
      { ...plan, noteWall: true },
      [started("agent-1", "run-1")],
      "running",
    );
    expect(exec.teamNotes).toEqual([]);
    expect(exec.noteWall).toBe(true);
  });

  // 便签会过期 → supersession (§2.2): an amendment (carries `supersedes` + mode) marks its TARGET
  // superseded (改写) / voided (作废); the amendment itself stays active. Same single-source fold
  // the conformance golden pins cross-end (multi_agent_team_notes_amended).
  it("marks the target superseded on 改写 and voided on 作废", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      started("agent-2", "run-2", 2),
      note(
        "n1",
        "run-1",
        "agent-1",
        "研究员",
        "decision",
        "字段用 password",
        3,
      ),
      note("n2", "run-2", "agent-2", "撰写员", "heads_up", "示例用本地时间", 4),
      // n3 改写s n1 → n1 becomes superseded, n3 carries the corrected decision.
      note(
        "n3",
        "run-1",
        "agent-1",
        "研究员",
        "decision",
        "字段改用 pwd",
        5,
        "n1",
        "update",
      ),
      // n4 作废s n2 → n2 becomes voided, n4 is the retraction notice.
      note(
        "n4",
        "run-2",
        "agent-2",
        "撰写员",
        "heads_up",
        "撤回之前那条",
        6,
        "n2",
        "void",
      ),
    ];
    const exec = projectExecution(plan, frames, "running");
    const byId = Object.fromEntries(exec.teamNotes.map((n) => [n.noteId, n]));
    expect(byId.n1.status).toBe("superseded");
    expect(byId.n2.status).toBe("voided");
    // The amendments themselves stay active and link back to their origin.
    expect(byId.n3).toMatchObject({ status: "active", supersedes: "n1" });
    expect(byId.n4).toMatchObject({ status: "active", supersedes: "n2" });
  });
});
