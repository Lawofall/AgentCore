#!/usr/bin/env node
/**
 * 手机 Web SPA 已退役。m.example.com 将 301 到 app.example.com。
 * 禁止再构建或上传 stub；线上 DNS 不由本脚本拆改。
 *
 *   pnpm -C apps/mobile deploy:pages
 */
console.error(
  "手机 Web 已退役：m.example.com 将 301 到 app.example.com。禁止再部署 stub。",
);
process.exit(1);
