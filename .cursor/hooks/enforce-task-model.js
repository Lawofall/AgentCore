/**
 * preToolUse hook: force Task tool `model` to cursor-grok-4.6-high.
 * Fail-open: any parse/runtime error exits 0 with empty allow (no rewrite).
 *
 * Schema: https://cursor.com/docs/hooks (preToolUse)
 *   stdin:  { tool_name, tool_input, ... }
 *   stdout: { permission?, updated_input? }
 */

"use strict";

const REQUIRED_MODEL = "cursor-grok-4.6-high";

function readStdin() {
  return new Promise((resolve, reject) => {
    const chunks = [];
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (c) => chunks.push(c));
    process.stdin.on("end", () => resolve(chunks.join("")));
    process.stdin.on("error", reject);
  });
}

function allow(updatedInput) {
  const out = { permission: "allow" };
  if (updatedInput !== undefined) {
    out.updated_input = updatedInput;
  }
  process.stdout.write(JSON.stringify(out));
}

async function main() {
  try {
    const raw = await readStdin();
    if (!raw || !raw.trim()) {
      allow();
      return;
    }

    let payload;
    try {
      payload = JSON.parse(raw);
    } catch {
      allow();
      return;
    }

    // Defense in depth: matcher should already filter to Task.
    if (payload.tool_name && payload.tool_name !== "Task") {
      allow();
      return;
    }

    const toolInput =
      payload.tool_input && typeof payload.tool_input === "object"
        ? payload.tool_input
        : {};

    if (toolInput.model === REQUIRED_MODEL) {
      allow();
      return;
    }

    allow({ ...toolInput, model: REQUIRED_MODEL });
  } catch {
    allow();
  }
}

main();
