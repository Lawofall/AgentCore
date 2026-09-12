# scripts/archive — 维护者真跑探针

不进生产门禁；本机对着跑着的后端 / 平台凭据复跑。从 `apps/server`：

```bash
uv run python scripts/archive/<script>.py
```

下面是**常驻入口**（文档、dogfood、CI 或产品报错会点名）。一次性 bench / measure 已清掉，别往这里堆 `_tmp_*`。

| 脚本 | 干什么 |
|------|--------|
| `probe_turn.py` | 发一条真回合，dump SSE 到达顺序 + 折叠时间线（与桌面同路 HTTP） |
| `probe_multiend.py` | 两条真 SSE（桌面 + 手机 `follow=true`）断言帧到没到对端 |
| `probe_egress.py` | DNS / SSRF / `web_fetch` 出网；认 Clash fake-IP |
| `probe_routing_think.py` | CEO 是否/如何委派 + 第一动前思考打转 |
| `probe_delivery_steer_live.py` | 同对话再发：插话 / 排队 / 缺 delivery |
| `probe_resume_memory.py` | ask_user 挂起 → 断线 → `/resume` 后项目记忆命中 |
| `probe_code_execution.py` | 代码真跑沙箱；nightly `evals-nightly.yml`（`continue-on-error`） |

凭据默认走 dogfood（`local-llm-dogfood.mdc`），不要填生产池 Key。
