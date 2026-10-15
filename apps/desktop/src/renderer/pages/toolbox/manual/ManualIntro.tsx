import { ChapterRenderer } from "./ChapterRenderer";
import { introChapter } from "./content/intro";

/** 认识 GAgent——由结构化内容源驱动。 */
export function ManualIntro() {
  return (
    <ChapterRenderer chapter={introChapter} previewManual="manual-intro" />
  );
}
