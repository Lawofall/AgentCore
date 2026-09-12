import { NODE_HEIGHT } from "@agentcore/graph-layout";
import { describe, expect, it } from "vitest";
import { FACE_CARD_HEIGHT } from "../shared";

describe("AgentNodeFace · fixed layout footprint", () => {
  it("FACE_CARD_HEIGHT equals NODE_HEIGHT (whiteboard slot)", () => {
    expect(FACE_CARD_HEIGHT).toBe(NODE_HEIGHT);
  });
});
