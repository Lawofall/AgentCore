/**
 * Remark plugin: turn workspace-relative file paths in text / inline code into
 * custom `filemark` elements. Markers inside fences / links stay verbatim.
 */

import {
  isWorkspaceFilePath,
  splitWorkspacePathText,
} from "@/lib/workspaceFilePath";

interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
  data?: {
    hName?: string;
    hProperties?: Record<string, string>;
  };
}

const SKIP_TYPES = new Set([
  "code",
  "link",
  "linkReference",
  "definition",
  "image",
]);

function fileMarkNode(path: string): MdNode {
  return {
    type: "file",
    data: {
      hName: "filemark",
      hProperties: { dataPath: path },
    },
    children: [{ type: "text", value: path }],
  };
}

function walk(node: MdNode): void {
  if (!node.children) return;
  const next: MdNode[] = [];
  for (const child of node.children) {
    if (child.type === "inlineCode" && child.value) {
      const inner = child.value.trim();
      if (isWorkspaceFilePath(inner)) {
        next.push(fileMarkNode(inner));
        continue;
      }
      next.push(child);
      continue;
    }
    if (child.type === "text" && child.value && child.value.includes("/")) {
      const parts = splitWorkspacePathText(child.value);
      if (parts.length === 1 && parts[0]?.type === "text") {
        next.push(child);
      } else {
        for (const part of parts) {
          next.push(
            part.type === "path"
              ? fileMarkNode(part.value)
              : { type: "text", value: part.value },
          );
        }
      }
      continue;
    }
    if (child.children && !SKIP_TYPES.has(child.type)) {
      walk(child);
    }
    next.push(child);
  }
  node.children = next;
}

export function remarkWorkspacePaths() {
  return function attacher() {
    return (tree: MdNode) => {
      walk(tree);
    };
  };
}
