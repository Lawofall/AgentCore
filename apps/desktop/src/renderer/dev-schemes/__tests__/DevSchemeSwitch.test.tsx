// @vitest-environment jsdom
import {
  isSchemePanelOpen,
  registerScheme,
  resetSchemesForTests,
  setSchemePanelOpen,
} from "@/lib/devSchemes";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { DevSchemeSwitch } from "../DevSchemeSwitch";

beforeEach(() => {
  resetSchemesForTests();
  registerScheme({
    id: "stub",
    label: "桩",
    group: "stub",
    groupLabel: "桩",
    slots: {},
  });
  setSchemePanelOpen(false);
});

afterEach(() => {
  cleanup();
  setSchemePanelOpen(false);
  resetSchemesForTests();
});

describe("DevSchemeSwitch", () => {
  it("starts hidden — no leftover corner icon", () => {
    render(<DevSchemeSwitch />);
    expect(screen.queryByTestId("dev-scheme-switch")).toBeNull();
    expect(screen.queryByRole("button", { name: "展开界面方案" })).toBeNull();
  });

  it("X closes the panel and leaves nothing", () => {
    render(<DevSchemeSwitch />);
    act(() => setSchemePanelOpen(true));
    expect(screen.getByTestId("dev-scheme-switch")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "关闭界面方案" }));
    expect(screen.queryByTestId("dev-scheme-switch")).toBeNull();
    expect(isSchemePanelOpen()).toBe(false);
    expect(screen.queryByRole("button", { name: "展开界面方案" })).toBeNull();
  });

  it("Escape closes the panel", () => {
    render(<DevSchemeSwitch />);
    act(() => setSchemePanelOpen(true));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("dev-scheme-switch")).toBeNull();
  });

  it("pointerdown outside closes; inside does not", () => {
    render(
      <>
        <DevSchemeSwitch />
        <button type="button">page</button>
      </>,
    );
    act(() => setSchemePanelOpen(true));
    fireEvent.pointerDown(screen.getByRole("tab", { name: "现行" }));
    expect(screen.getByTestId("dev-scheme-switch")).toBeTruthy();
    fireEvent.pointerDown(screen.getByRole("button", { name: "page" }));
    expect(screen.queryByTestId("dev-scheme-switch")).toBeNull();
  });
});
