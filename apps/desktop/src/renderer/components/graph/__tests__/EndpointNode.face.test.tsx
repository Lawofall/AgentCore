// @vitest-environment jsdom
import { NODE_HEIGHT, NODE_WIDTH } from "@/lib/graphMetrics";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EndpointNodeFace, endpointBodyText } from "../EndpointNode";

describe("endpointBodyText", () => {
  it("uses deliverable preview when present", () => {
    expect(
      endpointBodyText({
        isInput: false,
        preview: "成稿开头",
        statusCaption: "等待「撰写员」(1/2)",
      }),
    ).toBe("成稿开头");
  });

  it("puts wait caption in the body when preview is empty", () => {
    expect(
      endpointBodyText({
        isInput: false,
        preview: "",
        statusCaption: "等待「撰写员」(1/2)",
      }),
    ).toBe("等待「撰写员」(1/2)");
  });

  it("does not echo wait copy onto the input bookend", () => {
    expect(
      endpointBodyText({
        isInput: true,
        preview: "",
        statusCaption: "等待「撰写员」",
      }),
    ).toBe("");
  });
});

describe("EndpointNodeFace · whiteboard slot", () => {
  it("pins CEO wait-state card to NODE_WIDTH × NODE_HEIGHT", () => {
    render(
      <EndpointNodeFace
        isInput={false}
        status="running"
        statusCaption="等待「协作图渲染链路审计员」(0/1)"
        preview=""
      />,
    );
    const card = screen.getByTestId("endpoint-node-card");
    expect(card.style.width).toBe(`${NODE_WIDTH}px`);
    expect(card.style.height).toBe(`${NODE_HEIGHT}px`);
    expect(screen.getByTestId("captain-sink-label").textContent).toBe(
      "正在收尾…",
    );
    expect(screen.getByTestId("captain-sink-preview").textContent).toBe(
      "等待「协作图渲染链路审计员」(0/1)",
    );
  });

  it("pins the input bookend to the same slot", () => {
    render(
      <EndpointNodeFace
        isInput
        status="completed"
        preview="协作图渲染链路审计员"
      />,
    );
    const card = screen.getByTestId("endpoint-node-card");
    expect(card.style.height).toBe(`${NODE_HEIGHT}px`);
    expect(card.textContent).toContain("你的任务");
    expect(card.textContent).not.toContain("对话发起");
    expect(card.textContent).toContain("协作图渲染链路审计员");
  });
});
