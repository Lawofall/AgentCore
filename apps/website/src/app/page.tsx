"use client";

import BrowserFrame from "@/components/BrowserFrame";
import CollabGraph from "@/components/CollabGraph";
import { useLang } from "@/components/LangProvider";
import LogoWall from "@/components/LogoWall";
import PaperFooter from "@/components/PaperFooter";
import PillNav from "@/components/PillNav";
import ProcessPath from "@/components/ProcessPath";
import UseCases from "@/components/UseCases";
import Reveal from "@/components/Reveal";
import RichTitle from "@/components/RichTitle";
import {
  CAPABILITIES,
  CLOSING,
  COMPARE,
  CTA,
  ECOSYSTEM,
  HERO,
  MARQUEE,
  MECHANISM,
  ROLE,
  THESIS,
  USECASES,
} from "@/content/home";
import {
  DOWNLOAD_PAGE_PATH,
  MOBILE_WEB_URL,
  WEB_APP_URL,
} from "@/lib/download";

/* Hero 社会认同位左侧的头像堆：用协作图那套 Agent 身份色，
   三个相互压叠的圆点代表「一支队伍」而非一个头像。 */
function AgentStack() {
  return (
    <span aria-hidden="true" className="flex shrink-0 -space-x-2.5">
      {["--agent-1", "--agent-6", "--agent-4"].map((token) => (
        <span
          key={token}
          className="block size-9 rounded-full border-2 border-[var(--ink-deep)]"
          style={{
            background: `radial-gradient(circle at 32% 28%, color-mix(in oklab, var(${token}), white 28%), var(${token}))`,
          }}
        />
      ))}
    </span>
  );
}

/* 分区小标：等宽大写 + 一段短横线。白纸与暗纸各一套配色。 */
function Kicker({
  children,
  onPaper = false,
  className = "",
}: {
  children: React.ReactNode;
  onPaper?: boolean;
  className?: string;
}) {
  return (
    <p
      className={`inline-flex items-center gap-3 font-mono text-[0.6875rem] uppercase tracking-[0.2em] ${
        onPaper ? "text-paper-faint" : "text-dim"
      } ${className}`.trim()}
    >
      <span
        aria-hidden="true"
        className="block h-px w-5 shrink-0"
        style={{ background: "var(--brand-gradient)" }}
      />
      {children}
    </p>
  );
}

