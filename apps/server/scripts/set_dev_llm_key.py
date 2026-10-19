"""设置（或替换）dev 账号的 BYOK DeepSeek provider —— 走正规登录 + `/users/me/llm-providers`。

为什么需要它：DeepSeek 是 BYOK（每用户自带多服务商、AES 加密落库，见 ``llm/resolve.py``），dev
账号发回合前必须先有一个能被**当前后端**解密的 provider，否则 preflight 直接 ``402 LLM_KEY_REQUIRED``。
后端的 ``ENCRYPTION_KEY`` 一旦变更（如曾靠启动 shell 注入临时值、重启即换），已存密文就解不开
——本脚本用**当前后端**的 key 重新加密落库，一行修复，重启后照跑。

从 ``apps/server`` 跑（key 经 ``--key`` 或 ``DEV_DEEPSEEK_KEY`` 传入，绝不硬编码进仓库）::

    $env:DEV_DEEPSEEK_KEY="sk-..."; uv run python scripts/set_dev_llm_key.py
    uv run python scripts/set_dev_llm_key.py --key sk-...        # 或直接给参数
    uv run python scripts/set_dev_llm_key.py --user dev --password devpassword --key sk-...

凭据 / 地址默认同 ``probe_turn.py``（``dev`` / ``devpassword``、``http://localhost:8000``），可用
``DEV_USERNAME`` / ``DEV_PASSWORD`` / ``PROBE_BASE_URL`` 或对应参数覆盖。仅 dev 便利工具，无旁路。

成功后 ``test`` 应回 ``active``；若回 ``KEY_STORAGE_UNAVAILABLE`` 说明后端没配 ``ENCRYPTION_KEY``
（把它写进 ``apps/server/.env`` 再重启后端），若回 ``无法解密`` 说明后端 key 与落库时不一致。
"""

from __future__ import annotations

import argparse
import asyncio
import os

import httpx

DEFAULT_BASE_URL = os.environ.get("PROBE_BASE_URL", "http://localhost:8000")
DEFAULT_USERNAME = os.environ.get("DEV_USERNAME", "dev")
DEFAULT_PASSWORD = os.environ.get("DEV_PASSWORD", "devpassword")


async def _login(client: httpx.AsyncClient, base_url: str, user: str, pw: str) -> str:
    r = await client.post(
        f"{base_url}/v1/auth/token",
        json={"username": user, "password": pw},
        headers={"X-Client-Platform": "desktop"},
    )
    if r.status_code == 401:
        raise SystemExit(
            "登录失败 (401)。先建 dev 账号：uv run python scripts/seed_dev_user.py"
            f"\n或用 --user/--password 指定一个已存在账号 (当前试的是 {user!r})。"
        )
    r.raise_for_status()
    return r.json()["access_token"]


async def run(args: argparse.Namespace) -> None:
    key = (args.key or os.environ.get("DEV_DEEPSEEK_KEY") or "").strip()
    if not key:
        raise SystemExit(
            "未提供 DeepSeek key。用 --key sk-... 或设 DEV_DEEPSEEK_KEY 环境变量。"
        )
    base_url = args.base_url.rstrip("/")
    base = f"{base_url}/v1/users/me/llm-providers"
    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await _login(client, base_url, args.user, args.password)
        headers = {"Authorization": f"Bearer {token}"}

        # Set-or-replace: reuse the first provider if one exists (keeps this idempotent),
        # else add a new one — the first provider auto-becomes the account chat default.
        listing = await client.get(base, headers=headers)
        listing.raise_for_status()
        providers = listing.json().get("providers", [])
        if providers:
            pid = providers[0]["id"]
            resp = await client.patch(f"{base}/{pid}", headers=headers, json={"api_key": key})
        else:
            resp = await client.post(
                base,
                headers=headers,
                json={"label": "DeepSeek", "api_key": key, "vendor_id": "deepseek"},
            )
        if resp.status_code not in (200, 201):
            raise SystemExit(f"保存失败 {resp.status_code}: {resp.text}")
        pj = resp.json()
        pid = pj["id"]
        print(f"saved: provider={pid} status={pj['status']} masked={pj['masked_key']}")

        test = await client.post(f"{base}/{pid}/test", headers=headers)
        if test.status_code != 200:
            raise SystemExit(f"连通性测试请求失败 {test.status_code}: {test.text}")
        tj = test.json()
        print(f"test:  status={tj['status']} masked={tj['masked_key']} msg={tj.get('message')}")
        if tj["status"] != "active":
            raise SystemExit("连通性测试未通过——按上面的 msg 排查（密钥/余额/服务端加密配置）。")
    print("\nOK：dev 账号已可用 DeepSeek。现在可跑 scripts/archive/probe_turn.py 真发一条回合。")


def main() -> None:
    parser = argparse.ArgumentParser(description="设置 dev 账号的 BYOK DeepSeek key 并测连通")
    parser.add_argument("--key", default=None, help="DeepSeek API key（或用 DEV_DEEPSEEK_KEY 环境变量）")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--user", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
