// @vitest-environment jsdom
/**
 * 信封投影跟这场走。没这场时只记草稿，不写账户。
 */

import { api } from "@/services/api";
import {
  __resetEnvelopeSwitchStoresForTests,
  draftEnvelopeBoard,
} from "@/services/envelopeSwitches";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/api", () => ({
  api: { get: vi.fn(), put: vi.fn() },
}));
vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));

import { EnvelopeSwitchList } from "../EnvelopeSwitchList";

const get = vi.mocked(api.get);
const put = vi.mocked(api.put);

function board(omitted: string[]) {
  const off = new Set(omitted);
  return {
    omitted,
    switches: [
      {
        id: "runtime_date",
        label: "日期",
        summary: "信封里报当前日期。主管和队员读同一场。",
        off: off.has("runtime_date"),
      },
      {
        id: "file_index",
        label: "文件索引",
        summary: "主管信封附上文件名单。队员不读这份名单。",
        off: off.has("file_index"),
      },
    ],
  };
}

beforeEach(() => {
  __resetEnvelopeSwitchStoresForTests();
  get.mockReset();
  put.mockReset();
  get.mockResolvedValue(board([]));
  put.mockImplementation(async (_url: string, body: unknown) =>
    board((body as { omitted: string[] }).omitted),
  );
});

afterEach(cleanup);

describe("EnvelopeSwitchList", () => {
  it("草稿名册盖住每一行投影", () => {
    expect(draftEnvelopeBoard().switches.map((row) => row.id)).toEqual([
      "runtime_date",
      "execution",
      "boundary",
      "desk",
      "system",
      "git",
      "client",
      "mounts",
      "gaps",
      "sandbox",
      "interpreters",
      "file_index",
    ]);
  });

  it("没这场时拨开关写星标装配", async () => {
    render(<EnvelopeSwitchList scope="draft" />);
    fireEvent.click(await screen.findByRole("switch", { name: "日期" }));
    await waitFor(() => {
      expect(put).toHaveBeenCalledWith("/v1/users/me/envelope-switches", {
        omitted: ["runtime_date"],
      });
    });
  });

  it("已有这场时拨开关写这场", async () => {
    render(
      <EnvelopeSwitchList
        scope="conversation"
        conversationId="c1"
        conversationTitle="周报"
      />,
    );
    const date = await screen.findByRole("switch", { name: "日期" });
    fireEvent.click(date);
    await waitFor(() => {
      expect(put).toHaveBeenCalledWith(
        "/v1/conversations/c1/envelope-switches",
        { omitted: ["runtime_date"] },
      );
    });
  });

  it("有这场时开关下写这一轮不换", async () => {
    render(
      <EnvelopeSwitchList
        scope="conversation"
        conversationId="c1"
        showBinding={false}
        showTurnHint
      />,
    );
    expect(
      await screen.findByText("正在生成的这一轮不换，从下一次进入回合生效。"),
    ).toBeTruthy();
    expect(screen.queryByText(/还没有这场/)).toBeNull();
  });

  it("都开时全部关闭把当前行写入拒绝表", async () => {
    render(<EnvelopeSwitchList scope="draft" showBulk />);
    await waitFor(() => {
      expect(screen.getByRole("switch", { name: "文件索引" })).toBeTruthy();
      expect(screen.queryByRole("switch", { name: "执行" })).toBeNull();
    });
    expect(screen.getByRole("button", { name: "全部关闭" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "全部打开" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "全部关闭" }));
    await waitFor(() => {
      expect(put).toHaveBeenCalledWith("/v1/users/me/envelope-switches", {
        omitted: ["runtime_date", "file_index"],
      });
    });
  });

  it("有开有关时两颗都在，打开清空拒绝表", async () => {
    get.mockResolvedValue(board(["runtime_date"]));
    render(
      <EnvelopeSwitchList scope="conversation" conversationId="c1" showBulk />,
    );
    expect(
      await screen.findByRole("button", { name: "全部打开" }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "全部关闭" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "全部打开" }));
    await waitFor(() => {
      expect(put).toHaveBeenCalledWith(
        "/v1/conversations/c1/envelope-switches",
        { omitted: [] },
      );
    });
  });

  it("都关时只出全部打开", async () => {
    get.mockResolvedValue(board(["runtime_date", "file_index"]));
    render(<EnvelopeSwitchList scope="draft" showBulk />);
    expect(
      await screen.findByRole("button", { name: "全部打开" }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "全部关闭" })).toBeNull();
  });

  it("不传 showBulk 时名单顶上没有总按钮", async () => {
    render(<EnvelopeSwitchList scope="draft" />);
    await screen.findByRole("switch", { name: "日期" });
    expect(screen.queryByRole("button", { name: "全部关闭" })).toBeNull();
    expect(screen.queryByRole("button", { name: "全部打开" })).toBeNull();
  });
});
