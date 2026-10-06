import {
  dedupeName,
  isUntitledPromptFolderName,
  uniqueNumberedName,
  uniqueUntitledFolder,
} from "@/components/files/dedupeName";
import { describe, expect, it } from "vitest";

describe("dedupeName（复制-粘贴的去重命名）", () => {
  it("名字不冲突时原样返回", () => {
    expect(dedupeName("a.txt", new Set(["b.txt"]))).toBe("a.txt");
    expect(dedupeName("docs", new Set())).toBe("docs");
  });

  it("冲突时追加「 副本」并保留扩展名", () => {
    expect(dedupeName("a.txt", new Set(["a.txt"]))).toBe("a 副本.txt");
  });

  it("无扩展名的文件/目录在末尾追加「 副本」", () => {
    expect(dedupeName("report", new Set(["report"]))).toBe("report 副本");
  });

  it("「 副本」也已存在时递增编号", () => {
    const existing = new Set(["a.txt", "a 副本.txt", "a 副本 2.txt"]);
    expect(dedupeName("a.txt", existing)).toBe("a 副本 3.txt");
  });

  it("前导点文件按整体处理（不把 .env 当扩展名）", () => {
    expect(dedupeName(".env", new Set([".env"]))).toBe(".env 副本");
  });

  it("多扩展名只在最后一个点前插入「 副本」", () => {
    expect(dedupeName("a.tar.gz", new Set(["a.tar.gz"]))).toBe("a.tar 副本.gz");
  });
});

describe("uniqueUntitledFolder（新建目录的默认名）", () => {
  it("空目录用「未命名文件夹」", () => {
    expect(uniqueUntitledFolder([])).toBe("未命名文件夹");
  });

  it("已有未命名时从 (2) 递增（对齐服务端 unique_sibling_name）", () => {
    expect(uniqueUntitledFolder(["未命名文件夹"])).toBe("未命名文件夹 (2)");
    expect(uniqueUntitledFolder(["未命名文件夹", "未命名文件夹 (2)"])).toBe(
      "未命名文件夹 (3)",
    );
  });

  it("大小写视为同名", () => {
    expect(uniqueNumberedName("Docs", ["docs"])).toBe("Docs (2)");
  });
});

describe("isUntitledPromptFolderName", () => {
  it("认生成出来的占位名", () => {
    expect(isUntitledPromptFolderName("未命名夹")).toBe(true);
    expect(isUntitledPromptFolderName("未命名夹 (2)")).toBe(true);
    expect(isUntitledPromptFolderName("未命名夹 (10)")).toBe(true);
  });

  it("起过的名字和 (1) 不算占位", () => {
    expect(isUntitledPromptFolderName("法律")).toBe(false);
    expect(isUntitledPromptFolderName("未命名夹 (1)")).toBe(false);
    expect(isUntitledPromptFolderName("未命名夹 2")).toBe(false);
    expect(isUntitledPromptFolderName("未命名文件夹")).toBe(false);
  });
});
