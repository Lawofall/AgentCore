// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/narrowLayout", () => ({
  useNarrowLayoutState: () => ({
    isNarrow: true,
    hideChrome: false,
    conversationDrawerOpen: false,
    setConversationDrawerOpen: () => undefined,
  }),
}));

import { MorePage } from "../MorePage";

afterEach(() => {
  cleanup();
});

describe("MorePage narrow", () => {
  it("shows the index as a list and hides desktop-only settings", () => {
    render(
      <MemoryRouter initialEntries={["/more"]}>
        <MorePage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: "账户设置" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "模型" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "消息隐私" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "快捷键" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Git 凭据" })).toBeNull();
    expect(screen.queryByRole("link", { name: "通用" })).toBeNull();
    expect(screen.queryByRole("link", { name: "反馈" })).toBeNull();
  });

  it("pushes a sub-page with a back header", () => {
    render(
      <MemoryRouter initialEntries={["/more/account"]}>
        <Routes>
          <Route path="/more" element={<MorePage />}>
            <Route path="account" element={<div>账户正文</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByRole("button", { name: "返回" })).toBeTruthy();
    expect(screen.getByText("账户正文")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "模型" })).toBeNull();
  });

  it("legal doc uses the document title in the back header", () => {
    render(
      <MemoryRouter initialEntries={["/more/legal/terms"]}>
        <Routes>
          <Route path="/more" element={<MorePage />}>
            <Route path="legal/:docId" element={<div>条款正文</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "用户服务协议" })).toBeTruthy();
    expect(screen.getByText("条款正文")).toBeTruthy();
  });
});
