import type { FileSource } from "@/lib/fileSource";
import {
  UPLOAD_MAX_BYTES,
  UPLOAD_MAX_FILES,
  captureDropUpload,
  collectPickedFiles,
  describeUploadReport,
  expandDropUpload,
  isIgnoredUploadPath,
  uploadPicked,
} from "@/lib/folderUpload";
import { describe, expect, it, vi } from "vitest";

/** `<input webkitdirectory>` 给出的 FileList：每个 File 带 webkitRelativePath。 */
function pickedList(
  entries: readonly (readonly [string, number?])[],
): FileList {
  const files = entries.map(([relPath, size = 1]) => {
    const file = new File(
      [new Uint8Array(size)],
      relPath.split("/").pop() ?? "",
    );
    Object.defineProperty(file, "webkitRelativePath", { value: relPath });
    return file;
  });
  return Object.assign(files, {
    item: (i: number) => files[i] ?? null,
  }) as unknown as FileList;
}

type Tree = { [name: string]: Tree | number };

/** 拖入的目录 entry 树（`FileSystemDirectoryEntry` 的最小可用替身）。 */
function dirEntry(name: string, tree: Tree): FileSystemEntry {
  const children = Object.entries(tree).map(([child, value]) =>
    typeof value === "number"
      ? fileEntry(child, value)
      : dirEntry(child, value),
  );
  return {
    name,
    isDirectory: true,
    isFile: false,
    createReader: () => {
      let served = false;
      return {
        // 真实实现分批给，读到空批为止。
        readEntries: (ok: (batch: FileSystemEntry[]) => void) => {
          ok(served ? [] : children);
          served = true;
        },
      };
    },
  } as unknown as FileSystemEntry;
}

function fileEntry(name: string, size: number): FileSystemEntry {
  return {
    name,
    isDirectory: false,
    isFile: true,
    file: (ok: (f: File) => void) => ok(new File([new Uint8Array(size)], name)),
  } as unknown as FileSystemEntry;
}

function dataTransfer(entries: FileSystemEntry[]): DataTransfer {
  return {
    items: entries.map((entry) => ({
      kind: "file",
      webkitGetAsEntry: () => entry,
      getAsFile: () => null,
    })),
    files: [],
  } as unknown as DataTransfer;
}

/** 记录每次写入的假源；`failOn` 里的路径写入抛错。 */
function sink(failOn: Record<string, string> = {}) {
  const written: string[] = [];
  const dirs: string[] = [];
  const source: Pick<FileSource, "mkdir" | "writeBytes"> = {
    mkdir: async (path) => {
      dirs.push(path);
    },
    writeBytes: async (path) => {
      const reason = failOn[path];
      if (reason) throw new Error(reason);
      written.push(path);
    },
  };
  return { source, written, dirs };
}

describe("整夹上传的采集与过滤", () => {
  it("保留目录层级，并把中间目录一并列出（先建父再建子）", () => {
    const picked = collectPickedFiles(
      pickedList([["设计/图标/线性/a.svg"], ["设计/readme.md"]]),
    );
    expect(picked.files.map((f) => f.relPath)).toEqual([
      "设计/图标/线性/a.svg",
      "设计/readme.md",
    ]);
    expect(picked.dirs).toEqual(["设计", "设计/图标", "设计/图标/线性"]);
  });

  it("噪音目录与系统噪音后缀被跳过，而且**说出来**跳了哪些", () => {
    const picked = collectPickedFiles(
      pickedList([
        ["p/src/a.ts"],
        ["p/node_modules/lib/b.js"],
        ["p/.git/config"],
        ["p/index.db"],
      ]),
    );
    expect(picked.files.map((f) => f.relPath)).toEqual(["p/src/a.ts"]);
    expect(picked.ignored).toEqual([
      "p/node_modules/lib/b.js",
      "p/.git/config",
      "p/index.db",
    ]);
  });

  it("用户自己的图片 / 压缩包照传——AI 噪音后缀是 AI 视角的规则", () => {
    expect(isIgnoredUploadPath("设计/封面.png")).toBe(false);
    expect(isIgnoredUploadPath("备份/x.zip")).toBe(false);
    expect(isIgnoredUploadPath("build/out.js")).toBe(true);
  });

  it("穿越路径直接丢弃，不让它落到目标目录外", () => {
    expect(collectPickedFiles(pickedList([["../etc/passwd"]])).files).toEqual(
      [],
    );
  });

  it("拖入的目录 entry 递归展开，空目录也留下来", async () => {
    const picked = await expandDropUpload(
      captureDropUpload(
        dataTransfer([
          dirEntry("设计", {
            "a.svg": 4,
            图标: { "b.svg": 4 },
            node_modules: { "c.js": 4 },
            空的: {},
          }),
        ]),
      ),
    );
    expect(picked.files.map((f) => f.relPath).sort()).toEqual([
      "设计/a.svg",
      "设计/图标/b.svg",
    ]);
    expect(picked.dirs).toContain("设计/空的");
    expect(picked.ignored).toEqual(["设计/node_modules"]);
  });

  it("拿不到 entry 的裸文件同样受文件数上限约束，并如实标 truncated", async () => {
    // 只读 name，不必真造 File；两万多个真 Blob 纯属浪费。
    const looseFiles = Array.from(
      { length: UPLOAD_MAX_FILES + 2 },
      (_, i) => ({ name: `f${i}.txt` }) as unknown as File,
    );

    const picked = await expandDropUpload({ entries: [], looseFiles });

    expect(picked.files).toHaveLength(UPLOAD_MAX_FILES);
    expect(picked.files.at(-1)?.relPath).toBe(`f${UPLOAD_MAX_FILES - 1}.txt`);
    expect(picked.truncated).toBe(true);
  });

  it("裸文件里的忽略项照记，不占上限额度", async () => {
    const picked = await expandDropUpload({
      entries: [],
      looseFiles: [
        new File(["1"], "a.md"),
        new File(["2"], "index.db"),
        new File(["3"], "b.md"),
      ],
    });

    expect(picked.files.map((f) => f.relPath)).toEqual(["a.md", "b.md"]);
    expect(picked.ignored).toEqual(["index.db"]);
    expect(picked.truncated).toBe(false);
  });
});

