import { describe, expect, it } from "vitest";
import {
  AGENTCORE_ROOT,
  INDEX_REL,
  INTERNAL_ZONE_NAMES,
  LIST_FILES_SKIP_DIRS,
  VERSIONS_REL,
  isAttachmentPath,
  isInternalZoneRelPath,
  shouldSkipAiListEntry,
  shouldSkipAiNoiseFileName,
  shouldSkipDirName,
  shouldSkipFileName,
  shouldSkipSystemFileName,
  shouldSkipSystemWorkspaceEntry,
  shouldSkipWorkspaceEntry,
} from "../fs/workspaceIgnore";

describe("workspaceIgnore", () => {
  it("skips system and dependency directories (aligned with server IGNORED_DIRS)", () => {
    expect(shouldSkipDirName(".agentcore")).toBe(false);
    expect(shouldSkipDirName(".git")).toBe(true);
    expect(shouldSkipDirName("node_modules")).toBe(true);
    expect(shouldSkipDirName("vendor")).toBe(true);
    expect(shouldSkipDirName("logs")).toBe(true);
    expect(shouldSkipDirName("tmp")).toBe(true);
    expect(shouldSkipDirName("temp")).toBe(true);
    expect(shouldSkipDirName(".tmp")).toBe(true);
    expect(shouldSkipDirName("htmlcov")).toBe(true);
    expect(shouldSkipDirName(".mypy_cache")).toBe(true);
    expect(shouldSkipDirName(".pytest_cache")).toBe(true);
    expect(shouldSkipDirName(".pytest_tmp")).toBe(true);
    expect(shouldSkipDirName(".turbo")).toBe(true);
    expect(shouldSkipDirName("coverage")).toBe(true);
    expect(shouldSkipDirName(".idea")).toBe(true);
    expect(shouldSkipDirName(".vscode")).toBe(true);
    expect(shouldSkipDirName("src")).toBe(false);
    expect(shouldSkipDirName("index")).toBe(false);
    expect(LIST_FILES_SKIP_DIRS.has(".github")).toBe(false);
    expect(LIST_FILES_SKIP_DIRS.has("index")).toBe(false);
  });

  it("path-aware internal zones under AgentCore only", () => {
    expect(isInternalZoneRelPath(INDEX_REL)).toBe(true);
    expect(isInternalZoneRelPath(`${AGENTCORE_ROOT}/trash/x`)).toBe(true);
    expect(isInternalZoneRelPath(`${AGENTCORE_ROOT}/baselines/m.zip`)).toBe(
      true,
    );
    expect(isInternalZoneRelPath(VERSIONS_REL)).toBe(true);
    expect(
      isInternalZoneRelPath(`${AGENTCORE_ROOT}/versions/v1/content.zip`),
    ).toBe(true);
    expect(isInternalZoneRelPath(AGENTCORE_ROOT)).toBe(false);
    expect(isInternalZoneRelPath(`${AGENTCORE_ROOT}/规则/x.md`)).toBe(false);
    expect(shouldSkipDirName("index", AGENTCORE_ROOT)).toBe(true);
    expect(shouldSkipDirName("versions", AGENTCORE_ROOT)).toBe(true);
    expect(shouldSkipDirName("规则", AGENTCORE_ROOT)).toBe(false);
    expect(shouldSkipDirName("index", "")).toBe(false);
    // 裸名 versions（用户项目里的目录）不得误伤
    expect(shouldSkipDirName("versions", "")).toBe(false);
    expect(LIST_FILES_SKIP_DIRS.has("versions")).toBe(false);
  });

  it("internal zone names mirror server stage_dirs.INTERNAL_ZONE_NAMES", () => {
    expect([...INTERNAL_ZONE_NAMES].sort()).toEqual([
      "baselines",
      "index",
      "trash",
      "versions",
    ]);
  });

  it("skips system file suffixes (UI + AI)", () => {
    expect(shouldSkipSystemFileName("code_search.db")).toBe(true);
    expect(shouldSkipSystemFileName("CODE_SEARCH.DB")).toBe(true);
    expect(shouldSkipSystemFileName("foo.pyc")).toBe(true);
    expect(shouldSkipSystemFileName("photo.png")).toBe(false);
    expect(shouldSkipSystemFileName("readme.md")).toBe(false);
  });

  it("AI noise covers media/archives; not system .db", () => {
    expect(shouldSkipAiNoiseFileName("hero.png")).toBe(true);
    expect(shouldSkipAiNoiseFileName("out.zip")).toBe(true);
    expect(shouldSkipAiNoiseFileName("app.log")).toBe(true);
    expect(shouldSkipAiNoiseFileName("data.parquet")).toBe(true);
    expect(shouldSkipAiNoiseFileName("arr.feather")).toBe(true);
    expect(shouldSkipAiNoiseFileName("x.arrow")).toBe(true);
    expect(shouldSkipAiNoiseFileName("w.npy")).toBe(true);
    expect(shouldSkipAiNoiseFileName("t.h5")).toBe(true);
    expect(shouldSkipAiNoiseFileName("t.hdf5")).toBe(true);
    expect(shouldSkipAiNoiseFileName("m.pkl")).toBe(true);
    expect(shouldSkipAiNoiseFileName("m.pickle")).toBe(true);
    expect(shouldSkipAiNoiseFileName("code_search.db")).toBe(false);
    expect(shouldSkipFileName("hero.png")).toBe(true);
    expect(shouldSkipFileName("data.parquet")).toBe(true);
    expect(shouldSkipFileName("app.log")).toBe(true);
    expect(shouldSkipFileName("code_search.db")).toBe(true);
  });

  it("workspace entry helpers respect the two tiers", () => {
    expect(shouldSkipWorkspaceEntry(".git", true)).toBe(true);
    expect(shouldSkipWorkspaceEntry("index", true, AGENTCORE_ROOT)).toBe(true);
    expect(shouldSkipWorkspaceEntry("code_search.db", false)).toBe(true);
    expect(shouldSkipWorkspaceEntry("hero.png", false)).toBe(true);
    expect(shouldSkipWorkspaceEntry("notes.md", false)).toBe(false);

    expect(shouldSkipSystemWorkspaceEntry(".git", true)).toBe(true);
    expect(shouldSkipSystemWorkspaceEntry("index", true, AGENTCORE_ROOT)).toBe(
      true,
    );
    expect(shouldSkipSystemWorkspaceEntry("code_search.db", false)).toBe(true);
    expect(shouldSkipSystemWorkspaceEntry("hero.png", false)).toBe(false);
    expect(shouldSkipSystemWorkspaceEntry("notes.md", false)).toBe(false);
  });

  it("AI list exempts attachments/ zip but not elsewhere (index still hides)", () => {
    expect(isAttachmentPath("attachments/pack.zip")).toBe(true);
    expect(isAttachmentPath("attachments")).toBe(true);
    expect(isAttachmentPath("src/attachments/x.zip")).toBe(false);

    expect(shouldSkipAiListEntry("pack.zip", false, "attachments")).toBe(false);
    expect(shouldSkipAiListEntry("photo.png", false, "attachments")).toBe(
      false,
    );
    expect(shouldSkipAiListEntry("photo.png", false, "src")).toBe(false);
    expect(shouldSkipAiListEntry("out.zip", false, "")).toBe(true);
    expect(shouldSkipAiListEntry("out.zip", false, "src")).toBe(true);
    // System noise never exempt under attachments/
    expect(shouldSkipAiListEntry("x.db", false, "attachments")).toBe(true);
    // Index / grep path still hides attachment zip and images
    expect(shouldSkipWorkspaceEntry("pack.zip", false, "attachments")).toBe(
      true,
    );
    expect(shouldSkipWorkspaceEntry("hero.png", false)).toBe(true);
  });

  it("AI list exempts reveal_paths materials outside attachments/", () => {
    const reveal = new Set(["src/pack.zip"]);
    expect(shouldSkipAiListEntry("pack.zip", false, "src", reveal)).toBe(false);
    expect(shouldSkipAiListEntry("other.zip", false, "src", reveal)).toBe(true);
    expect(shouldSkipAiListEntry("pack.zip", false, "src")).toBe(true);
    expect(shouldSkipAiListEntry("shot.png", false, "src")).toBe(false);
    // System noise never exempt via reveal
    expect(
      shouldSkipAiListEntry("x.db", false, "src", new Set(["src/x.db"])),
    ).toBe(true);
    // attachments/ still exempt without reveal
    expect(
      shouldSkipAiListEntry("pack.zip", false, "attachments", reveal),
    ).toBe(false);
  });

  it("AI list shows archives under external ns / session mount / revealArchives", () => {
    expect(shouldSkipAiListEntry("咨询.sy.zip", false, "external/desk")).toBe(
      false,
    );
    expect(shouldSkipAiListEntry("shot.png", false, "external/desk")).toBe(
      false,
    );
    expect(shouldSkipAiListEntry("out.zip", false, "")).toBe(true);
    expect(
      shouldSkipAiListEntry("out.zip", false, "", undefined, {
        revealArchives: true,
      }),
    ).toBe(false);
    expect(
      shouldSkipAiListEntry("out.zip", false, "", undefined, {
        externalNs: true,
      }),
    ).toBe(false);
    expect(
      shouldSkipAiListEntry("shot.png", false, "", undefined, {
        externalNs: true,
        revealArchives: true,
      }),
    ).toBe(false);
  });
});
