"use client";

import { useLang } from "@/components/LangProvider";
import PaperFooter from "@/components/PaperFooter";
import PillNav from "@/components/PillNav";
import { NOT_FOUND } from "@/content/home";
import { DOWNLOAD_PAGE_PATH, WEB_APP_URL } from "@/lib/download";

/**
 * 404。
 *
 * 与首页 Hero 同构（深靛黑 + 星野 + 极光），只是内容换成一句道歉和几个出口——
 * 走丢的人最需要的是路，不是一张创意插画。
 */
export default function NotFoundView() {
  const { t } = useLang();

  return (
    <>
      <PillNav home={false} />

      <main className="relative z-10">
        <section className="relative z-0 flex min-h-[82vh] flex-col justify-center overflow-hidden bg-[var(--ink-deep)] pb-24 pt-32">
          <div
            aria-hidden="true"
            className="starfield pointer-events-none absolute inset-0 opacity-80"
          />
          <div
            aria-hidden="true"
            className="aurora pointer-events-none absolute inset-0"
          />
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 bottom-0 h-[12.5rem] bg-gradient-to-t from-background via-background/70 to-transparent"
          />

          <div className="container-x relative z-[2] text-center">
            <p
              className="float-in m-0 font-bold leading-none tracking-[-0.04em]"
              style={{
                fontSize: "clamp(5rem, 18vw, 11rem)",
                background: "var(--brand-gradient)",
                WebkitBackgroundClip: "text",
                backgroundClip: "text",
                color: "transparent",
              }}
            >
              404
            </p>

            <h1
              className="display-title float-in mx-auto mt-4 max-w-[34rem]"
              style={{ animationDelay: "80ms" }}
            >
              {t(NOT_FOUND.title)}
            </h1>

            <p
              className="float-in display-lead mx-auto mt-5 max-w-[30rem] text-dim"
              style={{ animationDelay: "160ms" }}
            >
              {t(NOT_FOUND.lead)}
            </p>

            <div
              className="float-in mt-10 flex flex-col justify-center gap-3 sm:flex-row sm:flex-wrap"
              style={{ animationDelay: "240ms" }}
            >
              <a href="/" className="btn-grad max-sm:min-h-[3.25rem]">
                <span>{t(NOT_FOUND.home)}</span>
                <span aria-hidden="true" className="text-[0.75rem]">
                  ↗
                </span>
              </a>
              <a
                href={WEB_APP_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="btn-outline max-sm:min-h-[3.25rem]"
                style={{
                  background:
                    "linear-gradient(var(--ink-deep), var(--ink-deep)) padding-box, var(--brand-gradient) border-box",
                }}
              >
                {t(NOT_FOUND.app)}
              </a>
              <a
                href={DOWNLOAD_PAGE_PATH}
                className="btn-outline max-sm:min-h-[3.25rem]"
                style={{
                  background:
                    "linear-gradient(var(--ink-deep), var(--ink-deep)) padding-box, var(--brand-gradient) border-box",
                }}
              >
                {t(NOT_FOUND.download)}
              </a>
            </div>
          </div>
        </section>
      </main>

      <PaperFooter home={false} />
    </>
  );
}
