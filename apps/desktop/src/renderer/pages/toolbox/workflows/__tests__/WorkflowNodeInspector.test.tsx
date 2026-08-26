// @vitest-environment jsdom
import type { WorkflowDefinition } from "@/services/workflowDefinition";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkflowNodeInspector } from "../WorkflowNodeInspector";

/** 交付契约不止 `form`：其余字段只有服务端与 playbook 会写，前端必须原样带走。 */
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

const FULL_CONTRACT = { form: "files", ...CONTRACT_REST };

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

function nextDeliverable(onChange: ReturnType<typeof vi.fn>) {
  expect(onChange).toHaveBeenCalledTimes(1);
  const next = onChange.mock.calls[0][0] as WorkflowDefinition;
  const node = next.nodes[0];
  return node.kind === "agent_step" ? node.deliverable : undefined;
}

function deliverableSelect() {
  return screen.getByLabelText(/交付形式/) as HTMLSelectElement;
}

afterEach(() => {
  cleanup();
});

describe("WorkflowNodeInspector 交付形式", () => {
  it("改交付形式不会抹掉 artifacts / required_sections / strict", () => {
    const onChange = renderInspector(definitionWithDeliverable(FULL_CONTRACT));

    fireEvent.change(deliverableSelect(), {
      target: { value: "workspace" },
    });

    expect(nextDeliverable(onChange)).toEqual({
      ...CONTRACT_REST,
      form: "workspace",
    });
  });

  it("非法旧自由文按文档档显示，改档仍保留其余契约", () => {
    const onChange = renderInspector(
      definitionWithDeliverable({ form: "自由文旧值", ...CONTRACT_REST }),
    );

    expect(deliverableSelect().value).toBe("files");

    fireEvent.change(deliverableSelect(), {
      target: { value: "prose" },
    });

    expect(nextDeliverable(onChange)).toEqual({
      ...CONTRACT_REST,
      form: "prose",
    });
  });

  it("未声明 form 按文档档显示，选项是三选一", () => {
    renderInspector(definitionWithDeliverable(undefined));
    expect(deliverableSelect().value).toBe("files");
    expect([...deliverableSelect().options].map((o) => o.value)).toEqual([
      "prose",
      "files",
      "workspace",
    ]);
  });

  it("原本没有交付契约时改档只写 form", () => {
    const onChange = renderInspector(definitionWithDeliverable(undefined));

    fireEvent.change(deliverableSelect(), {
      target: { value: "prose" },
    });

    expect(nextDeliverable(onChange)).toEqual({ form: "prose" });
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
