import type { Metadata } from "next";
import DownloadView from "@/components/DownloadView";

export const metadata: Metadata = {
  title: "下载 AgentCore 桌面客户端 — 协作智能平台",
  description:
    "下载 AgentCore 桌面客户端 for Windows 与 macOS（Apple Silicon），以及 Android APK。Multi-Agent 协作工作台，自动更新。",
};

export default function DownloadPage() {
  return <DownloadView />;
}
