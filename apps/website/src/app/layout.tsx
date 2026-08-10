import type { Metadata, Viewport } from "next";
import { JetBrains_Mono, Newsreader, Outfit } from "next/font/google";
import LangProvider from "@/components/LangProvider";
import "./globals.css";

/*
 * 只自托管三款拉丁字体：
 *   Outfit      — 几何无衬线，标题与正文的主力；
 *   Newsreader  — 高对比衬线，只在点睛词上用斜体（.accent）；
 *   JetBrains Mono — 标签、序号、日志。
 * 中文走系统字体栈（PingFang SC / 宋体，见 globals.css 的 --font-display / --font-serif），
 * 避免为一套 CJK 字体在静态导出里塞进十几 MB 子集。
 */
const outfit = Outfit({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-outfit",
  display: "swap",
});

const newsreader = Newsreader({
  subsets: ["latin"],
  weight: ["300", "400", "500"],
  style: ["normal", "italic"],
  variable: "--font-newsreader",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

const TITLE = "AgentCore — 协作智能平台";
const DESCRIPTION =
  "AI 的下一步，不是更聪明的个体，而是更好的协作。AgentCore 让多个 AI Agent 像团队一样分工、协商、互审，共同完成复杂任务——你不是使用者，而是领导者。";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  keywords: [
    "AgentCore",
    "协作智能",
    "Multi-Agent",
    "多 Agent 协作",
    "AI 工作台",
    "Collaborative Intelligence",
  ],
  openGraph: {
    title: TITLE,
    description: DESCRIPTION,
    type: "website",
    locale: "zh_CN",
    siteName: "AgentCore",
  },
  twitter: {
    card: "summary_large_image",
    title: TITLE,
    description: DESCRIPTION,
  },
};

export const viewport: Viewport = {
  /* 与 globals.css 的 --background 对齐（靛青画布 oklch(0.155 0.018 265)）。 */
  themeColor: "#080c14",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="zh-CN"
      className={`${outfit.variable} ${newsreader.variable} ${jetbrainsMono.variable}`}
    >
      <body>
        <LangProvider>{children}</LangProvider>
      </body>
    </html>
  );
}
