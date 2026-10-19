// @vitest-environment jsdom
/**
 * 法律条款页的标题归属：正文只出章节，标题 + 更新日期由宿主（设置页头 / 登录前
 * 阅读面板）出一次——原来两边都出，页面上是两个 h1、两行更新日期。
 */
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LegalDocPane } from "../LegalDocPane";
import { LegalSettingsPage } from "../LegalSettingsPage";
import { LEGAL_DOCS } from "../content";

function renderSettingsDoc(docId: string) {
  return render(
    <MemoryRouter initialEntries={[`/more/legal/${docId}`]}>
      <Routes>
        <Route path="/more/legal/:docId" element={<LegalSettingsPage />} />
        <Route path="/more/about" element={<p>关于页</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
});

describe("LegalSettingsPage", () => {
  it("renders the title and 更新日期 exactly once", () => {
    const { container } = renderSettingsDoc("terms");
    const doc = LEGAL_DOCS.terms;
    const headings = container.querySelectorAll("h1");
    expect(headings).toHaveLength(1);
    expect(headings[0]?.textContent).toBe(doc.title);
    expect(screen.getAllByText(`更新日期：${doc.updatedAt}`)).toHaveLength(1);
    // 正文仍在：章节标题是 h2。
    expect(container.querySelectorAll("h2").length).toBe(doc.sections.length);
    expect(
      screen.getByRole("heading", { name: "九、订阅与额度" }),
    ).toBeTruthy();
  });

  it("falls back to 关于 for an unknown doc id", () => {
    renderSettingsDoc("nope");
    expect(screen.getByText("关于页")).toBeTruthy();
  });
});

describe("LegalDocPane (登录前)", () => {
  it("still owns a title and 更新日期 of its own", () => {
    const doc = LEGAL_DOCS.privacy;
    const { container } = render(
      <LegalDocPane docId="privacy" onBack={vi.fn()} />,
    );
    const headings = container.querySelectorAll("h1");
    expect(headings).toHaveLength(1);
    expect(headings[0]?.textContent).toBe(doc.title);
    expect(screen.getAllByText(`更新日期：${doc.updatedAt}`)).toHaveLength(1);
  });
});
