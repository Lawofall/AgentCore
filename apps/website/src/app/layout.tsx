import type { Metadata, Viewport } from "next";
import { JetBrains_Mono, Space_Grotesk } from "next/font/google";
import LangProvider from "@/components/LangProvider";
import "./globals.css";

/*
 * 只自托管两款拉丁字体：Space Grotesk（标题/正文）与 JetBrains Mono（标签/日志）。
 * 中文走系统字体栈（PingFang SC / 微软雅黑，见 globals.css 的 --font-display），
 * 避免为一套 CJK 字体在静态导出里塞进十几 MB 子集。
 */
const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-space-grotesk",
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
  themeColor: "#08090c",
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
      className={`${spaceGrotesk.variable} ${jetbrainsMono.variable}`}
    >
      <body>
        <LangProvider>{children}</LangProvider>
      </body>
    </html>
  );
}
