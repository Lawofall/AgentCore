// @vitest-environment jsdom
import type { WorkflowDefinition } from "@/services/workflowDefinition";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkflowNodeInspector } from "../WorkflowNodeInspector";

function definitionWithDeliverable(
  deliverable: Record<string, unknown> | undefined,
): WorkflowDefinition {
  return {
    nodes: [
      {
        id: "step-1",
        kind: "agent_step",
        role: "调研员",
        task: "扫一遍竞品动态",
        deliverable,
      },
    ],
    edges: [],
  };
}

/** 画布不直接编辑的那半份契约。 */
const CONTRACT_REST = {
  artifacts: ["brief.md"],
  required_sections: ["结论", "风险"],
  strict: true,
  citation_mode: "inline",
};

function renderInspector(definition: WorkflowDefinition) {
  const onChange = vi.fn();
  render(
    <WorkflowNodeInspector
      definition={definition}
      selectedId="step-1"
      onChange={onChange}
    />,
  );
  return onChange;
}

function nextStep(onChange: ReturnType<typeof vi.fn>) {
  expect(onChange).toHaveBeenCalledTimes(1);
  const next = onChange.mock.calls[0][0] as WorkflowDefinition;
  const node = next.nodes[0];
  expect(node.kind).toBe("agent_step");
  return node.kind === "agent_step" ? node : undefined;
}

afterEach(() => {
  cleanup();
});

describe("WorkflowNodeInspector 节点编辑", () => {
  it("没有交付形式下拉，改角色仍保留 artifacts / required_sections / strict", () => {
    const onChange = renderInspector(definitionWithDeliverable(CONTRACT_REST));

    expect(screen.queryByLabelText(/交付形式/)).toBeNull();
    expect(screen.queryByText("纯文字")).toBeNull();
    expect(screen.queryByText("改工程")).toBeNull();

    fireEvent.change(screen.getByLabelText("角色"), {
      target: { value: "分析员" },
    });

    const node = nextStep(onChange);
    expect(node?.role).toBe("分析员");
    expect(node?.task).toBe("扫一遍竞品动态");
    expect(node?.deliverable).toEqual(CONTRACT_REST);
  });

  it("可以改任务说明", () => {
    const onChange = renderInspector(definitionWithDeliverable(undefined));

    fireEvent.change(screen.getByLabelText("任务说明"), {
      target: { value: "重写结论" },
    });

    expect(nextStep(onChange)?.task).toBe("重写结论");
  });
});

/** 任务文本里的 `{{key}}` 得让用户认出是变量，而不是读成乱码。 */
function definitionWithTask(
  task: string,
  slots?: WorkflowDefinition["slots"],
): WorkflowDefinition {
  return {
    nodes: [{ id: "step-1", kind: "agent_step", role: "调研员", task }],
    edges: [],
    slots,
  };
}

describe("WorkflowNodeInspector 占位符", () => {
  it("列出任务引用的参数，并按默认值给出成文预览", () => {
    renderInspector(
      definitionWithTask("调研 {{topic}} 的定价", [
        { key: "topic", label: "调研主题", default: "Notion 的协作功能" },
      ]),
    );

    expect(screen.getByText("{{topic}}")).toBeTruthy();
    expect(screen.getByText("调研主题")).toBeTruthy();
    expect(
      screen.getByText(/按默认值：调研 Notion 的协作功能 的定价/),
    ).toBeTruthy();
  });

  it("引用了没声明的参数就说它不会被替换", () => {
    renderInspector(definitionWithTask("调研 {{angle}}", []));

    expect(screen.getByText("{{angle}}")).toBeTruthy();
    expect(screen.getByText("未声明")).toBeTruthy();
  });

  it("没有占位符时不摆这块提示", () => {
    renderInspector(definitionWithTask("扫一遍竞品动态"));

    expect(screen.queryByText(/按默认值/)).toBeNull();
  });
});