describe("逐项诚实报告（不许一个 toast 吞掉整批）", () => {
  it("单项失败不中断整批，失败项连名字带原因留在报告里", async () => {
    const { source, written } = sink({ "dst/b.md": "目标被占用" });
    const report = await uploadPicked(
      {
        files: [
          { relPath: "a.md", file: new File(["1"], "a.md") },
          { relPath: "b.md", file: new File(["2"], "b.md") },
          { relPath: "c.md", file: new File(["3"], "c.md") },
        ],
        dirs: [],
        ignored: [],
        truncated: false,
      },
      "dst",
      source,
    );
    expect(written).toEqual(["dst/a.md", "dst/c.md"]);
    expect(report.uploaded).toBe(2);
    expect(report.failures).toEqual([{ path: "b.md", reason: "目标被占用" }]);
  });

  it("超过单文件硬顶的项不发请求，单独报出来", async () => {
    const { source, written } = sink();
    const big = new File([""], "big.zip");
    Object.defineProperty(big, "size", { value: UPLOAD_MAX_BYTES + 1 });
    const report = await uploadPicked(
      {
        files: [
          { relPath: "big.zip", file: big },
          { relPath: "ok.md", file: new File(["1"], "ok.md") },
        ],
        dirs: [],
        ignored: [],
        truncated: false,
      },
      "",
      source,
    );
    expect(written).toEqual(["ok.md"]);
    expect(report.failures[0].path).toBe("big.zip");
    expect(report.failures[0].reason).toContain("50MB");
  });

  it("目录先建好，空目录因此不会消失", async () => {
    const { source, dirs } = sink();
    await uploadPicked(
      { files: [], dirs: ["设计", "设计/空的"], ignored: [], truncated: false },
      "dst",
      source,
    );
    expect(dirs).toEqual(["dst/设计", "dst/设计/空的"]);
  });

  it("只要有失败 / 跳过 / 截断，概述就必须挂「查看详情」", () => {
    expect(
      describeUploadReport({
        destDir: "",
        uploaded: 3,
        ignored: ["node_modules/a.js"],
        failures: [],
        truncated: false,
      }),
    ).toMatchObject({ message: "已上传 3 个文件", hasDetail: true });

    expect(
      describeUploadReport({
        destDir: "",
        uploaded: 3,
        ignored: [],
        failures: [],
        truncated: false,
      }),
    ).toMatchObject({ hasDetail: false, description: undefined });

    expect(
      describeUploadReport({
        destDir: "",
        uploaded: 0,
        ignored: [],
        failures: [{ path: "a.md", reason: "x" }],
        truncated: false,
      }).message,
    ).toBe("没有文件上传成功");
  });

  it("源不支持写入时逐项如实说明，而不是静默丢弃", async () => {
    const report = await uploadPicked(
      {
        files: [{ relPath: "a.md", file: new File(["1"], "a.md") }],
        dirs: [],
        ignored: [],
        truncated: false,
      },
      "",
      { mkdir: vi.fn(), writeBytes: undefined },
    );
    expect(report.uploaded).toBe(0);
    expect(report.failures).toEqual([
      { path: "a.md", reason: "此工作区不支持上传" },
    ]);
  });
});
