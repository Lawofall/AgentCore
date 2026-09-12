// @vitest-environment jsdom
import {
  createDocShare,
  getDoc,
  listDocShares,
  saveDocBody,
} from "@/services/docs";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/docs", () => ({
  getDoc: vi.fn(),
  renameDoc: vi.fn(),
  saveDocBody: vi.fn(),
  listDocShares: vi.fn(),
  createDocShare: vi.fn(),
  revokeDocShare: vi.fn(),
}));

vi.mock("@/lib/clipboard", () => ({
  copyText: vi.fn().mockResolvedValue(true),
}));

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));

import { DocEditorPage } from "../DocEditorPage";

const get = vi.mocked(getDoc);
const save = vi.mocked(saveDocBody);
const listShares = vi.mocked(listDocShares);
const createShare = vi.mocked(createDocShare);

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

function renderEditor() {
  return render(
    <MemoryRouter initialEntries={["/docs/d1"]}>
      <Routes>
        <Route path="/docs/:docId" element={<DocEditorPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("DocEditorPage", () => {
  beforeEach(() => {
    get.mockReset();
    save.mockReset();
    listShares.mockReset();
    createShare.mockReset();
    get.mockResolvedValue({
      id: "d1",
      title: "Q3",
      folder_id: "f1",
      folder_name: "方案",
      version: 1,
      can_write: true,
      created_at: "2026-09-12T00:00:00Z",
      updated_at: "2026-09-12T00:00:00Z",
      body: {
        schemaVersion: 1,
        blocks: [{ id: "p1", type: "paragraph", text: "初稿" }],
      },
    });
    save.mockResolvedValue({ ok: true, version: 2, conflict: false });
    listShares.mockResolvedValue([]);
    createShare.mockResolvedValue({
      id: "s1",
      url: "/shared/s1",
      title: "Q3",
      created_at: "2026-09-12T00:00:00Z",
      expires_at: null,
    });
  });

  it("loads title and paragraph, autosaves an edit", async () => {
    renderEditor();
    expect(await screen.findByDisplayValue("Q3")).toBeTruthy();
    const area = await screen.findByDisplayValue("初稿");
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    await act(async () => {
      fireEvent.change(area, { target: { value: "改过" } });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(900);
    });
    expect(save).toHaveBeenCalled();
    const [, body, baseline] = save.mock.calls[0] ?? [];
    expect(baseline).toBe(1);
    expect(body.blocks[0]).toMatchObject({ type: "paragraph", text: "改过" });
  });

  it("hides editors and share for a read-only member", async () => {
    get.mockResolvedValue({
      id: "d1",
      title: "Q3",
      folder_id: "f1",
      folder_name: "方案",
      version: 1,
      can_write: false,
      created_at: "2026-09-12T00:00:00Z",
      updated_at: "2026-09-12T00:00:00Z",
      body: {
        schemaVersion: 1,
        blocks: [{ id: "p1", type: "paragraph", text: "初稿" }],
      },
    });
    renderEditor();
    expect(await screen.findByText("只读成员不能改这份文档。")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "段落" })).toBeNull();
    expect(screen.queryByRole("button", { name: "表" })).toBeNull();
    expect(screen.queryByRole("button", { name: "图" })).toBeNull();
    expect(screen.queryByRole("button", { name: "删除这块" })).toBeNull();
    expect(screen.queryByRole("button", { name: "分享" })).toBeNull();
  });

  it("adds a default table block for writers", async () => {
    renderEditor();
    expect(await screen.findByDisplayValue("Q3")).toBeTruthy();
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "表" }));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(900);
    });
    expect(save).toHaveBeenCalled();
    const [, body] = save.mock.calls[0] ?? [];
    expect(body.blocks).toHaveLength(2);
    expect(body.blocks[1]).toMatchObject({
      type: "table",
      columns: ["", ""],
      rows: [
        ["", ""],
        ["", ""],
        ["", ""],
      ],
    });
  });

  it("shows a table read-only without add-table control", async () => {
    get.mockResolvedValue({
      id: "d1",
      title: "Q3",
      folder_id: "f1",
      folder_name: "方案",
      version: 1,
      can_write: false,
      created_at: "2026-09-12T00:00:00Z",
      updated_at: "2026-09-12T00:00:00Z",
      body: {
        schemaVersion: 1,
        blocks: [
          {
            id: "t1",
            type: "table",
            columns: ["指标", "值"],
            rows: [["收入", "100"]],
          },
        ],
      },
    });
    renderEditor();
    expect(await screen.findByDisplayValue("指标")).toBeTruthy();
    expect(await screen.findByDisplayValue("收入")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "表" })).toBeNull();
    expect(screen.queryByRole("button", { name: "加列" })).toBeNull();
  });

  it("adds a default chart block for writers", async () => {
    renderEditor();
    expect(await screen.findByDisplayValue("Q3")).toBeTruthy();
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "图" }));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(900);
    });
    expect(save).toHaveBeenCalled();
    const [, body] = save.mock.calls[0] ?? [];
    expect(body.blocks).toHaveLength(2);
    expect(body.blocks[1]).toMatchObject({
      type: "chart",
      kind: "bar",
      title: "",
      items: [
        { label: "", value: 0 },
        { label: "", value: 0 },
        { label: "", value: 0 },
      ],
    });
  });

  it("shows a chart read-only without add-chart control", async () => {
    get.mockResolvedValue({
      id: "d1",
      title: "Q3",
      folder_id: "f1",
      folder_name: "方案",
      version: 1,
      can_write: false,
      created_at: "2026-09-12T00:00:00Z",
      updated_at: "2026-09-12T00:00:00Z",
      body: {
        schemaVersion: 1,
        blocks: [
          {
            id: "ch1",
            type: "chart",
            kind: "bar",
            title: "季度",
            items: [{ label: "Q1", value: 3 }],
          },
        ],
      },
    });
    renderEditor();
    expect(await screen.findByDisplayValue("季度")).toBeTruthy();
    expect(await screen.findByDisplayValue("Q1")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "图" })).toBeNull();
    expect(screen.queryByRole("button", { name: "加点" })).toBeNull();
  });

  it("flushes unsaved blocks before minting a share", async () => {
    renderEditor();
    const area = await screen.findByDisplayValue("初稿");
    await act(async () => {
      fireEvent.change(area, { target: { value: "改过" } });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "分享" }));
    });
    expect(await screen.findByText("分享文档")).toBeTruthy();
    const mint = await screen.findByRole("button", { name: "创建分享链接" });
    await act(async () => {
      fireEvent.click(mint);
    });
    await waitFor(() => expect(save).toHaveBeenCalled());
    await waitFor(() => expect(createShare).toHaveBeenCalled());
    const [, body] = save.mock.calls[0] ?? [];
    expect(body.blocks[0]).toMatchObject({ type: "paragraph", text: "改过" });
    expect(createShare.mock.calls[0]?.[0]).toBe("d1");
  });
});
