import type { CapabilityPack } from "@/services/capabilities";
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PackOverview } from "../CapabilityPackCard";

const legalPack: CapabilityPack = {
  id: "legal",
  name: "法律能力",
  summary: "合同审查、法规检索与合规把关。",
  skills: [
    {
      name: "contract_review",
      summary: "审查合同风险条款",
      body: "# 合同审查\n逐步核对关键条款。",
    },
  ],
};

afterEach(cleanup);

describe("PackOverview 纯展示", () => {
  it("展示名称、简介与包内技能，无启用/停用交互", () => {
    render(<PackOverview pack={legalPack} />);
    expect(
      document.querySelector('[data-capability-pack="legal"]'),
    ).toBeTruthy();
    expect(screen.getByText("法律能力")).toBeTruthy();
    expect(screen.getByText("合同审查、法规检索与合规把关。")).toBeTruthy();
    expect(screen.getByText("包内技能")).toBeTruthy();
    expect(screen.getByText("审查合同风险条款")).toBeTruthy();
    expect(screen.queryByText("contract_review")).toBeNull();
    expect(screen.queryByRole("button", { name: /启用|停用/ })).toBeNull();
    expect(screen.queryByText("未启用")).toBeNull();
    expect(screen.queryByText("已启用")).toBeNull();
  });
});
