// @vitest-environment jsdom
/**
 * 找人页：详情顶栏同构；无搜索且列表空时给 hint，不要白板。
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  getTokens: () => ({ access_token: "a", refresh_token: "r" }),
  BASE_URL: "",
}));

const { listBlocks, searchUsers, startDm, unblockUser } = vi.hoisted(() => ({
  listBlocks: vi.fn(),
  searchUsers: vi.fn(),
  startDm: vi.fn(),
  unblockUser: vi.fn(),
}));
vi.mock("@/api/messaging", async () => {
  const actual =
    await vi.importActual<typeof import("@/api/messaging")>("@/api/messaging");
  return { ...actual, listBlocks, searchUsers, startDm, unblockUser };
});

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return { ...actual, useNavigate: () => navigate };
});

import { NewDmPage } from "@/pages/im/NewDmPage";

beforeEach(() => {
  vi.clearAllMocks();
  listBlocks.mockResolvedValue([]);
});

afterEach(cleanup);

describe("NewDmPage", () => {
  it("uses icon-btn back + bar-title", async () => {
    render(<NewDmPage />);
    expect(screen.getByLabelText("返回").className).toMatch(/icon-btn/);
    expect(document.querySelector(".bar-title")?.textContent).toBe("找人");
    expect(screen.queryByText("← 返回")).toBeNull();
    expect(
      await screen.findByText("输入用户名或 ID 精确搜索，即可发起对话。"),
    ).toBeTruthy();
  });

  it("shows a hint when there is no query and the list is empty", async () => {
    render(<NewDmPage />);
    expect(
      await screen.findByText("输入用户名或 ID 精确搜索，即可发起对话。"),
    ).toBeTruthy();
  });

  it("shows 黑名单 instead of the empty hint when blocks exist", async () => {
    listBlocks.mockResolvedValue([
      { id: "u1", username: "bob", display_name: "Bob" },
    ]);
    render(<NewDmPage />);
    expect(await screen.findByText("黑名单")).toBeTruthy();
    expect(screen.getByText("Bob")).toBeTruthy();
    expect(
      screen.queryByText("输入用户名或 ID 精确搜索，即可发起对话。"),
    ).toBeNull();
  });

  it("renders block avatars from DTO avatar_url", async () => {
    listBlocks.mockResolvedValue([
      {
        id: "u1",
        username: "bob",
        display_name: "Bob",
        avatar_url: "/avatars/bob.png",
      },
      { id: "u2", username: "cara", display_name: "Cara" },
    ]);
    render(<NewDmPage />);
    await screen.findByText("Bob");
    const avatars = document.querySelectorAll(".im-search-result .im-avatar");
    expect(avatars[0]?.tagName).toBe("IMG");
    expect(avatars[0]?.getAttribute("src")).toBe("/avatars/bob.png");
    expect(avatars[1]?.tagName).toBe("SPAN");
    expect(avatars[1]?.textContent).toBe("C");
    expect(document.body.innerHTML).not.toContain("/v1/users/");
  });

  it("renders people-search avatars from DTO avatar_url", async () => {
    searchUsers.mockResolvedValue([
      {
        id: "u1",
        username: "alice",
        display_name: "Alice",
        avatar_url: "/avatars/alice.png",
      },
      { id: "u2", username: "zoe", display_name: "Zoe" },
    ]);
    render(<NewDmPage />);
    fireEvent.change(screen.getByPlaceholderText("按用户名或 ID 精确搜索"), {
      target: { value: "alice" },
    });
    await screen.findByText("Alice");
    const avatars = document.querySelectorAll(".im-search-result .im-avatar");
    expect(avatars[0]?.getAttribute("src")).toBe("/avatars/alice.png");
    expect(avatars[1]?.tagName).toBe("SPAN");
    expect(avatars[1]?.textContent).toBe("Z");
    expect(document.body.innerHTML).not.toContain("/v1/users/");
  });
});
