// @vitest-environment jsdom
/**
 * Cloud run tool card 「打开预览」: show only when preview_available + conversationId;
 * click mints then openExternal / window.open. Never parse a URL from model text.
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import type { ProcessStep } from "@/types/events";
import type { BrowserApi } from "@shared/browser-contract";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { apiPost, notifyActionError } = vi.hoisted(() => ({
  apiPost: vi.fn(),
  notifyActionError: vi.fn(),
}));

vi.mock("@/services/api", () => ({
  api: { post: (...args: unknown[]) => apiPost(...args) },
}));

vi.mock("@/lib/toast", () => ({
  notifyActionError: (...args: unknown[]) => notifyActionError(...args),
}));

vi.mock("@/stores/sidePanel", () => ({
  useSidePanelStore: Object.assign(
    (selector: (s: { showBrowser: () => void }) => unknown) =>
      selector({ showBrowser: () => {} }),
    { getState: () => ({ showBrowser: () => {} }) },
  ),
}));

vi.mock("@/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
}));

import { ToolLine } from "../ToolLine";

type ToolStep = Extract<ProcessStep, { kind: "tool" }>;

function step(over: Partial<ToolStep>): ToolStep {
  return {
    kind: "tool",
    id: "call_1",
    tool_name: "run",
    arguments: {},
    result: null,
    display: null,
    status: "success",
    ...over,
  };
}

const PREVIEW_URL =
  "https://preview.example.test/p/abc?token=eyJhbGciOiJIUzI1NiJ9.payload.sig";

function runPreviewStep(over?: Partial<ToolStep>): ToolStep {
  return step({
    result: "running",
    display: {
      process_id: "tp-abc",
      status: "running",
      preview_available: true,
      http_ports: [5173],
    },
    status: "success",
    ...over,
  });
}

function renderLine(s: ToolStep, conversationId?: string | null) {
  return render(
    <TooltipProvider>
      <ToolLine step={s} conversationId={conversationId} />
    </TooltipProvider>,
  );
}

const openExternal = vi.fn().mockResolvedValue({ ok: true });

afterEach(() => {
  cleanup();
  window.browserApi = undefined;
  vi.restoreAllMocks();
});

beforeEach(() => {
  apiPost.mockReset();
  notifyActionError.mockReset();
  openExternal.mockReset();
  openExternal.mockResolvedValue({ ok: true });
  apiPost.mockResolvedValue({ url: PREVIEW_URL });
  window.browserApi = { openExternal } as unknown as BrowserApi;
});

describe("ToolLine · 云端 run 打开预览", () => {
  it("shows 打开预览 only with preview_available + conversationId", () => {
    renderLine(runPreviewStep(), "conv-1");
    expect(screen.getByRole("button", { name: "打开预览" })).toBeTruthy();
    expect(screen.queryByText(/eyJhbGciOiJIUzI1NiJ9/)).toBeNull();
  });

  it("hides the button when preview_available is absent", () => {
    renderLine(
      step({
        display: {
          process_id: "tp-abc",
          status: "running",
          http_ports: [5173],
        },
      }),
      "conv-1",
    );
    expect(screen.queryByRole("button", { name: "打开预览" })).toBeNull();
  });

  it("hides the button without conversationId", () => {
    renderLine(runPreviewStep());
    expect(screen.queryByRole("button", { name: "打开预览" })).toBeNull();
  });

  it("hides the button without display.process_id", () => {
    renderLine(
      step({
        display: { preview_available: true, http_ports: [5173] },
      }),
      "conv-1",
    );
    expect(screen.queryByRole("button", { name: "打开预览" })).toBeNull();
  });

  it("does not treat Agent browser jpeg as preview", () => {
    renderLine(
      step({
        tool_name: "browser",
        arguments: { action: "navigate", url: "https://example.com" },
        display: {
          kind: "browser",
          action: "navigate",
          url: "https://example.com",
          preview_available: true,
          process_id: "tp-abc",
        },
      }),
      "conv-1",
    );
    expect(screen.queryByRole("button", { name: "打开预览" })).toBeNull();
  });

  it("click posts mint then openExternal", async () => {
    renderLine(runPreviewStep(), "conv-1");
    fireEvent.click(screen.getByRole("button", { name: "打开预览" }));
    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith("/v1/preview/token", {
        conversation_id: "conv-1",
        process_id: "tp-abc",
      });
      expect(openExternal).toHaveBeenCalledWith({ url: PREVIEW_URL });
    });
    expect(notifyActionError).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain("eyJhbGciOiJIUzI1NiJ9");
  });

  it("click falls back to window.open when browserApi is absent", async () => {
    window.browserApi = undefined;
    const windowOpen = vi.spyOn(window, "open").mockReturnValue(null);
    renderLine(runPreviewStep(), "conv-1");
    fireEvent.click(screen.getByRole("button", { name: "打开预览" }));
    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith("/v1/preview/token", {
        conversation_id: "conv-1",
        process_id: "tp-abc",
      });
      expect(windowOpen).toHaveBeenCalledWith(
        PREVIEW_URL,
        "_blank",
        "noopener,noreferrer",
      );
    });
    expect(openExternal).not.toHaveBeenCalled();
  });

  it("one button per http_ports when length > 1; click sends that port", async () => {
    renderLine(
      runPreviewStep({
        display: {
          process_id: "tp-abc",
          status: "running",
          preview_available: true,
          http_ports: [5173, 3000],
        },
      }),
      "conv-1",
    );
    expect(
      screen.getByRole("button", { name: "打开预览 · 5173" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "打开预览 · 3000" }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "打开预览" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "打开预览 · 3000" }));
    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith("/v1/preview/token", {
        conversation_id: "conv-1",
        process_id: "tp-abc",
        port: 3000,
      });
    });
  });

  it("toasts mint failure without showing the JWT", async () => {
    apiPost.mockRejectedValue(new Error("API 503"));
    renderLine(runPreviewStep(), "conv-1");
    fireEvent.click(screen.getByRole("button", { name: "打开预览" }));
    await waitFor(() => {
      expect(notifyActionError).toHaveBeenCalledWith(
        "打开预览失败",
        expect.any(Error),
      );
    });
    expect(openExternal).not.toHaveBeenCalled();
    const toasted = notifyActionError.mock.calls.flat().map(String).join(" ");
    expect(toasted).not.toMatch(/eyJhbGciOiJIUzI1NiJ9/);
    expect(document.body.textContent).not.toContain("eyJhbGciOiJIUzI1NiJ9");
  });
});
