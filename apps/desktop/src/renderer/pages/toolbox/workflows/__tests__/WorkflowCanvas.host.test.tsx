// @vitest-environment jsdom
import type { WorkflowDefinition } from "@/services/workflowDefinition";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WorkflowCanvas } from "../WorkflowCanvas";

let lastRfProps: Record<string, unknown> | null = null;

vi.mock("@xyflow/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@xyflow/react")>();
  return {
    ...actual,
    ReactFlow: (props: Record<string, unknown>) => {
      lastRfProps = props;
      return <div data-testid="rf" />;
    },
  };
});

const DEFINITION: WorkflowDefinition = {
  nodes: [{ id: "s", kind: "agent_step", role: "写手", task: "写稿" }],
  edges: [],
};

describe("WorkflowCanvas RF host", () => {
  it("does not pass fitView / fitViewOptions (StoreUpdater loop)", () => {
    lastRfProps = null;
    render(
      <WorkflowCanvas
        definition={DEFINITION}
        selectedId={null}
        onChange={() => undefined}
        onSelect={() => undefined}
      />,
    );
    expect(lastRfProps).not.toBeNull();
    const props: Record<string, unknown> = lastRfProps ?? {};
    expect("fitView" in props).toBe(false);
    expect("fitViewOptions" in props).toBe(false);
    expect(typeof props.onInit).toBe("function");
  });
});
