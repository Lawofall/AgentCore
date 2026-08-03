"use client";

import { BranchesDiagram, ChainDiagram } from "@/components/BranchDiagram";
import { useLang } from "@/components/LangProvider";
import MechanismBoard from "@/components/MechanismBoard";
import Reveal from "@/components/Reveal";
import SiteFooter from "@/components/SiteFooter";
import SiteHeader from "@/components/SiteHeader";
import TaskConsole from "@/components/TaskConsole";
import {
  CAPABILITIES,
  CLOSING,
  COMPARE,
  CTA,
  ECOSYSTEM,
  HERO,
  ROLE,
  THESIS,
  WHY,
} from "@/content/home";
import {
  DOWNLOAD_PAGE_PATH,
  MOBILE_WEB_URL,
  WEB_APP_URL,
} from "@/lib/download";

export default function Home() {
  const { t } = useLang();

  return (
    <>
      <div aria-hidden="true" className="grid-backdrop">
        <span />
      </div>

      <SiteHeader />

      <main id="top">
        {/* ── Hero ── */}
        <section className="relative z-[1] overflow-hidden pb-16 pt-[6.5rem] sm:pb-24 sm:pt-[9.375rem]">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute -top-36 left-1/2 h-[38.75rem] w-[min(68.75rem,100vw)] -translate-x-1/2 blur-[10px]"
            style={{
              background:
                "radial-gradient(ellipse at 30% 40%, var(--glow-1), transparent 62%), radial-gradient(ellipse at 78% 30%, var(--glow-2), transparent 60%)",
            }}
          />
          <div className="container-x relative grid items-center gap-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.02fr)] lg:gap-16">
            <div>
              <div
                className="float-in mb-[1.375rem] flex items-center gap-[9px] sm:mb-[1.875rem]"
                style={{ animationDelay: "60ms" }}
              >
                <span className="block size-1.5 rounded-full bg-primary" />
                <span className="font-mono text-[0.625rem] uppercase tracking-[0.2em] text-dim sm:text-[0.6875rem]">
                  {t(HERO.eyebrow)}
                </span>
              </div>

              <h1
                className="float-in m-0 text-[clamp(2.375rem,4.6vw,4.25rem)] font-semibold leading-[1.08] tracking-[-0.035em]"
                style={{ animationDelay: "140ms" }}
              >
                <span className="block">{t(HERO.titleTop)}</span>
                <span className="block text-primary">{t(HERO.titleBottom)}</span>
              </h1>

              <p
                className="float-in mt-5 max-w-[32.5rem] text-[0.96875rem] leading-[1.75] text-muted-foreground sm:mt-7 sm:text-[1.0625rem]"
                style={{ animationDelay: "220ms", textWrap: "pretty" }}
              >
                <span className="hidden sm:inline">{t(HERO.lead)}</span>
                <span className="sm:hidden">{t(HERO.leadMobile)}</span>
              </p>

              <div
                className="float-in mt-7 flex flex-col gap-2.5 sm:mt-[2.375rem] sm:flex-row sm:flex-wrap sm:gap-3"
                style={{ animationDelay: "300ms" }}
              >
                <a
                  href={WEB_APP_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn btn-primary max-sm:min-h-[3.25rem] max-sm:rounded-xl"
                >
                  {t(CTA.webApp)}
                </a>
                <a
                  href={DOWNLOAD_PAGE_PATH}
                  className="btn btn-ghost max-sm:min-h-[3.25rem] max-sm:rounded-xl"
                >
                  {t(CTA.desktop)}
                </a>
              </div>

              <div
                className="float-in mt-5 flex flex-wrap gap-2 sm:mt-[1.625rem]"
                style={{ animationDelay: "380ms" }}
              >
                {HERO.specs.map((spec) => (
                  <span key={spec.zh} className="spec-pill">
                    {t(spec)}
                  </span>
                ))}
              </div>
            </div>

            <div className="float-in" style={{ animationDelay: "460ms" }}>
              <div className="hidden sm:block">
                <TaskConsole />
              </div>
              <div className="sm:hidden">
                <TaskConsole compact />
              </div>
            </div>
          </div>
        </section>

        {/* ── 命题 ── */}
        <section id="thesis" className="section">
          <div className="container-x">
            <Reveal>
              <p className="eyebrow mb-8 sm:mb-10">{t(THESIS.eyebrow)}</p>
            </Reveal>
            <div className="max-w-[62.5rem] text-[clamp(1.4375rem,2.9vw,2.625rem)] font-normal leading-[1.42] tracking-[-0.02em] [text-wrap:pretty]">
              {THESIS.lines.map((line, i) => (
                <Reveal key={line.zh} bare className="thesis-line" delay={i * 120}>
                  {t(line)}
                </Reveal>
              ))}
              <Reveal bare className="thesis-line mt-4 sm:mt-5" delay={240}>
                {t(THESIS.punch)}
              </Reveal>
            </div>
          </div>
        </section>

        {/* ── 问题 ── */}
        <section id="why" className="section">
          <div className="container-x">
            <Reveal>
              <p className="eyebrow mb-[1.125rem] sm:mb-6">{t(WHY.eyebrow)}</p>
            </Reveal>
            <div className="mb-10 grid items-end gap-6 sm:mb-16 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] lg:gap-16">
              <Reveal>
                <h2 className="section-title">
                  <span className="block">{t(WHY.titleTop)}</span>
                  <span className="block">{t(WHY.titleBottom)}</span>
                </h2>
              </Reveal>
              <Reveal delay={100}>
                <p className="section-lead m-0 lg:mb-1.5">{t(WHY.lead)}</p>
              </Reveal>
            </div>

            <div className="grid gap-3 sm:gap-6 lg:grid-cols-2">
              <Reveal className="h-full">
                <div className="h-full rounded-2xl border border-border-soft bg-[var(--panel)] px-[1.25rem] pb-[1.375rem] pt-[1.375rem] sm:px-[1.875rem] sm:pb-[2.125rem] sm:pt-[1.875rem]">
                  <p className="mb-[0.875rem] font-mono text-[0.625rem] uppercase tracking-[0.18em] text-ghost sm:mb-[1.125rem] sm:text-[0.65625rem]">
                    {t(WHY.single.kicker)}
                  </p>
                  <p className="mb-5 text-[1.0625rem] font-medium text-dim sm:mb-[1.875rem] sm:text-[1.1875rem]">
                    {t(WHY.single.title)}
                  </p>
                  <ChainDiagram />
                  <p className="m-0 text-[0.84375rem] leading-[1.75] text-faint sm:text-[0.90625rem]">
                    {t(WHY.single.body)}
                  </p>
                </div>
              </Reveal>

              <Reveal className="h-full" delay={100}>
                <div className="surface-accent h-full px-[1.25rem] pb-[1.375rem] pt-[1.375rem] sm:px-[1.875rem] sm:pb-[2.125rem] sm:pt-[1.875rem]">
                  <p className="mb-[0.875rem] font-mono text-[0.625rem] uppercase tracking-[0.18em] text-primary sm:mb-[1.125rem] sm:text-[0.65625rem]">
                    {t(WHY.team.kicker)}
                  </p>
                  <p className="mb-5 text-[1.0625rem] font-medium sm:mb-[1.875rem] sm:text-[1.1875rem]">
                    {t(WHY.team.title)}
                  </p>
                  <BranchesDiagram />
                  <p className="m-0 text-[0.84375rem] leading-[1.75] text-muted-foreground sm:text-[0.90625rem]">
                    {t(WHY.team.body)}
                  </p>
                </div>
              </Reveal>
            </div>
          </div>
        </section>

        {/* ── 协作机制（宽屏钉住 / 窄屏时间轴）── */}
        <MechanismBoard />

        {/* ── 核心能力 ── */}
        <section id="value" className="section">
          <div className="container-x">
            <Reveal>
              <p className="eyebrow mb-[1.125rem] sm:mb-6">
                {t(CAPABILITIES.eyebrow)}
              </p>
            </Reveal>
            <Reveal>
              <h2 className="section-title mb-9 max-w-[51.25rem] sm:mb-[3.875rem]">
                {t(CAPABILITIES.title)}
              </h2>
            </Reveal>

            {/* 1px 间隙 + 外框描边：三张卡看起来是一整块被切开的面板。 */}
            <div className="grid gap-px overflow-hidden rounded-2xl border border-border-soft bg-border-soft lg:grid-cols-3">
              {CAPABILITIES.cards.map((card, i) => (
                <Reveal key={card.idx} delay={i * 90} className="h-full">
                  <div className="h-full bg-[var(--panel)] px-6 pb-9 pt-8 sm:px-8 sm:pb-11 sm:pt-[2.375rem]">
                    <p className="mb-5 font-mono text-[0.6875rem] text-primary sm:mb-[1.625rem]">
                      {card.idx}
                    </p>
                    <h3 className="m-0 mb-3.5 text-[1.1875rem] font-semibold tracking-[-0.01em] sm:text-[1.3125rem]">
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
        </section>

        {/* ── 你的角色 ── */}
        <section id="role" className="section">
          <div className="container-x">
            <Reveal>
              <p className="eyebrow eyebrow-alt mb-[1.125rem] sm:mb-6">
                {t(ROLE.eyebrow)}
              </p>
            </Reveal>
            <Reveal>
              <h2 className="section-title mb-3">{t(ROLE.title)}</h2>
            </Reveal>
            <Reveal delay={80}>
              <p className="section-lead mb-10 max-w-[35rem] sm:mb-[4.125rem]">
                {t(ROLE.lead)}
              </p>
            </Reveal>

            <Reveal bare>
              <div className="relative">
                <span
                  aria-hidden="true"
                  className="absolute inset-x-0 top-[1.625rem] hidden h-px bg-border sm:block"
                />
                <span
                  aria-hidden="true"
                  className="role-line absolute left-0 top-[1.625rem] hidden h-px w-full bg-gradient-to-r from-primary to-brand-2 sm:block"
                />
                <div className="relative grid gap-8 sm:grid-cols-3">
                  {ROLE.stages.map((stage) => (
                    <div key={stage.product}>
                      <span
                        aria-hidden="true"
                        className={`mb-[1.875rem] mt-[1.3125rem] hidden size-[11px] rounded-full sm:block ${
                          stage.tone === "now"
                            ? "bg-primary shadow-[0_0_0_4px_color-mix(in_oklab,var(--primary),transparent_86%)]"
                            : stage.tone === "mid"
                              ? "border border-[oklch(0.495_0.041_271)] bg-background"
                              : "border border-line bg-background"
                        }`}
                      />
                      <p
                        className={`mb-3.5 font-mono text-[0.65625rem] uppercase tracking-[0.16em] ${
                          stage.tone === "now" ? "text-primary" : "text-ghost"
                        }`}
                      >
                        {stage.product}
                      </p>
                      <p
                        className={`mb-2.5 text-[1.5rem] font-semibold tracking-[-0.02em] sm:text-[1.6875rem] ${
                          stage.tone === "now"
                            ? "text-foreground"
                            : stage.tone === "mid"
                              ? "text-muted-foreground"
                              : "text-faint"
                        }`}
                      >
                        {t(stage.name)}
                      </p>
                      <p
                        className={`m-0 text-sm leading-[1.75] ${
                          stage.tone === "now"
                            ? "text-muted-foreground"
                            : stage.tone === "mid"
                              ? "text-faint"
                              : "text-ghost"
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
        </section>

        {/* ── 对比 ── */}
        <section id="compare" className="section">
          <div className="container-x">
            <Reveal>
              <p className="eyebrow mb-[1.125rem] sm:mb-6">
                {t(COMPARE.eyebrow)}
              </p>
            </Reveal>
            <Reveal>
              <h2 className="section-title mb-9 sm:mb-[3.375rem]">
                {t(COMPARE.title)}
              </h2>
            </Reveal>

            <Reveal>
              <div className="overflow-hidden rounded-2xl border border-border-soft">
                <div className="hidden grid-cols-[12.5rem_1fr_1fr] border-b border-border-soft bg-foreground/[0.02] sm:grid">
                  <div className="px-6 py-5" />
                  <div className="px-6 py-5 font-mono text-[0.6875rem] uppercase tracking-[0.14em] text-ghost">
                    {t(COMPARE.headOthers)}
                  </div>
                  <div className="px-6 py-5 font-mono text-[0.6875rem] uppercase tracking-[0.14em] text-primary">
                    {COMPARE.headOurs}
                  </div>
                </div>

                {COMPARE.rows.map((row) => (
                  <div
                    key={row.dim.zh}
                    className="grid border-b border-border-soft last:border-b-0 sm:grid-cols-[12.5rem_1fr_1fr]"
                  >
                    <div className="px-5 pb-2 pt-4 text-sm text-dim sm:px-6 sm:py-[1.375rem]">
                      {t(row.dim)}
                    </div>
                    <div className="px-5 pb-3 text-[0.875rem] leading-[1.65] text-faint sm:px-6 sm:py-[1.375rem] sm:text-[0.90625rem]">
                      <span className="mb-1 block font-mono text-[0.625rem] uppercase tracking-[0.14em] text-ghost sm:hidden">
                        {t(COMPARE.headOthers)}
                      </span>
                      {t(row.others)}
                    </div>
                    <div className="bg-primary/[0.03] px-5 pb-4 pt-3 text-[0.875rem] leading-[1.65] text-foreground/85 sm:px-6 sm:py-[1.375rem] sm:text-[0.90625rem]">
                      <span className="mb-1 block font-mono text-[0.625rem] uppercase tracking-[0.14em] text-primary sm:hidden">
                        {COMPARE.headOurs}
                      </span>
                      {t(row.ours)}
                    </div>
                  </div>
                ))}
              </div>
            </Reveal>
          </div>
        </section>

        {/* ── 扩展生态 ── */}
        <section id="ecosystem" className="section">
          <div className="container-x">
            <Reveal>
              <div className="mb-[1.125rem] flex flex-wrap items-center gap-3.5 sm:mb-6">
                <p className="eyebrow eyebrow-alt">{t(ECOSYSTEM.eyebrow)}</p>
                <span className="rounded-[1.25rem] border border-brand-2/30 px-2.5 py-1 font-mono text-[0.625rem] tracking-[0.14em] text-brand-2">
                  {t(ECOSYSTEM.badge)}
                </span>
              </div>
            </Reveal>
            <Reveal>
              <h2 className="section-title mb-3">{t(ECOSYSTEM.title)}</h2>
            </Reveal>
            <Reveal delay={80}>
              <p className="section-lead mb-9 max-w-[37.5rem] sm:mb-[3.25rem]">
                {t(ECOSYSTEM.lead)}
              </p>
            </Reveal>

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 lg:grid-cols-5">
              {ECOSYSTEM.assets.map((asset, i) => (
                <Reveal key={asset.code} delay={i * 70} className="h-full">
                  <div
                    className={`flex h-full flex-col px-5 pb-[1.875rem] pt-[1.625rem] ${
                      asset.featured
                        ? "surface-accent rounded-[0.875rem]"
                        : "rounded-[0.875rem] border border-border-soft bg-[var(--panel)]"
                    }`}
                  >
                    <span
                      className={`mb-8 block size-[9px] rounded-sm sm:mb-11 ${
                        asset.accent === "primary" ? "bg-primary" : "bg-brand-2"
                      } ${
                        asset.featured
                          ? "shadow-[0_0_0_4px_color-mix(in_oklab,var(--primary),transparent_86%)]"
                          : ""
                      }`}
                    />
                    <p className="mb-1 text-base font-semibold">{asset.code}</p>
                    <p className="mb-3.5 text-[0.8125rem] text-faint">
                      {t(asset.name)}
                    </p>
                    <p className="m-0 text-[0.84375rem] leading-[1.7] text-dim">
                      {t(asset.body)}
                    </p>
                  </div>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        {/* ── 结尾 CTA ── */}
        <section id="start" className="section relative overflow-hidden">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute -bottom-64 left-1/2 h-[32.5rem] w-[62.5rem] -translate-x-1/2"
            style={{
              background:
                "radial-gradient(ellipse at center, var(--glow-1), transparent 65%)",
            }}
          />
          <div className="container-x relative text-center">
            <Reveal>
              <h2 className="m-0 mb-5 text-[clamp(2.125rem,4.4vw,3.875rem)] font-semibold leading-[1.12] tracking-[-0.035em]">
                {t(CLOSING.title)}
              </h2>
            </Reveal>
            <Reveal delay={80}>
              <p className="mx-auto mb-9 max-w-[32.5rem] text-[1.0625rem] leading-[1.75] text-dim sm:mb-11">
                {t(CLOSING.lead)}
              </p>
            </Reveal>
            <Reveal delay={160}>
              <div className="flex flex-col justify-center gap-3 sm:flex-row sm:flex-wrap">
                <a
                  href={WEB_APP_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn btn-primary px-[1.625rem] py-[0.9375rem] max-sm:min-h-[3.25rem] max-sm:rounded-xl"
                >
                  {t(CTA.webApp)}
                </a>
                <a
                  href={DOWNLOAD_PAGE_PATH}
                  className="btn btn-ghost px-[1.625rem] py-[0.9375rem] max-sm:min-h-[3.25rem] max-sm:rounded-xl"
                >
                  {t(CTA.desktop)}
                </a>
                <a
                  href={MOBILE_WEB_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn btn-quiet px-[1.625rem] py-[0.9375rem] max-sm:min-h-[3.25rem] max-sm:rounded-xl"
                >
                  {t(CTA.mobileWeb)}
                </a>
              </div>
            </Reveal>
          </div>
        </section>
      </main>

      <SiteFooter />
    </>
  );
}
