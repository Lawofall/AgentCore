import type { Metadata } from "next";
import NotFoundView from "@/components/NotFoundView";

export const metadata: Metadata = {
  title: "页面不存在 — AgentCore",
  robots: { index: false, follow: true },
};

export default function NotFound() {
  return <NotFoundView />;
}
