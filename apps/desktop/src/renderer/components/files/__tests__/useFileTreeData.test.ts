// @vitest-environment jsdom

import { useFileTreeData } from "@/components/files/useFileTreeData";
import type { FileNode, FileSource } from "@/lib/fileSource";
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

function file(name: string): FileNode {
  return { path: name, name, isDir: false };
}

function dir(name: string): FileNode {
  return { path: name, name, isDir: true };
}

function stubSource(
  id: string,
  listDir: FileSource["listDir"],
  extra: Partial<FileSource> = {},
): FileSource {
  return {
    id,
    label: id,
    caps: { watch: false, transfer: false, edit: false, snapshots: false },
    listDir,
    read: async () => ({ kind: "text", text: "", truncated: false }),
    createFile: async () => {},
    mkdir: async () => {},
    move: async () => {},
    delete: async () => {},
    ...extra,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

describe("useFileTreeData silent patch", () => {
  it("reload 把已 ready 的根标成 loading；reloadSilent 不标、也不清空 children", async () => {
    const source = stubSource("workspace:silent-load", async () => [
      file("a.md"),
    ]);
    const { result } = renderHook(() => useFileTreeData(source));
    await waitFor(() => expect(result.current.statusOf("")).toBe("ready"));
    expect(result.current.childrenOf("")?.map((n) => n.name)).toEqual(["a.md"]);

    const hang = deferred<FileNode[]>();
    source.listDir = async () => hang.promise;

    act(() => {
      void result.current.reloadSilent("");
    });
    expect(result.current.statusOf("")).toBe("ready");
    expect(result.current.childrenOf("")?.map((n) => n.name)).toEqual(["a.md"]);

    await act(async () => {
      hang.resolve([file("b.md")]);
    });
    await waitFor(() =>
      expect(result.current.childrenOf("")?.map((n) => n.name)).toEqual([
        "b.md",
      ]),
    );
    expect(result.current.statusOf("")).toBe("ready");

    const hangReload = deferred<FileNode[]>();
    source.listDir = async () => hangReload.promise;
    act(() => {
      result.current.reload("");
    });
    expect(result.current.statusOf("")).toBe("loading");
    expect(result.current.childrenOf("")?.map((n) => n.name)).toEqual(["b.md"]);

    await act(async () => {
      hangReload.resolve([file("c.md")]);
    });
    await waitFor(() => expect(result.current.statusOf("")).toBe("ready"));
  });

  it("换 source 对象但 id 不变不重置；换 id 才清空再拉", async () => {
    const listing = { current: [file("keep.md")] };
    const listed: string[] = [];
    const make = (id: string): FileSource =>
      stubSource(id, async (dir) => {
        listed.push(`${id}:${dir}`);
        return listing.current;
      });

    const { result, rerender } = renderHook(({ src }) => useFileTreeData(src), {
      initialProps: { src: make("workspace:same") },
    });
    await waitFor(() => expect(result.current.statusOf("")).toBe("ready"));
    expect(result.current.childrenOf("")?.[0]?.name).toBe("keep.md");
    const afterFirst = listed.length;

    listing.current = [file("should-not-appear.md")];
    rerender({ src: make("workspace:same") });
    expect(result.current.statusOf("")).toBe("ready");
    expect(result.current.childrenOf("")?.[0]?.name).toBe("keep.md");
    expect(listed.length).toBe(afterFirst);

    rerender({ src: make("workspace:other") });
    await waitFor(() =>
      expect(result.current.childrenOf("")?.[0]?.name).toBe(
        "should-not-appear.md",
      ),
    );
    expect(listed.some((c) => c.startsWith("workspace:other:"))).toBe(true);
  });

  it("切 source.id 后较慢的 listDir 不得写进已 reset 的树", async () => {
    const hangA = deferred<FileNode[]>();
    const hangB = deferred<FileNode[]>();
    const { result, rerender } = renderHook(({ src }) => useFileTreeData(src), {
      initialProps: {
        src: stubSource("workspace:a", async () => hangA.promise),
      },
    });

    rerender({ src: stubSource("workspace:b", async () => hangB.promise) });
    expect(result.current.childrenOf("")).toBeUndefined();

    await act(async () => {
      hangA.resolve([file("from-a.md")]);
    });
    expect(result.current.childrenOf("")?.map((n) => n.name)).not.toEqual([
      "from-a.md",
    ]);

    await act(async () => {
      hangB.resolve([file("from-b.md")]);
    });
    await waitFor(() =>
      expect(result.current.childrenOf("")?.map((n) => n.name)).toEqual([
        "from-b.md",
      ]),
    );
  });

  it("切 source.id 后较慢的 listTree 不得写进已 reset 的树", async () => {
    const hangA = deferred<FileNode[]>();
    const hangB = deferred<FileNode[]>();
    const { result, rerender } = renderHook(({ src }) => useFileTreeData(src), {
      initialProps: {
        src: stubSource("eager:a", async () => [], {
          listTree: async () => hangA.promise,
        }),
      },
    });

    rerender({
      src: stubSource("eager:b", async () => [], {
        listTree: async () => hangB.promise,
      }),
    });

    await act(async () => {
      hangA.resolve([file("from-a.md")]);
    });
    expect(result.current.childrenOf("")?.map((n) => n.name)).not.toEqual([
      "from-a.md",
    ]);

    await act(async () => {
      hangB.resolve([file("from-b.md")]);
    });
    await waitFor(() =>
      expect(result.current.childrenOf("")?.map((n) => n.name)).toEqual([
        "from-b.md",
      ]),
    );
  });

  it("silent 不拉从未展开的层", async () => {
    const listed: string[] = [];
    const source = stubSource("workspace:skip-unexpanded", async (folder) => {
      listed.push(folder);
      if (folder === "") return [dir("docs")];
      if (folder === "docs") return [file("docs/a.md")];
      return [];
    });
    const { result } = renderHook(() => useFileTreeData(source));
    await waitFor(() => expect(result.current.statusOf("")).toBe("ready"));
    expect(listed).toEqual([""]);

    await act(async () => {
      await result.current.reloadSilent("docs");
    });
    expect(listed).toEqual([""]);
    expect(result.current.childrenOf("docs")).toBeUndefined();
  });
});
