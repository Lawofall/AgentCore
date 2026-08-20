import {
  type TurnPhase,
  allowsSseEvent,
} from "@/stores/conversation/turnPhase";
import { INTERACTION_KIND_WIRE } from "@agentcore/contract-types";
import { describe, expect, it } from "vitest";

const TERMINAL_OR_STOPPING: TurnPhase[] = [
  "stopping",
  "stopped",
  "completed",
  "failed",
];

describe("allowsSseEvent — interaction *_required on stopping/terminal", () => {
  it.each(TERMINAL_OR_STOPPING)(
    "allows checkpoint_required in phase %s",
    (phase) => {
      expect(allowsSseEvent(phase, "checkpoint_required")).toBe(true);
    },
  );

  it("allows other INTERACTION_KIND_WIRE *_required events when terminal", () => {
    for (const wire of Object.values(INTERACTION_KIND_WIRE)) {
      if (!wire.requiredEvent.endsWith("_required")) continue;
      expect(allowsSseEvent("completed", wire.requiredEvent)).toBe(true);
      expect(allowsSseEvent("stopping", wire.requiredEvent)).toBe(true);
    }
  });

  it("allows the paired *_resolved settlement events when stopping/terminal", () => {
    // 卡能在这个窗画出来（上一条），它的收口帧就只可能在同一个窗到：另一端拍板 /
    // CEO 仲裁 / 超时兜底都没有本端的乐观收口可依，挡掉就永远停在 pending。
    for (const wire of Object.values(INTERACTION_KIND_WIRE)) {
      if (!wire.resolvedEvent) continue;
      expect(allowsSseEvent("completed", wire.resolvedEvent)).toBe(true);
      expect(allowsSseEvent("stopping", wire.resolvedEvent)).toBe(true);
    }
  });

  it.each(TERMINAL_OR_STOPPING)(
    "allows approval_resolved / stage_card_resolved in phase %s",
    (phase) => {
      expect(allowsSseEvent(phase, "approval_resolved")).toBe(true);
      expect(allowsSseEvent(phase, "stage_card_resolved")).toBe(true);
    },
  );

  it("still blocks content mutations on stopping/terminal", () => {
    for (const phase of TERMINAL_OR_STOPPING) {
      expect(allowsSseEvent(phase, "content_delta")).toBe(false);
      expect(allowsSseEvent(phase, "tool_use_start")).toBe(false);
      expect(
        allowsSseEvent(phase, "tool_use_start", { tool_call_id: "ceo" }),
      ).toBe(false);
    }
  });

  it("allows worker-scoped tool_use_* on stopping/terminal (detached graph chrome)", () => {
    const worker = {
      tool_call_id: "tc",
      run_id: "r1",
      tool_name: "web_search",
    };
    for (const phase of TERMINAL_OR_STOPPING) {
      expect(allowsSseEvent(phase, "tool_use_start", worker)).toBe(true);
      expect(allowsSseEvent(phase, "tool_use_end", worker)).toBe(true);
      expect(
        allowsSseEvent(phase, "tool_use_progress", {
          ...worker,
          phase: "querying",
        }),
      ).toBe(true);
    }
  });

  it.each(TERMINAL_OR_STOPPING)(
    "allows turn_queue_started in phase %s",
    (phase) => {
      expect(allowsSseEvent(phase, "turn_queue_started")).toBe(true);
    },
  );

  it.each(TERMINAL_OR_STOPPING)(
    "allows user_interjection and turn_queued in phase %s",
    (phase) => {
      expect(allowsSseEvent(phase, "user_interjection")).toBe(true);
      expect(allowsSseEvent(phase, "turn_queued")).toBe(true);
    },
  );

  it.each(TERMINAL_OR_STOPPING)(
    "allows interaction_orphaned in phase %s (收尾 orphan 只出现在这里)",
    (phase) => {
      expect(allowsSseEvent(phase, "interaction_orphaned")).toBe(true);
    },
  );

  it("keeps workspace_op_required gated on conversation SSE allowlist", () => {
    // Cloud CLIENT_TOOL rides the device fulfill stream (no turnPhase). Sidecar
    // fulfills before the gate in dispatchSSEEvent. Allowlist still excludes these
    // so a stray conversation-bus frame does not pass as a normal SSE mutation.
    for (const phase of TERMINAL_OR_STOPPING) {
      expect(allowsSseEvent(phase, "workspace_op_required")).toBe(false);
    }
  });

  it("keeps host_op_required gated on conversation SSE allowlist", () => {
    for (const phase of TERMINAL_OR_STOPPING) {
      expect(allowsSseEvent(phase, "host_op_required")).toBe(false);
    }
  });

  it("allows post-turn auto-snapshot signals on terminal", () => {
    for (const phase of ["completed", "failed", "stopped"] as const) {
      expect(allowsSseEvent(phase, "workspace_snapshot_done")).toBe(true);
      expect(allowsSseEvent(phase, "workspace_snapshot_failed")).toBe(true);
    }
  });

  it.each(TERMINAL_OR_STOPPING)(
    "allows team_synthesis_preview in phase %s (detached captain-node live preview)",
    (phase) => {
      expect(allowsSseEvent(phase, "team_synthesis_preview")).toBe(true);
    },
  );
});
