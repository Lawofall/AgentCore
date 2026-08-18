// @vitest-environment jsdom
/**
 * Write affordances on the cloud file browser: the per-row 「⋯」 menu (重命名 / 移动 / 删除)
 * and 新建文件夹 — and their absence when no ops are injected, which is how a local
 * workspace stays honestly read-only instead of showing buttons the server would 409.
 */
import { FileBrowser } from "@/components/FileBrowser";
import type { FileBrowserOps } from "@/components/fileBrowser/ops";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({ getTokens: () => ({ access: "t" }) }));
vi.mock("@/components/Modal", () => ({
  Modal: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

afterEach(cleanup);

const entries = [
  { path: "docs", is_dir: true },
  { path: "docs/a.md", is_dir: false },
  { path: "archive", is_dir: true },
  { path: "note.txt", is_dir: false },
];

function makeOps(over: Partial<FileBrowserOps> = {}): FileBrowserOps {
  return {
    move: vi.fn().mockResolvedValue(undefined),
    remove: vi.fn().mockResolvedValue(undefined),
    createDir: vi.fn().mockResolvedValue(undefined),
    readForEdit: vi.fn(),
    writeText: vi.fn(),
    ...over,
  };
}

function renderBrowser(ops?: FileBrowserOps, cwd = "") {
  const list = vi.fn().mockResolvedValue({ entries, truncated: false });
  render(
    <MemoryRouter>
      <FileBrowser
        source={{ list, download: vi.fn() }}
        cwd={cwd}
        onCwdChange={() => {}}
        ops={ops}
      />
    </MemoryRouter>,
  );
  return { list };
}

describe("FileBrowser · read-only without ops", () => {
  it("shows no row menu and no 新建文件夹", async () => {
    renderBrowser(undefined);
    await screen.findByText("note.txt");
    expect(screen.queryByLabelText("note.txt 的更多操作")).toBeNull();
    expect(screen.queryByLabelText("新建文件夹")).toBeNull();
  });
});

describe("FileBrowser · rename", () => {
  it("moves the entry to the new name in the same folder and re-lists", async () => {
    const ops = makeOps();
    const { list } = renderBrowser(ops);
    await screen.findByText("note.txt");

    fireEvent.click(screen.getByLabelText("note.txt 的更多操作"));
    fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    fireEvent.change(screen.getByLabelText("新名称"), {
      target: { value: "笔记.txt" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(ops.move).toHaveBeenCalledWith("note.txt", "笔记.txt");
      expect(screen.getByText("已重命名为「笔记.txt」")).toBeTruthy();
      // Re-list lands after the toast; asserting outside waitFor raced under load.
      expect(list).toHaveBeenCalledTimes(2);
    });
  });

  it("keeps the backend's refusal visible instead of pretending it worked", async () => {
    const ops = makeOps({
      move: vi.fn().mockRejectedValue(new Error("已存在同名文件")),
    });
    renderBrowser(ops);
    await screen.findByText("note.txt");

    fireEvent.click(screen.getByLabelText("note.txt 的更多操作"));
    fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    fireEvent.change(screen.getByLabelText("新名称"), {
      target: { value: "a.md" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(screen.getByText("已存在同名文件")).toBeTruthy();
    });
  });

  it("refuses a name that would change the path's shape", async () => {
    renderBrowser(makeOps());
    await screen.findByText("note.txt");

    fireEvent.click(screen.getByLabelText("note.txt 的更多操作"));
    fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    fireEvent.change(screen.getByLabelText("新名称"), {
      target: { value: "sub/x.txt" },
    });

    expect(screen.getByText(/不能包含/)).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "保存" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });
});

describe("FileBrowser · move", () => {
  it("walks folders and moves into the picked one", async () => {
    const ops = makeOps();
    renderBrowser(ops);
    await screen.findByText("note.txt");

    fireEvent.click(screen.getByLabelText("note.txt 的更多操作"));
    fireEvent.click(screen.getByRole("button", { name: "移动到…" }));
    // Root is where it already lives, so confirming is blocked until we descend.
    expect(screen.getByText("已经在这个文件夹里了")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "进入文件夹 archive" }));
    fireEvent.click(screen.getByRole("button", { name: "移动到「archive」" }));

    await waitFor(() => {
      expect(ops.move).toHaveBeenCalledWith("note.txt", "archive/note.txt");
      expect(screen.getByText("已移动到「archive」")).toBeTruthy();
    });
  });

  it("never offers a folder its own subtree as a destination", async () => {
    renderBrowser(makeOps());
    await screen.findByText("docs");

    fireEvent.click(screen.getByLabelText("docs 的更多操作"));
    fireEvent.click(screen.getByRole("button", { name: "移动到…" }));

    // Its current parent is refused, and it is not listed as a folder to descend into,
    // so there is no route to "inside itself" at all.
    expect(screen.getByText("已经在这个文件夹里了")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "进入文件夹 docs" }),
    ).toBeNull();
    expect(
      (
        screen.getByRole("button", {
          name: "移动到「根目录」",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });
});

describe("FileBrowser · delete", () => {
  it("confirms, soft-deletes, and points at the 软删区 for recovery", async () => {
    const ops = makeOps();
    renderBrowser(ops);
    await screen.findByText("note.txt");

    fireEvent.click(screen.getByLabelText("note.txt 的更多操作"));
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(screen.getByText(/软删区/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    await waitFor(() => {
      expect(ops.remove).toHaveBeenCalledWith("note.txt");
      expect(screen.getByText(/已删除「note.txt」/)).toBeTruthy();
    });
  });
});

describe("FileBrowser · new folder", () => {
  it("creates under the current directory", async () => {
    const ops = makeOps();
    renderBrowser(ops, "docs");
    await screen.findByText("a.md");

    fireEvent.click(screen.getByLabelText("新建文件夹"));
    fireEvent.change(screen.getByLabelText("文件夹名称"), {
      target: { value: "草稿" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));

    await waitFor(() => {
      expect(ops.createDir).toHaveBeenCalledWith("docs/草稿");
      expect(screen.getByText("已新建文件夹「草稿」")).toBeTruthy();
    });
  });
});

describe("FileBrowser · list row meta", () => {
  it("shows mtime only, never size, on a file row", async () => {
    const list = vi.fn().mockResolvedValue({
      entries: [
        {
          path: "note.txt",
          is_dir: false,
          size_bytes: 12000,
          mtime_ms: Date.now() - 1000,
        },
      ],
      truncated: false,
    });
    render(
      <MemoryRouter>
        <FileBrowser
          source={{ list, download: vi.fn() }}
          cwd=""
          onCwdChange={() => {}}
        />
      </MemoryRouter>,
    );
    expect(await screen.findByText("刚刚")).toBeTruthy();
    expect(screen.queryByText("12 KB")).toBeNull();
  });

  it("does not occupy the subtitle when mtime is missing", async () => {
    const list = vi.fn().mockResolvedValue({
      entries: [
        {
          path: "note.txt",
          is_dir: false,
          size_bytes: 12000,
          mtime_ms: null,
        },
      ],
      truncated: false,
    });
    render(
      <MemoryRouter>
        <FileBrowser
          source={{ list, download: vi.fn() }}
          cwd=""
          onCwdChange={() => {}}
        />
      </MemoryRouter>,
    );
    expect(await screen.findByText("note.txt")).toBeTruthy();
    expect(screen.queryByText("刚刚")).toBeNull();
    expect(screen.queryByText("12 KB")).toBeNull();
  });
});
