/**
 * 打包契约：`dependencies` 只留必须进 asar node_modules 的原生模块。
 * 纯 JS 放 devDependencies，由 electron-vite 打进 out/（发布与门禁 §1.6）。
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const PACKAGED_NATIVE_DEPS = ["node-pty"] as const;

const packageDir = join(dirname(fileURLToPath(import.meta.url)), "../../..");
const pkg = JSON.parse(
  readFileSync(join(packageDir, "package.json"), "utf8"),
) as { dependencies?: Record<string, string> };

describe("desktop production deps", () => {
  it("only lists native modules that must exist on disk in the packaged app", () => {
    expect(Object.keys(pkg.dependencies ?? {}).sort()).toEqual(
      [...PACKAGED_NATIVE_DEPS].sort(),
    );
  });
});
