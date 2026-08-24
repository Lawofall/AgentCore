// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NarrowBlockedPage, NarrowLayoutProvider } from "../narrowLayout";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function stubViewport(narrow: boolean): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: narrow && query.includes("767"),
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
}

function renderBlocked(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <NarrowLayoutProvider>
        <Routes>
          <Route path="/" element={<div>home</div>} />
          <Route path="/more" element={<div>more</div>} />
          <Route
            path="/toolbox"
            element={
              <NarrowBlockedPage>
                <div>toolbox</div>
              </NarrowBlockedPage>
            }
          />
          <Route
            path="/more/shortcuts"
            element={
              <NarrowBlockedPage>
                <div>shortcuts</div>
              </NarrowBlockedPage>
            }
          />
        </Routes>
      </NarrowLayoutProvider>
    </MemoryRouter>,
  );
}

describe("NarrowBlockedPage", () => {
  it("renders children on a wide viewport", () => {
    stubViewport(false);
    renderBlocked("/toolbox");
    expect(screen.getByText("toolbox")).toBeTruthy();
  });

  it("sends blocked toolbox home on a narrow viewport", () => {
    stubViewport(true);
    renderBlocked("/toolbox");
    expect(screen.getByText("home")).toBeTruthy();
    expect(screen.queryByText("toolbox")).toBeNull();
  });

  it("sends hidden settings to /more on a narrow viewport", () => {
    stubViewport(true);
    renderBlocked("/more/shortcuts");
    expect(screen.getByText("more")).toBeTruthy();
    expect(screen.queryByText("shortcuts")).toBeNull();
  });
});
