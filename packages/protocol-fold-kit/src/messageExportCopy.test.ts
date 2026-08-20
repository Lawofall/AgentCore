import { describe, expect, it } from "vitest";
import {
  MESSAGE_EXPORT_DELIVERABLE_HEADING,
  MESSAGE_EXPORT_PROCESS_HEADING,
  MESSAGE_EXPORT_REASONING_HEADING,
  MESSAGE_EXPORT_STEP_CHROME,
  MESSAGE_EXPORT_TOOL_STATUS_SUFFIX,
} from "./messageExportCopy";

describe("message export chrome", () => {
  it("pins section headings and process-row chrome", () => {
    expect(MESSAGE_EXPORT_REASONING_HEADING).toBe("【思考】");
    expect(MESSAGE_EXPORT_PROCESS_HEADING).toBe("【过程】");
    expect(MESSAGE_EXPORT_DELIVERABLE_HEADING).toBe("【交付】");
    expect(MESSAGE_EXPORT_STEP_CHROME.team).toBe("· （团队协作）");
    expect(MESSAGE_EXPORT_STEP_CHROME.checkpoint).toBe("· （向你确认）");
    expect(MESSAGE_EXPORT_STEP_CHROME.ask).toBe("· （提问）");
    expect(MESSAGE_EXPORT_STEP_CHROME.plan_review).toBe("· （计划复核）");
    expect(MESSAGE_EXPORT_STEP_CHROME.team_preview).toBe("· （团队预览）");
    expect(MESSAGE_EXPORT_TOOL_STATUS_SUFFIX.error).toBe("（失败）");
    expect(MESSAGE_EXPORT_TOOL_STATUS_SUFFIX.running).toBe("（进行中）");
  });
});
