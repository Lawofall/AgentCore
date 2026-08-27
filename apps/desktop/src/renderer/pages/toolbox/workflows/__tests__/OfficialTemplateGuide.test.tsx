// @vitest-environment jsdom
import type { WorkflowTemplate } from "@/services/workflows";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OfficialTemplateGuide } from "../OfficialTemplateGuide";

function tpl(id: string, title: string): WorkflowTemplate {
  return { id, title, summary: "", slots: [] };
}

const CATALOG: WorkflowTemplate[] = [
  tpl("map_fanout", "多角摸底"),
  tpl("cite_write_review", "调研报告成文"),
  tpl("build_app", "从零搭应用"),
];

describe("OfficialTemplateGuide", () => {
  it("routes each goal to a title taken from the catalog", () => {
    render(<OfficialTemplateGuide templates={CATALOG} />);
    const text =
      screen.getByTestId("official-template-guide").textContent ?? "";
    for (const t of CATALOG) expect(text).toContain(`「${t.title}」`);
  });

  it("never names a template missing from the catalog", () => {
    render(
      <OfficialTemplateGuide
        templates={CATALOG.filter((t) => t.id !== "build_app")}
      />,
    );
    const text =
      screen.getByTestId("official-template-guide").textContent ?? "";
    expect(text).toContain("「多角摸底」");
    expect(text).not.toContain("从零搭应用");
    expect(text).not.toContain("方案对比选型");
    expect(text).not.toMatch(/决策对比|目录有则选用|compare_options/);
  });

  it("hides itself when the catalog has nothing it knows how to route", () => {
    render(
      <OfficialTemplateGuide
        templates={[tpl("brand_new_shape", "全新形状")]}
      />,
    );
    expect(screen.queryByTestId("official-template-guide")).toBeNull();
  });

  it("shows titles, never raw playbook ids", () => {
    render(<OfficialTemplateGuide templates={CATALOG} />);
    const text =
      screen.getByTestId("official-template-guide").textContent ?? "";
    expect(text).not.toMatch(
      /map_fanout|cite_write_review|build_app|compare_options/,
    );
  });
});
