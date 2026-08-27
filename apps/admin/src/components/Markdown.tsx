/**
 * Lightweight Markdown for admin conversation replay.
 * react-markdown + remark-gfm + rehype-highlight. No katex / mermaid / citations.
 *
 * Highlight is lowlight → `<span class="hljs-*">` at render time — no eval,
 * workers, or theme scripts, so it holds under production `script-src 'self'`.
 */
import { type ComponentPropsWithoutRef, memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

type ReactMarkdownProps = ComponentPropsWithoutRef<typeof ReactMarkdown>;

const remarkPlugins: ReactMarkdownProps["remarkPlugins"] = [remarkGfm];
const rehypePlugins: ReactMarkdownProps["rehypePlugins"] = [
  [rehypeHighlight, { ignoreMissing: true }],
];

const components: Components = {
  a({ href, children, ...props }) {
    return (
      <a href={href} target="_blank" rel="noreferrer" {...props}>
        {children}
      </a>
    );
  },
  img({ src, alt }) {
    // SECURITY (PI-001): never auto-load model-emitted images — downgrade to a link.
    const href = typeof src === "string" ? src : undefined;
    const label =
      typeof alt === "string" && alt.trim() ? alt.trim() : "图片链接";
    if (!href) return <>{label}</>;
    return (
      <a href={href} target="_blank" rel="noreferrer">
        {label}
      </a>
    );
  },
};

export const Markdown = memo(function Markdown({
  content,
  muted = false,
}: {
  content: string;
  /** Secondary tone for thought — headings inherit, unlike a parent text-* wrap. */
  muted?: boolean;
}) {
  return (
    <div
      className={
        muted
          ? "markdown-body markdown-body--muted min-w-0 max-w-full"
          : "markdown-body min-w-0 max-w-full"
      }
    >
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});
