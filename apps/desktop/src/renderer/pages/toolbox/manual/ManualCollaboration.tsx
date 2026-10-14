import { ChapterRenderer } from "./ChapterRenderer";
import { collaborationChapter } from "./content/collaboration";

/** 看这张图——由结构化内容源驱动。 */
export function ManualCollaboration() {
  return (
    <ChapterRenderer
      chapter={collaborationChapter}
      previewManual="manual-collaboration"
    />
  );
}
