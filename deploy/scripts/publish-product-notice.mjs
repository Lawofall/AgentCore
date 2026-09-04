#!/usr/bin/env node
/**
 * Publish a product notice on production (Banner + IM 官方号).
 *
 *   pnpm publish:notice -- --title "…" --body "…" [--severity high] [--surface both|modal]
 *   [--dismiss once] [--end-hours N|none] [--body-file path] [--title-file path]
 *   [--cta-label "…"] [--cta-url "https://…"]
 *   [--card-template service|article] [--summary "…"] [--cover-url "https://…"]
 *
 * Uses DEPLOY_SSH_* from deploy/.env.deploy.local. Runs create+publish inside
 * the live api container (no admin password needed). Template copy →
 * docs/05-平台与运维/产品公告文案模板.md
 * Surfaces ``inbox`` / ``both`` / ``modal`` also write IM 官方号 on first publish.
 *
 * ``--end-hours``: omit or ``none`` / ``0`` = no expiry (policy/modal). Positive N =
 * hours from now (hotfixes historically used 2).
 */
import { readFileSync } from "node:fs";
import { loadDeployEnv, sshScript } from "./load-deploy-env.mjs";

loadDeployEnv();

function arg(name, fallback = "") {
  const i = process.argv.indexOf(`--${name}`);
  if (i === -1 || i + 1 >= process.argv.length) return fallback;
  return process.argv[i + 1];
}

const titleFile = arg("title-file").trim();
const title = (titleFile ? readFileSync(titleFile, "utf8") : arg("title")).trim();
const bodyFile = arg("body-file").trim();
const body = (bodyFile ? readFileSync(bodyFile, "utf8") : arg("body")).trim();
const severity = arg("severity", "high").trim() || "high";
const surface = arg("surface", "both").trim() || "both";
const dismiss = arg("dismiss", "once").trim() || "once";
const endHoursRaw = arg("end-hours", surface === "modal" ? "none" : "2").trim();
const endHours =
  endHoursRaw === "" || endHoursRaw === "none" || endHoursRaw === "0"
    ? null
    : Number(endHoursRaw);
const ctaLabel = arg("cta-label").trim();
const ctaUrl = arg("cta-url").trim();
const cardTemplate = arg("card-template", "service").trim() || "service";
const summary = arg("summary").trim();
const coverUrl = arg("cover-url").trim();

if (!title || !body) {
  console.error(
    'usage: pnpm publish:notice -- --title "…" --body "…" [--surface both|modal] [--end-hours N|none] [--body-file path] [--title-file path] [--cta-label "…"] [--cta-url "https://…"] [--card-template service|article] [--summary "…"] [--cover-url "https://…"]',
  );
  process.exit(1);
}
if (endHours != null && (!Number.isFinite(endHours) || endHours <= 0)) {
  console.error("--end-hours must be a positive number, or none/0");
  process.exit(1);
}
if ((ctaLabel && !ctaUrl) || (!ctaLabel && ctaUrl)) {
  console.error("--cta-label and --cta-url must be set together");
  process.exit(1);
}
if (cardTemplate !== "service" && cardTemplate !== "article") {
  console.error("--card-template must be service or article");
  process.exit(1);
}
if (cardTemplate === "article" && !summary) {
  console.error("--summary is required when --card-template=article");
  process.exit(1);
}

// Escape for embedding in a single-quoted remote Python string via JSON.
const payload = JSON.stringify({
  title,
  body,
  severity,
  surface,
  dismiss,
  end_hours: endHours,
  cta_label: ctaLabel || null,
  cta_url: ctaUrl || null,
  card_template: cardTemplate,
  summary: summary || null,
  cover_url: coverUrl || null,
});

const deployDir = process.env.AGENTCORE_DEPLOY_DIR?.trim() || "";
const deployDirExport = deployDir
  ? `export AGENTCORE_DEPLOY_DIR=${JSON.stringify(deployDir)}\n`
  : "";

const remote = `set -euo pipefail
${deployDirExport}HOME_DIR="\${AGENTCORE_HOME:-/opt/agentcore}"
DEPLOY_DIR="\${AGENTCORE_DEPLOY_DIR:-\$HOME_DIR/repo/deploy}"
cd "\$DEPLOY_DIR"
echo "==> publish product notice via agentcore-api"
docker exec -i agentcore-api python - <<'PY'
import asyncio, json
from datetime import UTC, datetime, timedelta
from sqlalchemy import select

from agentcore.db.base import async_session_factory
from agentcore.db.models import User
from agentcore.db.repositories import (
    ChatRepository,
    FolderMemberRepository,
    ProductNoticeRepository,
    UserBlockRepository,
    UserDirectoryRepository,
    UserRepository,
)
from agentcore.messaging import MessagingService
from agentcore.messaging.hub import HubChatEventPublisher, default_chat_hub

SPEC = json.loads(${JSON.stringify(payload)})

async def main() -> None:
    async with async_session_factory() as session:
        admin = (
            await session.execute(
                select(User).where(User.role == "admin", User.deleted_at.is_(None)).limit(1)
            )
        ).scalar_one_or_none()
        if admin is None:
            raise SystemExit("no admin user for created_by")
        repo = ProductNoticeRepository(session)
        end_hours = SPEC.get("end_hours")
        end_at = (
            datetime.now(UTC) + timedelta(hours=float(end_hours))
            if end_hours is not None
            else None
        )
        row = await repo.create(
            title=SPEC["title"],
            body=SPEC["body"],
            severity=SPEC["severity"],
            surface=SPEC["surface"],
            dismiss_policy=SPEC["dismiss"],
            created_by=str(admin.user_id),
            end_at=end_at,
            card_template=SPEC.get("card_template") or "service",
            summary=SPEC.get("summary"),
            cover_url=SPEC.get("cover_url"),
            cta_label=SPEC.get("cta_label"),
            cta_url=SPEC.get("cta_url"),
        )
        first = row.status != "published"
        published = await repo.publish(row.id)
        if published is None:
            raise SystemExit("publish failed")
        if first and published.surface in ("inbox", "both", "modal"):
            messaging = MessagingService(
                users=UserRepository(session),
                chats=ChatRepository(session),
                blocks=UserBlockRepository(session),
                directory=UserDirectoryRepository(session),
                events=HubChatEventPublisher(default_chat_hub()),
                folder_members=FolderMemberRepository(session),
            )
            await messaging.publish_product_notice(
                notice_id=published.id,
                title=published.title,
                body=published.body,
                severity=published.severity,
                surface=published.surface,
                card_template=published.card_template or "service",
                summary=published.summary,
                cover_url=published.cover_url,
                cta_label=published.cta_label,
                cta_url=published.cta_url,
            )
        print(json.dumps({"id": published.id, "status": published.status, "title": published.title}))

asyncio.run(main())
PY
`;

sshScript(remote);
console.log("notice published");
