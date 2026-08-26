import {
  isCoordinationActive,
  resolveDefaultDelivery,
} from "@/lib/composerDelivery";
import { useConversationStore } from "@/stores/conversation";
import { useExecutionStore } from "@/stores/execution";
import { beforeEach, describe, expect, it } from "vitest";

const CID = "conv-delivery";

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useExecutionStore.setState({ byId: {} });
  useConversationStore.getState().switchConversation(CID);
});

describe("composerDelivery", () => {
  it("空闲 → steer", () => {
    expect(resolveDefaultDelivery(false, CID)).toBe("steer");
  });

  it("经典 in-flight（无 plan）→ queue", () => {
    useConversationStore.getState().createAssistantMessage(CID);
    expect(isCoordinationActive(CID)).toBe(false);
    expect(resolveDefaultDelivery(true, CID)).toBe("queue");
  });

  it("协调活跃（有 plan）→ queue", () => {
    useConversationStore.getState().createAssistantMessage(CID);
    const messages = useConversationStore.getState().byId[CID]?.messages ?? [];
    const aid = messages[0]?.id;
    expect(aid).toBeTruthy();
    useExecutionStore.setState({
      byId: {
        [aid as string]: {
          plan: {
            executionId: "e1",
            steps: [],
            acts: [{ id: "act-1", title: null }],
          },
          frames: [],
          status: "running",
          debate: null,
          debateRounds: [],
          crossExamEnabled: false,
          debateOpening: null,
          coordinationWait: null,
          deliveryStatus: null,
          userInterjections: [],
          teamSynthesisPreview: null,
        } as never,
      },
    });
    expect(isCoordinationActive(CID)).toBe(true);
    expect(resolveDefaultDelivery(true, CID)).toBe("queue");
  });
});
