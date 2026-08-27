// @vitest-environment jsdom
// 统一投影键 + 时间线标记不变量（时间线一期防回归）。
//
// 回放 conformance 向量（与 #/preview / pnpm conformance 同一份）走真实
// dispatchSSEEvent，断言两条会静默丢卡的接缝：
//   1) 投影键一致：interaction entries 以 `serverMessageId ?? id`（execMessageId）
//      落库，气泡查询必须用同一键（assistantProjectionId）——用本地 UUID 查询
//      曾让全部时间线卡片消失（有标记、无实体）。
//   2) 不变量「有交互卡必有时间线标记」：每个 timeline-kind interaction 在
//      `process[]` 里都有对应位置标记（底部堆叠回退已废除，缺标记即丢卡）。
import { replayFixtureNow } from "@/preview/replay";
import {
  assistantProjectionId,
  useConversationStore,
} from "@/stores/conversation";
import { useExecutionStore } from "@/stores/execution";
import {
  INTERACTION_BY_KIND,
  listMessageEntries,
  useInteractionStore,
} from "@/stores/interactions";
import type { InteractionKind } from "@/stores/interactions/registry";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import type { ProcessStep } from "@/types/events";
import { loadFixtures } from "@agentcore/protocol-conformance";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

/** Timeline-kind interactions = the ones whose card/痕迹 rides a process marker. */
const TIMELINE_KINDS: InteractionKind[] = [
  "ask_user",
  "plan_review",
  "escalation",
];

/** Weak-form kinds (D5): marker required; row gated on resolved/orphaned — not in strong card invariant. */
const TRACE_KINDS: InteractionKind[] = ["approval", "stage_card"];

/** Marker step id for a timeline interaction entry, per registry wiring. */
function markerMatches(step: ProcessStep, kind: InteractionKind, id: string) {
  const timeline = INTERACTION_BY_KIND[kind].timeline;
  if (!timeline) return false;
  if (step.kind !== timeline.processKind) return false;
  return (
    (step as unknown as Record<string, unknown>)[timeline.stepIdField] === id
  );
}

const FIXTURES = loadFixtures().filter((fx) =>
  (fx.projected.interactions ?? []).some((i: { kind: string }) =>
    [...TIMELINE_KINDS, ...TRACE_KINDS].includes(i.kind as InteractionKind),
  ),
);

describe("timeline projection key + marker invariant (fixtures)", () => {
  beforeEach(() => {
    useInteractionStore.setState({ byId: new Map() });
  });
  afterEach(() => {
    useExecutionStore.setState({ byId: {} });
  });

  it("covers the timeline fixture families", () => {
    // Sanity: the families the bug hit (检查点 / 计划复核)
    // + 弱式痕迹（审批 / 阶段推进卡）。开工卡事件对已退役，不再进此表。
    const names = FIXTURES.map((f) => f.name);
    expect(names).toEqual(
      expect.arrayContaining([
        "single_agent_checkpoint",
        "plan_review_paused",
        "multi_agent_legal_war_room",
        "multi_agent_stage_card_orphaned",
        "multi_agent_stage_card_start_debate",
      ]),
    );
    expect(names).not.toContain("team_preview_resolved_continue");
  });
  for (const fx of FIXTURES) {
    it(`${fx.name}: cards resolvable by projection key, every card marked`, () => {
      const cid = `tlp-${fx.name}`;
      useConversationStore.getState().dropConversationRuntime(cid);
      usePausedTurnStore.getState().clear(cid);

      replayFixtureNow(cid, fx.events, fx.description ?? fx.name);

      const rt = useConversationStore.getState().byId[cid];
      const assistant = rt?.messages.find((m) => m.role === "assistant");
      expect(assistant, "assistant bubble exists").toBeTruthy();
      if (!assistant) return;

      // (1) 投影键：AssistantMessage 的查询键必须命中全部 timeline entries。
      const projectionId = assistantProjectionId(assistant);
      const entries = listMessageEntries(cid, projectionId, TIMELINE_KINDS);
      const goldenTimelineCount = (fx.projected.interactions ?? []).filter(
        (i: { kind: string }) => (TIMELINE_KINDS as string[]).includes(i.kind),
      ).length;
      expect(entries.length, "projection-key lookup hits every card").toBe(
        goldenTimelineCount,
      );

      // (2) 不变量：每张卡在 process[] 里有对应位置标记。
      const process = assistant.process ?? [];
      for (const e of entries) {
        expect(
          process.some((s) => markerMatches(s, e.kind, e.id)),
          `${fx.name}: marker for ${e.kind}:${e.id} in process[] (${process
            .map((s) => s.kind)
            .join("→")})`,
        ).toBe(true);
      }

      // (3) 多 Agent 回合必有 team 标记（协作图只在标记槽渲染，无兜底）。
      if (fx.events.some((ev: { type: string }) => ev.type === "run_plan")) {
        expect(
          process.some((s) => s.kind === "team"),
          "run_plan turn carries a team marker",
        ).toBe(true);
      }

      // (4) 弱式不变量（D5）：有 approval interaction →
      // process[] 必有对应 required 时刻标记（行渲染由 resolved 门控，此处只断言锚点）。
      const traceEntries = listMessageEntries(cid, projectionId, TRACE_KINDS);
      for (const e of traceEntries) {
        expect(
          process.some((s) => markerMatches(s, e.kind, e.id)),
          `${fx.name}: weak marker for ${e.kind}:${e.id}`,
        ).toBe(true);
      }
    });
  }
});
