"""SMTP 发信自检 — 配好 ``.env`` 的 ``SMTP_*`` 后真发一封验证码样式邮件。

从 ``apps/server`` 运行::

    uv run python scripts/verify_smtp.py user@example.com

未配置 ``SMTP_HOST`` 或 ``SMTP_FROM_ADDRESS`` 时明确报错退出（退出码非 0），
**不会**回落到 ``ConsoleEmailSender``。成功时打印发信地址、收件地址、TLS 模式与耗时。

失败时输出底层 SMTP / 网络异常（认证失败、连不上主机、TLS 模式不匹配等），
不吞成一句「发送失败」。
"""

from __future__ import annotations

import argparse
import asyncio
import smtplib
import sys
import time

from agentcore.config import settings
from agentcore.mail.sender import (
    PURPOSE_EMAIL_VERIFY,
    EmailSendError,
)
from agentcore.mail.smtp import SmtpEmailSender

_PROBE_CODE = "888888"


def _missing_smtp_fields() -> list[str]:
    missing: list[str] = []
    if not settings.smtp_host.strip():
        missing.append("SMTP_HOST")
    if not settings.smtp_from_address.strip():
        missing.append("SMTP_FROM_ADDRESS")
    return missing


def _describe_failure(exc: BaseException) -> str:
    """``smtplib.SMTPException`` 继承 ``OSError``，SMTP 分支必须排在 OSError 之前。"""
    host = settings.smtp_host.strip()
    port = settings.smtp_port
    tls_mode = settings.smtp_tls_mode
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return f"SMTP 认证失败（检查 SMTP_USERNAME / SMTP_PASSWORD）: {exc}"
    if isinstance(exc, smtplib.SMTPConnectError):
        return (
            f"SMTP 握手失败（{host}:{port}，TLS={tls_mode}；"
            f"ssl 用 465、starttls 常用 587）: {exc}"
        )
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return (
            f"SMTP 连接被对端断开（常见原因：TLS 模式与端口不匹配，"
            f"当前 TLS={tls_mode} port={port}）: {exc}"
        )
    if isinstance(exc, smtplib.SMTPException):
        return f"SMTP 协议错误（TLS={tls_mode}）: {exc}"
    if isinstance(exc, TimeoutError):
        return f"连接 SMTP 超时（{host}:{port}，TLS={tls_mode}）: {exc}"
    if isinstance(exc, ConnectionRefusedError):
        return f"SMTP 主机拒绝连接（{host}:{port}，检查主机/端口是否可达）: {exc}"
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in {110, 111}:
        return f"无法连接 SMTP 主机（{host}:{port}）: {exc}"
    if isinstance(exc, OSError):
        return f"网络/OS 错误（{host}:{port}）: {exc}"
    return f"{type(exc).__name__}: {exc}"


def _require_smtp_configured() -> None:
    missing = _missing_smtp_fields()
    if missing:
        print(
            f"SMTP 未配置（缺少: {', '.join(missing)}）。"
            "请在 apps/server/.env 中设置 SMTP_HOST 与 SMTP_FROM_ADDRESS。",
            file=sys.stderr,
        )
        print(
            "本脚本用于验证真实 SMTP 通路，不会使用 ConsoleEmailSender。",
            file=sys.stderr,
        )
        raise SystemExit(1)


async def _send_probe(to: str) -> float:
    sender = SmtpEmailSender()
    started = time.perf_counter()
    await sender.send_verification_code(
        to=to,
        purpose=PURPOSE_EMAIL_VERIFY,
        code=_PROBE_CODE,
        ttl_seconds=settings.email_code_ttl_seconds,
    )
    return time.perf_counter() - started


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SMTP 发信自检：经 agentcore.mail 真发一封验证码样式邮件",
    )
    parser.add_argument(
        "to",
        help="收件邮箱地址（由命令行传入，不写死）",
    )
    args = parser.parse_args()
    to = args.to.strip()
    if not to or "@" not in to:
        print(f"无效的收件地址: {args.to!r}", file=sys.stderr)
        raise SystemExit(2)

    _require_smtp_configured()

    from_addr = settings.smtp_from_address.strip()
    tls_mode = settings.smtp_tls_mode
    host = settings.smtp_host.strip()
    port = settings.smtp_port

    print("SMTP 自检配置:")
    print(f"  from:     {from_addr} ({settings.smtp_from_name})")
    print(f"  to:       {to}")
    print(f"  host:     {host}:{port}")
    print(f"  tls_mode: {tls_mode}")
    print(f"  username: {settings.smtp_username.strip() or '(empty)'}")
    print()

    try:
        elapsed = asyncio.run(_send_probe(to))
    except EmailSendError as exc:
        cause = exc.__cause__
        if cause is not None:
            print(_describe_failure(cause), file=sys.stderr)
            print(f"底层异常: {type(cause).__name__}: {cause}", file=sys.stderr)
        else:
            print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc

    print("发送成功")
    print(f"  from:     {from_addr}")
    print(f"  to:       {to}")
    print(f"  tls_mode: {tls_mode}")
    print(f"  elapsed:  {elapsed:.3f}s")
    print(f"  probe_code: {_PROBE_CODE}（验证码样式自检，请查收件箱）")


if __name__ == "__main__":
    main()