export default function Home() {
  const { t } = useLang();

  return (
    <>
      <PillNav />

      {/* z-10 让 main 盖住 fixed 在视口底部的白纸页脚（幕布），内容滚走才揭开；
          同时 main 成为层叠上下文，各分区的 z 只在内部排序。
          这里刻意不给背景色也不裁切：各分区自带不透明底，留白处（最后一张暗纸的
          下圆角）要透出下面的白页脚；横向裁切由 body 的 overflow-x 负责。 */}
      <main id="top" className="relative z-10">
        {/* ══ 1 · Hero ══ 深靛黑 + 星野 + 极光，全站唯一的一屏 ══════ */}
        <section className="relative z-0 flex min-h-[94vh] flex-col justify-center overflow-hidden bg-[var(--ink-deep)] pb-24 pt-32 xl:min-h-[88vh] xl:pb-16 xl:pt-40">
          <div
            aria-hidden="true"
            className="starfield pointer-events-none absolute inset-0 opacity-80"
          />
          <div
            aria-hidden="true"
            className="aurora pointer-events-none absolute inset-0"
          />
          {/* 底缘淡出到画布色，让 Hero 与下一张纸之间没有硬边。 */}
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 bottom-0 h-[12.5rem] bg-gradient-to-t from-background via-background/70 to-transparent"
          />

          {/* 两栏推到 xl 而不是 lg：lg（1024px）下右栏只剩 ~470px，
              协作图会被迫退到窄几何，反而比整宽单栏更挤。 */}
          <div className="container-x relative grid items-center gap-14 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.06fr)] xl:gap-16">
            <div>
              {/* 品类行：首屏必须有一句说清「这是个什么平台」。
                  改版时一度被我换成头像堆丢掉了，补回来。 */}
              <p className="float-in mb-6 flex items-center gap-2.5 text-[0.9375rem] font-semibold">
                <span
                  aria-hidden="true"
                  className="block size-[7px] shrink-0 rounded-full"
                  style={{ background: "var(--brand-gradient)" }}
                />
                <span className="grad-text">{t(HERO.eyebrow)}</span>
              </p>

              <RichTitle
                as="h1"
                text={t(HERO.headline)}
                className="display-title float-in"
                style={{ animationDelay: "80ms" }}
              />

              {/* 引文块：左侧渐变竖线 + 引号，末句用点睛词收。 */}
              <blockquote
                className="hero-quote float-in mt-8"
                style={{ animationDelay: "180ms" }}
              >
                <span aria-hidden="true" className="hero-quote-mark">
                  &ldquo;
                </span>
                {t(HERO.quote)
                  .split("\n")
                  .map((line) => (
                    <p key={line} className="hero-quote-line">
                      {line}
                    </p>
                  ))}
                <RichTitle
                  as="p"
                  text={t(HERO.punch)}
                  className="hero-quote-punch"
                />
              </blockquote>

              <div
                className="float-in mt-8 flex items-center gap-3.5"
                style={{ animationDelay: "260ms" }}
              >
                <AgentStack />
                <div>
                  <p className="m-0 text-[0.875rem] font-semibold">
                    {t(HERO.proof)}
                  </p>
                  <p className="m-0 mt-1 font-mono text-[0.65625rem] uppercase tracking-[0.14em] text-faint">
                    {HERO.specs.map((s) => t(s)).join(" · ")}
                  </p>
                </div>
              </div>

              <div
                className="float-in mt-9 flex flex-col gap-3 sm:flex-row sm:flex-wrap"
                style={{ animationDelay: "300ms" }}
              >
                <a
                  href={WEB_APP_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn-grad max-sm:min-h-[3.25rem]"
                >
                  <span>{t(CTA.webApp)}</span>
                  <span aria-hidden="true" className="text-[0.75rem]">
                    ↗
                  </span>
                </a>
                <a
                  href={DOWNLOAD_PAGE_PATH}
                  className="btn-outline max-sm:min-h-[3.25rem]"
                  style={{
                    background:
                      "linear-gradient(var(--ink-deep), var(--ink-deep)) padding-box, var(--brand-gradient) border-box",
                  }}
                >
                  {t(CTA.desktop)}
                </a>
              </div>
            </div>

            {/* 单栏区间（<xl）给个宽度上限：整宽铺开时协作图会长到 800px+ 高，
                把下面的内容全推走。两栏时右栏本身就够窄，放开即可。 */}
            <div
              className="float-in mx-auto w-full max-w-[44rem] max-sm:-mx-1 xl:max-w-none"
              style={{ animationDelay: "420ms" }}
            >
              <BrowserFrame url="app.agentcore.dev">
                <CollabGraph />
              </BrowserFrame>
            </div>
          </div>
        </section>

        {/* ══ 2 · 命题 ══ 第一张暗纸，下缘收圆角压住白纸 ══════════ */}
        <section
          id="thesis"
          className="panel panel-bottom noise panel-pad relative z-40 border-b border-border-soft bg-background"
        >
          <div className="container-x relative z-[2] text-center">
            <Reveal>
              <Kicker className="mb-7">{t(THESIS.eyebrow)}</Kicker>
            </Reveal>
            <Reveal>
              <RichTitle
                text={t(THESIS.headline)}
                className="display-title mx-auto max-w-[46rem]"
              />
            </Reveal>
            <Reveal delay={90}>
              <p className="display-lead mx-auto mt-6 max-w-[38rem] text-dim">
                {t(THESIS.lead)}
              </p>
            </Reveal>

            <div className="mt-14 grid gap-10 text-left sm:mt-20 lg:grid-cols-3 lg:gap-12">
              {THESIS.values.map((value, i) => (
                <Reveal key={value.title.zh} delay={i * 110}>
                  <span
                    aria-hidden="true"
                    className="mb-6 block h-px w-full"
                    style={{ background: "var(--brand-gradient)", opacity: 0.5 }}
                  />
                  <RichTitle
                    as="h3"
                    text={t(value.title)}
                    className="m-0 mb-3 text-[1.4375rem] font-bold tracking-[-0.02em] sm:text-[1.625rem]"
                  />
                  <p className="m-0 text-[0.9375rem] leading-[1.75] text-dim [text-wrap:pretty]">
                    {t(value.body)}
                  </p>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        {/* ══ 3 · 白纸：跑马灯 + 五类资产 ══ 塞进上一张纸的下缘 ══ */}
        <section
          id="ecosystem"
          className="paper-panel noise relative z-10 -mt-16 overflow-hidden pb-[6.5rem] pt-[8.5rem] md:-mt-24 md:pb-[9rem] md:pt-[12rem]"
        >
          <div className="relative z-[2]">
            <div className="container-x text-center">
              <Reveal>
                <p className="wall-heading">{t(MARQUEE.eyebrow)}</p>
              </Reveal>
            </div>

            <div className="mt-12 md:mt-16">
              <LogoWall />
            </div>

            {/* 预设之外还能接什么，得写清楚——只列 8 个名字容易被读成「只支持这些」。 */}
            <div className="container-x mt-8 text-center md:mt-10">
              <Reveal>
                <p className="m-0 text-[0.875rem] leading-[1.7] text-paper-faint">
                  {t(MARQUEE.note)}
                </p>
              </Reveal>
            </div>

            <div className="container-x mt-20 md:mt-28">
              <div className="mb-10 grid items-end gap-6 md:mb-14 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] lg:gap-16">
                <Reveal>
                  <Kicker onPaper className="mb-5">
                    {t(ECOSYSTEM.eyebrow)}
                    <span className="ml-1 rounded-full border border-paper-line px-2 py-0.5 text-[0.5625rem] tracking-[0.12em]">
                      {t(ECOSYSTEM.badge)}
                    </span>
                  </Kicker>
                  <RichTitle
                    text={t(ECOSYSTEM.headline)}
                    className="display-title text-[clamp(1.875rem,3.8vw,3rem)] text-paper-ink"
                  />
                </Reveal>
                <Reveal delay={90}>
                  <p className="display-lead m-0 text-paper-dim lg:mb-2">
                    {t(ECOSYSTEM.lead)}
                  </p>
                </Reveal>
              </div>

              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 lg:grid-cols-5">
                {ECOSYSTEM.assets.map((asset, i) => (
                  <Reveal key={asset.code} delay={i * 70} className="h-full">
                    <div
                      className={`flex h-full flex-col rounded-2xl border px-5 pb-7 pt-6 transition-transform duration-300 hover:-translate-y-1 ${
                        asset.featured
                          ? "border-transparent text-[var(--on-brand)]"
                          : "border-paper-line bg-white"
                      }`}
                      style={
                        asset.featured
                          ? { background: "var(--brand-gradient)" }
                          : undefined
                      }
                    >
                      <span
                        className="mb-8 block size-[9px] rounded-sm sm:mb-11"
                        style={{
                          background: asset.featured
                            ? "color-mix(in oklab, var(--on-brand), transparent 10%)"
                            : asset.accent === "primary"
                              ? "var(--grad-4)"
                              : "var(--grad-1)",
                        }}
                      />
                      <p className="m-0 mb-1 text-base font-bold">
                        {asset.code}
                      </p>
                      <p
                        className={`m-0 mb-3 text-[0.8125rem] ${
                          asset.featured
                            ? "text-[color-mix(in_oklab,var(--on-brand),transparent_30%)]"
                            : "text-paper-faint"
                        }`}
                      >
                        {t(asset.name)}
                      </p>
                      <p
                        className={`m-0 text-[0.84375rem] leading-[1.7] ${
                          asset.featured
                            ? "text-[color-mix(in_oklab,var(--on-brand),transparent_15%)]"
                            : "text-paper-dim"
                        }`}
                      >
                        {t(asset.body)}
                      </p>
                    </div>
                  </Reveal>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* ══ 4 · 暗纸：四步机制 + 单体对照 + 核心能力 ══════════ */}
        <section
          id="how"
          className="panel panel-top noise panel-pad relative z-20 scroll-mt-28 bg-background"
        >
          <div className="container-x relative z-[2]">
            <div className="text-center">
              <Reveal>
                <Kicker className="mb-6">{t(MECHANISM.eyebrow)}</Kicker>
              </Reveal>
              <Reveal>
                <RichTitle
                  text={t(MECHANISM.headline)}
                  className="display-title mx-auto max-w-[42rem]"
                />
              </Reveal>
              <Reveal delay={90}>
                <p className="display-lead mx-auto mt-6 max-w-[36rem] text-dim">
                  {t(MECHANISM.lead)}
                </p>
              </Reveal>
            </div>

            <div className="mt-16 sm:mt-24">
              <ProcessPath />
            </div>

            {/* 能交付什么：四种产物，左右图文交替 */}
            <div id="build" className="mt-24 scroll-mt-28 sm:mt-32">
              <div className="mb-12 text-center sm:mb-16">
                <Reveal>
                  <RichTitle
                    text={t(USECASES.title)}
                    className="display-title mx-auto max-w-[38rem]"
                  />
                </Reveal>
                <Reveal delay={80}>
                  <p className="display-lead mx-auto mt-5 max-w-[32rem] text-dim">
                    {t(USECASES.lead)}
                  </p>
                </Reveal>
              </div>
              <UseCases />
            </div>

            {/* 核心能力 */}
            <div id="value" className="mt-20 scroll-mt-28 sm:mt-28">
              <Reveal>
                <Kicker className="mb-6">{t(CAPABILITIES.eyebrow)}</Kicker>
              </Reveal>
              <Reveal>
                <RichTitle
                  text={t(CAPABILITIES.headline)}
                  className="display-title mb-10 max-w-[44rem] text-[clamp(1.875rem,3.8vw,3rem)] sm:mb-14"
                />
              </Reveal>

              <div className="grid gap-px overflow-hidden rounded-2xl border border-border-soft bg-border-soft lg:grid-cols-3">
                {CAPABILITIES.cards.map((card, i) => (
                  <Reveal key={card.idx} delay={i * 90} className="h-full">
                    <div className="h-full bg-[var(--panel)] px-6 pb-9 pt-8 sm:px-8 sm:pb-11 sm:pt-10">
                      <p
                        className="mb-5 font-mono text-[0.6875rem] sm:mb-7"
                        style={{ color: "var(--grad-4)" }}
                      >
                        {card.idx}
                      </p>
                      <h3 className="m-0 mb-3.5 text-[1.1875rem] font-bold tracking-[-0.015em] sm:text-[1.3125rem]">
                        {t(card.title)}
                      </h3>
                      <p className="m-0 text-[0.875rem] leading-[1.8] text-dim [text-wrap:pretty] sm:text-[0.90625rem]">
                        {t(card.body)}
                      </p>
                    </div>
                  </Reveal>
                ))}
              </div>

              <Reveal delay={120}>
                <div className="mt-6 flex flex-wrap gap-2">
                  {CAPABILITIES.tags.map((tag) => (
                    <span key={tag.zh} className="mono-tag">
                      {t(tag)}
                    </span>
                  ))}
                </div>
              </Reveal>
            </div>
          </div>
        </section>

        {/* ══ 5 · 白纸：对比表 + 角色演进 ══════════════════════ */}
        <section
          id="compare"
          className="paper-panel panel panel-top noise panel-pad relative z-30 scroll-mt-28"
        >
          <div className="container-x relative z-[2]">
            <Reveal>
              <Kicker onPaper className="mb-6">
                {t(COMPARE.eyebrow)}
              </Kicker>
            </Reveal>
            <Reveal>
              <RichTitle
                text={t(COMPARE.headline)}
                className="display-title mb-10 text-paper-ink sm:mb-14"
              />
            </Reveal>

            <Reveal>
              <div className="overflow-hidden rounded-2xl border border-paper-line">
                <div className="hidden grid-cols-[13rem_1fr_1fr] border-b border-paper-line bg-black/[0.02] sm:grid">
                  <div className="px-6 py-5" />
                  <div className="px-6 py-5 font-mono text-[0.6875rem] uppercase tracking-[0.14em] text-paper-faint">
                    {t(COMPARE.headOthers)}
                  </div>
                  <div
                    className="px-6 py-5 font-mono text-[0.6875rem] uppercase tracking-[0.14em]"
                    style={{ color: "var(--grad-2)" }}
                  >
                    {COMPARE.headOurs}
                  </div>
                </div>

                {COMPARE.rows.map((row) => (
                  <div
                    key={row.dim.zh}
                    className="grid border-b border-paper-line last:border-b-0 sm:grid-cols-[13rem_1fr_1fr]"
                  >
                    <div className="px-5 pb-2 pt-4 text-sm font-semibold text-paper-ink sm:px-6 sm:py-6">
                      {t(row.dim)}
                    </div>
                    <div className="px-5 pb-3 text-[0.875rem] leading-[1.65] text-paper-faint sm:px-6 sm:py-6 sm:text-[0.90625rem]">
                      <span className="mb-1 block font-mono text-[0.625rem] uppercase tracking-[0.14em] text-paper-faint sm:hidden">
                        {t(COMPARE.headOthers)}
                      </span>
                      {t(row.others)}
                    </div>
                    <div className="bg-[color-mix(in_oklab,var(--grad-2),white_94%)] px-5 pb-4 pt-3 text-[0.875rem] leading-[1.65] text-paper-ink sm:px-6 sm:py-6 sm:text-[0.90625rem]">
                      <span
                        className="mb-1 block font-mono text-[0.625rem] uppercase tracking-[0.14em] sm:hidden"
                        style={{ color: "var(--grad-2)" }}
                      >
                        {COMPARE.headOurs}
                      </span>
                      {t(row.ours)}
                    </div>
                  </div>
                ))}
              </div>
            </Reveal>

            {/* 角色演进 */}
            <div id="role" className="mt-20 scroll-mt-28 sm:mt-28">
              <Reveal>
                <Kicker onPaper className="mb-6">
                  {t(ROLE.eyebrow)}
                </Kicker>
              </Reveal>
              <Reveal>
                <RichTitle
                  text={t(ROLE.headline)}
                  className="display-title mb-3 text-[clamp(1.875rem,3.8vw,3rem)] text-paper-ink"
                />
              </Reveal>
              <Reveal delay={80}>
                <p className="display-lead mb-12 max-w-[36rem] text-paper-dim sm:mb-16">
                  {t(ROLE.lead)}
                </p>
              </Reveal>

              <Reveal bare>
                <div className="relative">
                  <span
                    aria-hidden="true"
                    className="absolute inset-x-0 top-[1.625rem] hidden h-px bg-paper-line sm:block"
                  />
                  <span
                    aria-hidden="true"
                    className="role-line absolute left-0 top-[1.625rem] hidden h-px w-full sm:block"
                    style={{ background: "var(--brand-gradient)" }}
                  />
                  <div className="relative grid gap-10 sm:grid-cols-3 sm:gap-8">
                    {ROLE.stages.map((stage) => (
                      <div key={stage.product}>
                        <span
                          aria-hidden="true"
                          className="mb-7 mt-[1.3125rem] hidden size-[11px] rounded-full sm:block"
                          style={
                            stage.tone === "now"
                              ? {
                                  background: "var(--grad-4)",
                                  boxShadow:
                                    "0 0 0 4px color-mix(in oklab, var(--grad-4), transparent 84%)",
                                }
                              : {
                                  background: "var(--paper)",
                                  border: "1px solid var(--paper-line)",
                                }
                          }
                        />
                        <p
                          className={`mb-3 font-mono text-[0.65625rem] uppercase tracking-[0.16em] ${
                            stage.tone === "now" ? "" : "text-paper-faint"
                          }`}
                          style={
                            stage.tone === "now"
                              ? { color: "var(--grad-4)" }
                              : undefined
                          }
                        >
                          {stage.product}
                        </p>
                        <p
                          className={`mb-2.5 text-[1.625rem] font-bold tracking-[-0.02em] sm:text-[1.875rem] ${
                            stage.tone === "now"
                              ? "text-paper-ink"
                              : "text-paper-faint"
                          }`}
                        >
                          {t(stage.name)}
                        </p>
                        <p
                          className={`m-0 text-sm leading-[1.75] ${
                            stage.tone === "now"
                              ? "text-paper-dim"
                              : "text-paper-faint"
                          }`}
                        >
                          {t(stage.body)}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              </Reveal>
            </div>
          </div>
        </section>

        {/* ══ 6 · 暗纸：结尾 CTA ══════════════════════════════ */}
        <section
          id="start"
          className="panel panel-top panel-foot noise relative z-40 overflow-hidden bg-[var(--ink-deep)] pb-32 pt-24 sm:pb-40 sm:pt-32"
        >
          <div
            aria-hidden="true"
            className="starfield pointer-events-none absolute inset-0 opacity-60"
          />
          <div
            aria-hidden="true"
            className="aurora pointer-events-none absolute inset-0"
          />
          {/* 与页脚字标同一条洗色：这张纸的下缘先被染上，接缝之后由页脚接着往下走。 */}
          <div aria-hidden="true" className="aurora-wash panel-wash z-[3]" />
          <div aria-hidden="true" className="aurora-grain panel-wash z-[4]" />

          <div className="container-x relative z-[2] text-center">
            <Reveal>
              <RichTitle
                text={t(CLOSING.headline)}
                className="display-title mx-auto max-w-[38rem]"
              />
            </Reveal>
            <Reveal delay={80}>
              <p className="display-lead mx-auto mb-10 mt-6 max-w-[32rem] text-dim sm:mb-12">
                {t(CLOSING.lead)}
              </p>
            </Reveal>
            <Reveal delay={160}>
              <div className="flex flex-col justify-center gap-3 sm:flex-row sm:flex-wrap">
                <a
                  href={WEB_APP_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn-grad px-7 py-4 max-sm:min-h-[3.25rem]"
                >
                  <span>{t(CTA.webApp)}</span>
                  <span aria-hidden="true" className="text-[0.75rem]">
                    ↗
                  </span>
                </a>
                <a
                  href={DOWNLOAD_PAGE_PATH}
                  className="btn-outline px-7 py-4 max-sm:min-h-[3.25rem]"
                  style={{
                    background:
                      "linear-gradient(var(--ink-deep), var(--ink-deep)) padding-box, var(--brand-gradient) border-box",
                  }}
                >
                  {t(CTA.desktop)}
                </a>
                <a
                  href={MOBILE_WEB_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn btn-quiet px-7 py-4 max-sm:min-h-[3.25rem]"
                >
                  {t(CTA.mobileWeb)}
                </a>
              </div>
            </Reveal>
          </div>
        </section>
      </main>

      <PaperFooter />
    </>
  );
}
