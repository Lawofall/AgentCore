/**
 * 服务端回执里的别名必须成为 StoredRoot 上的那一份，sidecar 快照才寻址得到。
 * @vitest-environment node
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("electron", async () => {
  const { mkdtempSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const dir = mkdtempSync(join(tmpdir(), "agentcore-alias-"));
  return { app: { getPath: () => dir } };
});

import type { StoredRoot } from "../fs/roots";
import { listSessionRoots, __test as rootsTest } from "../fs/roots";
import { adoptSessionRootAlias } from "../fs/sessionAlias";
import { buildExternalMounts } from "../sidecar/externalMounts";

/** 中文目录名：桌面自算成 `d_`，服务端折成 base32 摘要 —— 两边永不相等。 */
const chineseRoot: StoredRoot = {
  id: "r1",
  name: "报告",
  absPath: "C:\\Users\\me\\报告",
  sessionOnly: true,
  conversationId: "c1",
  mode: "readonly",
  alias: "d_",
};

function seed(...rows: StoredRoot[]): void {
  rootsTest.reset(new Map(rows.map((r) => [r.id, r])));
}

describe("adoptSessionRootAlias", () => {
  beforeEach(() => {
    rootsTest.reset();
  });

  it("服务端回执的别名覆盖桌面自算的那份", async () => {
    seed(chineseRoot);

    await expect(
      adoptSessionRootAlias("c1", "r1", "ext_mfrggzdf"),
    ).resolves.toBe(true);

    expect(rootsTest.getMap().get("r1")?.alias).toBe("ext_mfrggzdf");
  });

  it("写回后 sidecar 快照拿到的是权威别名", async () => {
    seed(chineseRoot);
    expect(buildExternalMounts(listSessionRoots("c1"))[0]?.alias).toBe("d_");

    await adoptSessionRootAlias("c1", "r1", "ext_mfrggzdf");

    expect(buildExternalMounts(listSessionRoots("c1"))).toEqual([
      {
        alias: "ext_mfrggzdf",
        rootId: "r1",
        label: "报告",
        absPath: "C:\\Users\\me\\报告",
        mode: "readonly",
        workingRoot: false,
      },
    ]);
  });

  it("服务端去重后的别名（_2 后缀）同样写回", async () => {
    seed(chineseRoot, {
      ...chineseRoot,
      id: "r2",
      name: "报告",
      absPath: "D:\\backup\\报告",
      alias: "d__2",
    });

    await adoptSessionRootAlias("c1", "r2", "ext_mfrggzdf_2");

    const aliases = buildExternalMounts(listSessionRoots("c1"))
      .map((m) => m.alias)
      .sort();
    expect(aliases).toEqual(["d_", "ext_mfrggzdf_2"]);
  });

  it("重放同一回执不改动已一致的别名", async () => {
    seed({ ...chineseRoot, alias: "ext_mfrggzdf" });

    await expect(
      adoptSessionRootAlias("c1", "r1", "ext_mfrggzdf"),
    ).resolves.toBe(true);

    expect(rootsTest.getMap().get("r1")?.alias).toBe("ext_mfrggzdf");
  });

  it("持久化 payload 带上权威别名（重启后不回退）", async () => {
    seed(chineseRoot);

    await adoptSessionRootAlias("c1", "r1", "ext_mfrggzdf");

    expect(rootsTest.buildSessionFilePayload().c1[0]?.alias).toBe(
      "ext_mfrggzdf",
    );
  });

  it("别的对话 / 未知根 / 永久根 / 空别名一律拒绝", async () => {
    seed(chineseRoot, { id: "perm", name: "proj", absPath: "C:\\proj" });

    await expect(adoptSessionRootAlias("c-other", "r1", "x")).resolves.toBe(
      false,
    );
    await expect(adoptSessionRootAlias("c1", "missing", "x")).resolves.toBe(
      false,
    );
    await expect(adoptSessionRootAlias("c1", "perm", "x")).resolves.toBe(false);
    await expect(adoptSessionRootAlias("c1", "r1", "  ")).resolves.toBe(false);

    expect(rootsTest.getMap().get("r1")?.alias).toBe("d_");
    expect(rootsTest.getMap().get("perm")?.alias).toBeUndefined();
  });
});
