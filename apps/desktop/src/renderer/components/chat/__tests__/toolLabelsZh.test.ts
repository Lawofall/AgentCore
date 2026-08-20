import { describe, expect, it } from "vitest";
import { TOOL_LABELS_ZH, toolLabelZh } from "../toolLabelsZh";

describe("toolLabelZh", () => {
  it("覆盖审批 / 委派 / 开工卡原先三表并集键", () => {
    expect(toolLabelZh("file_write")).toBe("写入文件");
    expect(toolLabelZh("code_execute")).toBe("执行代码");
    expect(toolLabelZh("git")).toBe("Git 写入");
    expect(toolLabelZh("host")).toBe("本机 Host");
    expect(toolLabelZh("terminal")).toBe("终端");
    expect(toolLabelZh("desktop_notify")).toBe("系统通知");
    expect(toolLabelZh("external_mount_readonly")).toBe("挂载本机目录");
  });

  it("未知工具回退原名", () => {
    expect(toolLabelZh("unknown_tool_xyz")).toBe("unknown_tool_xyz");
  });

  it("与英文 execution TOOL_LABELS 分表（表值为中文展示，非 Search web 等英文 chrome）", () => {
    expect(TOOL_LABELS_ZH.file_write).toBe("写入文件");
    expect(TOOL_LABELS_ZH.code_execute).toBe("执行代码");
    expect(TOOL_LABELS_ZH.web_search).toBeUndefined();
  });
});
