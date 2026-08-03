import BrandMark from "@/components/BrandMark";
import DownloadPageHero from "@/components/DownloadPageHero";
import DownloadPanel from "@/components/DownloadPanel";
import {
  ANDROID_INSTALL_STEPS,
  DOWNLOAD_PAGE_PATH,
  MOBILE_WEB_URL,
  MAC_INSTALL_STEPS,
  WIN_INSTALL_STEPS,
} from "@/lib/download";
import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "下载 AgentCore 桌面客户端 — 协作智能平台",
  description:
    "下载 AgentCore 桌面客户端 for Windows 与 macOS（Apple Silicon）。Multi-Agent 协作工作台，自动更新。",
};

function InstallStepList({ steps }: { steps: string[] }) {
  return (
    <ol className="mt-6 space-y-4">
      {steps.map((step, i) => (
        <li key={step} className="surface flex gap-4 p-5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-sm font-bold text-primary">
            {i + 1}
          </span>
          <p className="pt-1 text-muted-foreground">{step}</p>
        </li>
      ))}
    </ol>
  );
}

export default function DownloadPage() {
  return (
    <div className="relative min-h-screen">
      <header className="sticky top-0 z-50 border-b border-border/60 bg-[color-mix(in_oklab,var(--background),transparent_25%)] backdrop-blur-xl">
        <nav className="container-x flex h-16 items-center justify-between">
          <Link href="/" className="flex items-center gap-2.5">
            <BrandMark size={20} />
            <span className="text-base font-semibold tracking-tight">AgentCore</span>
          </Link>
          <Link href="/" className="text-sm text-muted-foreground hover:text-foreground">
            ← 返回首页
          </Link>
        </nav>
      </header>

      <main className="container-x py-16 sm:py-24">
        <DownloadPageHero />

        <div className="mx-auto mt-12 max-w-4xl">
          <DownloadPanel />
        </div>

        <section className="mx-auto mt-10 max-w-4xl">
          <div className="surface flex flex-col gap-5 p-8 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-left">
              <p className="eyebrow">Mobile Web</p>
              <h2 className="mt-2 text-xl font-bold">手机网页版</h2>
              <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">
                在手机或平板浏览器打开即可使用，无需安装。云端对话、多 Agent 协作与桌面端同源 API。
              </p>
            </div>
            <a
              href={MOBILE_WEB_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="btn btn-ghost shrink-0 px-6 py-3"
            >
              打开 m.fashitianxia.xyz
            </a>
          </div>
        </section>

        <section className="mx-auto mt-16 max-w-3xl">
          <h2 className="text-center text-xl font-bold">安装步骤</h2>
          <div className="mt-10 grid gap-12 lg:grid-cols-3">
            <div>
              <h3 className="text-center text-base font-semibold">Windows</h3>
              <InstallStepList steps={WIN_INSTALL_STEPS} />
            </div>
            <div>
              <h3 className="text-center text-base font-semibold">
                macOS
                <span className="ml-1.5 text-sm font-normal text-muted-foreground">
                  Apple Silicon · 内测
                </span>
              </h3>
              <InstallStepList steps={MAC_INSTALL_STEPS} />
            </div>
            <div>
              <h3 className="text-center text-base font-semibold">Android</h3>
              <InstallStepList steps={ANDROID_INSTALL_STEPS} />
            </div>
          </div>
        </section>

        <section className="mx-auto mt-16 max-w-3xl rounded-2xl border border-border/70 bg-card/40 p-8 text-center">
          <h2 className="text-lg font-bold">已有客户端？</h2>
          <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
            打开 AgentCore → 设置 → 关于 → 检查更新。新版本会在后台下载，就绪后提示重启安装。
            macOS 内测包更新安装后若无法启动，请再次右键 → 打开。
          </p>
        </section>
      </main>

      <footer className="border-t border-border/60 py-8">
        <div className="container-x text-center text-sm text-muted-foreground">
          <Link href="/" className="hover:text-foreground">
            fashitianxia.xyz
          </Link>
          <span className="mx-2">·</span>
          <span>{DOWNLOAD_PAGE_PATH}</span>
        </div>
      </footer>
    </div>
  );
}
