/**
 * Frontmatter + host-name transform for the AgentCore rule mirror.
 * Run: node --test scripts/sync-agentcore-rules.test.mjs
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  applyMode,
  parseCursorMdc,
  renderAgentCoreMarkdown,
  rewriteHostTools,
  transformAll,
} from "./sync-agentcore-rules.mjs";

describe("parseCursorMdc", () => {
  it("reads alwaysApply + description", () => {
    const parsed = parseCursorMdc(
      "---\ndescription: 工作流\nalwaysApply: true\n---\n\n# 标题\n",
    );
    assert.equal(parsed.description, "工作流");
    assert.equal(parsed.alwaysApply, true);
    assert.deepEqual(parsed.globs, []);
    assert.match(parsed.body, /# 标题/);
  });

  it("reads yaml glob lists", () => {
    const parsed = parseCursorMdc(
      '---\ndescription: 色\nglobs:\n  - "apps/desktop/src/renderer/**/*.tsx"\n  - "packages/design-tokens/**"\nalwaysApply: false\n---\nbody\n',
    );
    assert.equal(parsed.alwaysApply, false);
    assert.deepEqual(parsed.globs, [
      "apps/desktop/src/renderer/**/*.tsx",
      "packages/design-tokens/**",
    ]);
  });
});

describe("applyMode", () => {
  it("maps always / paths / on_demand", () => {
    assert.deepEqual(applyMode({ alwaysApply: true, globs: [] }), {
      apply: "always",
      paths: [],
    });
    assert.deepEqual(applyMode({ alwaysApply: false, globs: ["**/*.py"] }), {
      apply: "paths",
      paths: ["**/*.py"],
    });
    assert.deepEqual(applyMode({ alwaysApply: false, globs: [] }), {
      apply: "on_demand",
      paths: [],
    });
  });
});

describe("rewriteHostTools", () => {
  it("swaps Cursor host names and sibling .mdc consult refs", () => {
    const out = rewriteHostTools(
      "用 StrReplace 精确替换；改完用 **Read** 自检。委派跟 Cursor Task 官方说明。细则 → `verify-scope.mdc`。",
    );
    assert.equal(
      out,
      "用 edit 精确替换；改完用 `read` 自检。委派跟 `delegate`。细则 → `verify-scope`。",
    );
  });

  it("does not rewrite 阅读", () => {
    assert.equal(rewriteHostTools("写日志时阅读。"), "写日志时阅读。");
  });
});

describe("renderAgentCoreMarkdown", () => {
  it("emits product frontmatter and a generated comment", () => {
    const md = renderAgentCoreMarkdown("color-tokens", {
      description: "配色",
      alwaysApply: false,
      globs: ["apps/desktop/src/renderer/**/*.tsx", "packages/design-tokens/**"],
      body: "只用语义 token。\n",
    });
    assert.match(md, /^---\napply: paths\n/);
    assert.match(md, /^description: 配色$/m);
    assert.match(
      md,
      /^paths: apps\/desktop\/src\/renderer\/\*\*\/\*\.tsx, packages\/design-tokens\/\*\*$/m,
    );
    assert.match(md, /generated from \.cursor\/rules\/color-tokens\.mdc/);
    assert.match(md, /^只用语义 token。$/m);
  });
});

describe("transformAll", () => {
  it("transforms every tracked .mdc", () => {
    const files = transformAll();
    assert.ok(files["dev-process.md"]);
    assert.match(files["dev-process.md"], /^apply: always$/m);
    assert.match(files["dev-process.md"], /用 edit 精确替换/);
    assert.match(files["dev-process.md"], /用 `read` 自检/);
    assert.equal(
      Object.keys(files).every((name) => name.endsWith(".md")),
      true,
    );
    assert.ok(Object.keys(files).length >= 20);
  });
});
