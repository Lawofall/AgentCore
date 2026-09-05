import { getLegalDoc } from "./content";
import type { LegalDocId } from "./types";

/**
 * Legal document body — the sections only, used by the login overlay and
 * 设置·关于.
 *
 * The title + 更新日期 belong to whoever hosts the body: the settings route puts
 * them in its `PageHeader`, the pre-auth pane in its own header. Rendering
 * them here too is how `/more/legal/:docId` ended up with two `<h1>`s and the
 * date printed twice.
 */
export function LegalDocBody({ docId }: { docId: LegalDocId }) {
  const doc = getLegalDoc(docId);
  if (!doc) {
    return <p className="text-sm text-muted-foreground">未找到该文档。</p>;
  }

  return (
    <article className="space-y-6 text-sm text-foreground">
      {doc.sections.map((section) => (
        <section key={section.heading} className="space-y-2">
          <h2 className="text-sm font-semibold">{section.heading}</h2>
          {section.paragraphs[0] ? (
            <p className="leading-relaxed text-muted-foreground">
              {section.paragraphs[0]}
            </p>
          ) : null}
          {section.bullets && section.bullets.length > 0 ? (
            <ul className="list-disc space-y-1.5 pl-5 text-muted-foreground">
              {section.bullets.map((item) => (
                <li key={item} className="leading-relaxed">
                  {item}
                </li>
              ))}
            </ul>
          ) : null}
          {section.paragraphs.slice(1).map((p) => (
            <p key={p} className="leading-relaxed text-muted-foreground">
              {p}
            </p>
          ))}
        </section>
      ))}
    </article>
  );
}
