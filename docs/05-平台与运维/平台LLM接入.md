---
status: reference
code: apps/server/agentcore/llm/
related:
  - docs/05-平台与运维/成本配额与计费.md
  - docs/03-AI核心/辩论编排设计.md
skip_if:
  - 不涉及 LLM 上游 / 模型解析 / BYOK
---

# 平台 LLM 接入

> **现状**：目标仍是 `billing_mode=platform`（额度 `quota_*` · 5 积分/月·5 积分/日 · 台账 **nano 卖价-CNY**，无 live FX）；**现网可临时 `byok`**（见 [成本配额与计费](/docs/05-平台与运维/成本配额与计费.md) 文首）。**dev 默认仍 BYOK**；本地要和线上同构 → [本地开发 · 两条起步路](/docs/02-架构/本地开发.md#两条起步路)。本文只记上游接入事实（厂商坑、BYOK 去向、platform 排查）。

## 一、三条上游路径

| 路径 | 何时走 | 上游 |
|---|---|---|
| **BYOK 直连** | 用户配了 OpenAI 兼容服务商 | 用户自带端点（多服务商；典型 DeepSeek） |
| **多厂商 provider 路由** | model 串带 `厂商/` 前缀 | 豆包 / Moonshot / 智谱 等（§四） |
| **platform 平台凭据** | `billing_mode=platform` / 显式 platform | `PLATFORM_*` 三项 |

**BYOK 去向**：每用户多服务商列表（`user_llm_providers`：AES-GCM 密文 key + base_url + 独立的 `vendor_id`）；账号选的是**装配上的模型列**（`assemblies` 的 main / worker；`background` / `vision` 列仍在、人不再编，每列解析为目录身份 `@platform/{id}` / `@byok/{provider_id}/{id}`，库内仍存 `(model, origin, provider_id)`）。服务商表单不收模型 id。测连只认 `GET /models`（合法 JSON 且非空即连通）；空列表 / 无该接口 → 诚实「未列出，可在输入框手填」，**不**用猜测的 chat 模型刷绿灯。响应像网页而非 JSON → 说地址不是 API 根，不与「未列出」混为一谈。已有装配的模型列指向该服务商时，才对这些 id 做 `POST /chat/completions` 探针（火山 `ep-…` 等目录未列出的 id）。目录已成功列出后槽位探针仍 401/403（非余额）→ 点名该模型不被上游接受，**禁止**说 Key 无效；目录未证明 Key 时同时请核 Key 与该模型。第一个服务商且保存的 `vendor_id` 命中厂商预设时，用代码里的预设种子写进当时账号回落装配的主模型，名字仍是那份装配的名字；自定义厂商不改装配上的模型。连通≠聊天就绪写在服务商列表页脚（并指向输入框选模型）。用户填的自定义地址若无路径（中转控制台常只给域名），保存与出站都当成 `{源站}/v1`；已有路径与厂商预设官方地址原样。字段 hint 说明这条，不进测连成功徽章。key **不在 `.env`**。BYOK 且无服务商、又无 platform 回退 → `402 LLM_KEY_REQUIRED`。

## 二、模型与凭据解析

人侧默认模型列在 [装配](/docs/03-AI核心/工具与能力系统.md#装配) 上；输入框可给这场写覆盖。本节写列的解析、拍快照、思考强度、凭据解析；会话钉 `assembly_id`，模型覆盖另钉在这场。哪一列算装进装配，以装配文为准。

**装配上的模型列**：读写在 `/v1/users/me/assemblies`（**新建拍快照**：create 写入客户端所选或账号回落的装配；预置先落成该用户自己的一行再存）。人选一份时客户端记下 last-used 并写入账号回落（`PUT …/assemblies/default`），人侧不露星。输入框的模型芯片写这场的 `model_override`（完整快照：`main` / `worker` / `reasoning_effort` / `context_budget` 四键都在），并 `PUT /v1/users/me/models/composer` 记账号上次选用；新对话从目录 `composer_model` 起，空则跟能力默认列。能力芯片只换这份能力，不把模型一起换掉。可选 `reasoning_effort` / `context_budget`：厂商官方 token 与窗口档；快照里 null = 该主模型默认 / 窗口本身，不是跟能力列。账号另存 `composer_model_knobs`（按目录身份），换模型恢复该叶上次档，不把上一叶带走；没记过则厂商默认。这场还没有覆盖行 = 跟能力。**元数据事实源** = `llm/catalog.py`（上架集）+ `llm/model_metadata.py`（展示 enrichment）。旧的 `platform-preset:{model_id}` 不再列入名单；该模型仍可见、且账号回落还是这个 id 时，回落改到「标准」，并把该模型写进落成行的主模型。从标准复制新建时，模型抄当时可见的平台默认。配方细节 → [工具与能力 · 装配](/docs/03-AI核心/工具与能力系统.md#装配)。明确不做：质量档矩阵、账号级角色→模型矩阵、「跟随账号默认」行、再立一份命名模型组合表。输入区是模型芯片 + 能力芯片。✅ **Per-worker 节点显式覆盖**（执行链 + sidecar proxy；确认面不提供人改模）与组合槽正交 → [编排器 · Per-worker 模型覆盖](/docs/03-AI核心/编排器与CEO主Agent.md#per-worker-模型覆盖abc-同一功能)。

**识图槽 `vision`（可选，已停用）**：组合列与 API 仍保留该槽，设置页不再编辑。图只进**当前主力**原生多模态（贴图挂当前 user；工作区光栅走 `read`）。主模型不收图 → 诚实说明，不另开眼睛轨。推理代理仍硬拒 `X-AgentCore-Role: vision`。历史 `role=vision` 入账行仍可折账。visual critic **已退役**。能力位只认该厂商契约（精确 id + 进程内负例），不是展示元数据家族继承、也不是 id 关键词。

**对话贴图路由**：main 收图（`llm/image_accept.model_accepts_images`）→ 原生 multimodal（`image_url` 挂当前 user）；否则诚实「当前主模型不收图」，不静默丢像素、不另开眼睛轨。工作区图片同一条：`read` 把像素发给这只模型。入模前压成 JPEG（长边 1568 / 扫描件 2048），不走文本 5 MiB 闸、不改盘上原件；权威 → [工具与能力 · 工作区识图](/docs/03-AI核心/工具与能力系统.md)。

`llm/resolve.py` 单点：

- **主对话**：用户 key 优先；无 key 才 platform。
- **长对话压缩**（连续性，不是 chrome）：跟 **这条对话** 的聊天付款方走（会话钉住的组合）。平台对话 → 平台 key + `enforce_quota`；自带 Key 对话 → 用户 key，**不**吃平台 ¥ 帽。入口 `billing/gate.py::run_compaction_llm`。回合后压缩仍 skip（不 429 刚结束的回合）；近顶折不进去 → 拒发。组合 background 槽不参与。
- **后台档**（title）：人不可选。代付开着 → 钉 `PLATFORM_BACKGROUND_MODEL`（默认 `deepseek-v4.1-flash`）+ 必过 `enforce_quota`。代付关掉 / 凭据缺失 → 跟随这场主模型（没有对话 id 时读账号星标）。组合 background 槽不读。平台不可用（上游 auth 拒绝 / 余额不足 / 订阅因付款方式停了）才回落用户 BYOK（跟主模型，不套 `PLATFORM_BACKGROUND_MODEL`）。统一入口 `billing/gate.py::run_background_llm`（可带 `conversation_id`；`resolve_and_gate_background` 解析 + 一次 auth 或余额→BYOK；耗尽 / 两边都失败 → skip，不 429 主回合、不对用户弹出「请改用自己的 API Key」）。标题生成必须把鉴权失败和余额不足重新抛出，这一次回落才看得到。禁止调用点各自 try/except 拼回落、禁止进程内 auth 熔断缓存。原 followups（「下一步」chips）已下线，不再走后台档。
- **回合内鉴权死短路（甲+乙）**：同一用户回合、同一付款方（`credential_source`）首次确认真 API Key `LLMAuthError`（不含 `INFERENCE_TOKEN_EXPIRED`）或余额不足后，`llm/turn_auth_dead.py` 按来源闩死后续未启动的同源 LLM（主聊后续轮 / 未开跑 worker / 本回合 chrome）；另一付款方不受影响（平台 chrome 死亡不得短路同回合 BYOK chat，反之亦然）。已在飞可自然失败。**不做**跨回合 TTL 负缓存（丙暂缓）。用户文案 / CTA 按 `credential_source` 分流（BYOK→去设置；平台→改用自己的 Key / 联系管理员）。
- **`platform_billing_selectable`**：仅 `billing_mode=platform` 时可选；BYOK 部署不开放平台代付。
- **Worker 槽**：空 = 跟随主模型；跨 origin 时 `build_turn_router` 注入 extras。Sidecar `cost_role=member`：请求 body `model` 为目录路由键（`platform/{id}` / `{provider_id}/{id}`）且合法 → **按该身份重解析凭据/model**；裸 mint/chat id 或未带显式 → 仍跟本槽。非法路由键 **硬失败**（`VALIDATION_ERROR`），禁 silent 回退野模型。→ [编排器 · Per-worker](/docs/03-AI核心/编排器与CEO主Agent.md#per-worker-模型覆盖abc-同一功能)。
- **统一目录** `GET /v1/users/me/models`：产品身份 = `ref`（`@platform/{id}` / `@byok/{provider_id}/{id}`）；行属性仍带 `(id, origin, provider_id)` 供分组。BYOK 行 = 按 `base_url` 匹配的厂商预设 seeds ∪ 上游 `GET /models` 发现（发现失败/空仍保留预设）；匹配到预设后按 `hideFromPicker` **精确 id** 对新选隐藏（退役官方别名，或该端点不得使用的官方 id；已钉组合仍能跑）。无预设（自定义端点）只并发现结果；发现失败则目录为空，**手填** model id。**不是**用前端硬编码清单取代发现。组合槽对 BYOK 始终可手填 model id（目录空时主体就是输入框；目录非空从设置 · 模型该渠道节底「使用其他模型」进入；火山 `ep-…`、私有中转漏列的 id）。筛选无命中不把输入框铺进列表。手填 id 写入 `enabled_refs`，目录合成 `custom` 行，设置 · 模型可关开。弹层平铺目录行；有补贴时含 `origin=platform` 行，无补贴不出现。账号可在设置 · 模型按渠道关开哪些行进弹层（平台 `hidden_refs` 默认开，接入服务商 `enabled_refs` 默认关；不改推理）。接入服务商不会把上游 `GET /models` 倒进输入框弹层。遗留已下架平台槽标需改选。目录 API 仍只在有补贴时返回 `origin=platform` 行。

## 三、sidecar 推理代理

桌面本地引擎**不拿 BYOK key**——经服务端出网：`POST /v1/inference/token` 铸 scoped token + 服务端解析 `model`；`POST /v1/inference/v1/chat/completions` 过同一道计费闸后转发。模型以服务端解析为准。`X-AgentCore-Role: vision` **已退役**（硬失败）。→ `api/routes/inference/`；整体 → [工作区](/docs/02-架构/工作区.md)。

**铸票 `token.model`**：可选 body `{ conversation_id? }`。有合法且属该用户的会话 → 与代理主槽同源 expand（`resolve_conversation_model_selection(...).model`，会话钉组合优先）；缺省 / 会话不存在或不属于该用户 → 账号默认（`resolve_user_chat_model`）。JWT **只绑 user**，不把 `conversation_id` 塞进 claims；返回的 model id 诚实透传（禁 silent 把 `flash-free` 糊成 `flash`）。

**令牌 TTL**：默认 `inference_token_expire_minutes=720`（12h）。桌面按**会话**缓存复用，仅临近过期（skew 1min）/ 换会话 / 显式 force 才重铸；开跑前若代理拒票则清缓存换票再 RPC 一次。代理 401/403 映射为 `INFERENCE_TOKEN_EXPIRED`（可重试、勿引导「去设置 · 服务商」），与 BYOK 的 `LLM_KEY_INVALID` 区分。

**错误契约 = 信封优先**：代理把所有类型化错误压平到 402 / 429 / 502，故本机叶子先按 AgentCore 错误信封的 `code` 还原错误类型（`llm/errors.py::inference_envelope_error`），厂商状态码启发式退为**无信封时**的回落（网关页、裸 401 拒票）。解析信封必须吃**完整 body**，禁止用日志 `body_preview`（500 字）当 JSON 输入——截断后叶子会把厂商 530 说成 AgentCore 挂了。裸 HTTP 530 无我方故障信封时按「你选的模型暂时不可用」，不按我方故障。按状态码判会三重误译：额度用尽读成上游限流（白等一分钟重试预算）、未配 Key 读成余额不足（引导去充值）、502 上的 `LLM_KEY_INVALID` 压成 `LLM_ERROR`（§二「回合内鉴权死短路」不触发，扇出里每个 worker 各撞一遍坏 key）。信封只认客户端会分流的码（key-config CTA / 重试可用性 / 换票），其余仍走状态码路径。**否决**：在叶子里按状态码逐个加 `/inference/` 特例分支。**直连厂商路径不变**——那条路上厂商状态码仍是唯一信息源。

**无票 = 不开跑（无本机平台模型回退）**：铸不出票时先 force 换一次；仍无票则以 `INFERENCE_TOKEN_EXPIRED` 诚实失败、**不发** `startTurn` / `resume`。sidecar 对空凭据在两个 RPC 入口同样早拒——调用方不止当前版本桌面（旧桌面、探活拉起的长活进程都可能无票接单），服务层是文案统一的唯一保证；`build_turn_router` 硬拒空凭据是最终兜底。**已删除的承诺**：「无票回落 sidecar 自身 `.env` 平台模型」——平台 key 不下放本机，dev 亦走 BYOK（见 `local-llm-dogfood.mdc`）；该承诺失效后曾把引擎内部英文异常当文案抛给线上用户。**否决**：无票时降级回云端链路——本机工作区回合的云端链路同样依赖 workspace channel，二者常一并不可用。

## 四、多厂商 provider 路由

`provider/model` 前缀 → `ProviderRouter`（空 key = 不注册）。辩论多凭据 → [辩论编排 §7.5](/docs/03-AI核心/辩论编排设计.md)。

- 带前缀 → 厂商；无前缀 → 默认 DeepSeek BYOK；未注册前缀 → 回退默认、模型名透传。
- **火山方舟**：一把 `ark-…` key + `https://ark.cn-beijing.volces.com/api/v3`；model 必须传**接入点 ID（`ep-…`）或已开通模型 ID**。BYOK 预设种子为 `doubao-seed-2-1-turbo-260628`，旧 `doubao-pro-32k` / `doubao-lite-32k` 裸名不可用。
- **兼容性铁律**：只发标准 OpenAI 字段，不发 DeepSeek 特有 `thinking` 等（别家网关会 400）。

## 四·附、DeepSeek API 易错约束（BYOK 常用）

官方文档：https://api-docs.deepseek.com。产品路由 / 计费仍以上文为准。以下为**外部 API 约束**（代码里看不出来）：

| 项 | 约束 |
|---|---|
| 模型名 | 官方现行 `deepseek-flash`（V4.1 Flash）。旧名 `deepseek-v4-flash` / `deepseek-v4-flash-vision-exp` / `deepseek-v4-pro` 仍可能被上游接受，产品 **hideFromPicker**（官方 / Go / Zen 对新选隐藏；已钉组合仍能跑）。`deepseek-chat` / `deepseek-reasoner` 已停用。官方 id 与 Go/Zen 识图契约不同：Go / Zen 亦隐藏 `deepseek-flash`。产品计价走 DeepSeek 中文官价，不跟中转额度倍数 |
| 识图 | 官方 `deepseek-flash` 收图。产品契约：`deepseek-v4-flash` / `deepseek-v4.1-flash` / Pro 文本 id 不收（Go 仍把 Vision Exp 列成独立 SKU）；旧 `deepseek-v4-flash-vision-exp` 仍收 |
| 上下文 | 官方 **1M**（input+output 合计）；max output 384K。目录 `context_length` 仍是这条，不跟过期的 128K 记忆。装配可以把近顶和窗口环用的上限调低，目录窗仍是 1M |
| base_url | `https://api.deepseek.com`（兼容 `/v1`） |
| 思考开关 | `extra_body.thinking.type=enabled/disabled`。官方省略 = 默认 enabled；**AgentCore 聊天/CEO/worker 显式发 enabled**，DeepSeek V4 同时发 `reasoning_effort`（组合未设则官方默认 `high`）。档位来自方言表官方 token，工具箱·模型暴露；别名不上 UI。OpenCode Go 省略 `thinking` 时思考 token=0；只发 `thinking.enabled` 仍可能不回 CoT |
| 温度坑 | **思考模式下** `temperature`/`top_p`/penalty **静默忽略** |
| 工具调用 | 有 tool call 的回合必须原样回传 `reasoning_content`，否则 400 |
| 其它 | 不支持强制 `tool_choice=required`（probe 遇 400 回退）；无 `developer` role |

**思考开关按角色**：CEO / worker / 单聊 = 出站写 `thinking.type=enabled`（不省略）；后台 one-shot（title/memory/compaction/file.rewrite）= `disabled`。无 per-agent 思考强度档。组合级一档（厂商 token）覆盖聊天与组队队员；叶方言不发该字段则跳过。

## 四·附、Moonshot / Kimi 易错约束（BYOK 常用）

官方：[Model Parameter Reference](https://platform.kimi.ai/docs/api/models-overview)。与 DeepSeek「思考模式下 temperature **静默忽略**」不同——Kimi 当前代对采样参数是**硬拒**（传错值 → 400）。

| 项 | 约束 |
|---|---|
| 模型名 | 预设种子 `kimi-k2.6` / `kimi-k3` / `kimi-k2.5`；legacy `moonshot-v1-*` 仍可自由采样 |
| base_url | `https://api.moonshot.cn/v1`（别名 `.ai`） |
| 温度坑 | `kimi-*`：**勿显式传** `temperature`（k3 / k2.7-code 固定 1.0；k2.5/k2.6 = thinking 1.0 / non-thinking 0.6）。传 0.7 等 → `invalid temperature: only 1 is allowed` |
| 出站 | `wire_dialect.omit_temperature`：`kimi-` 叶 + Moonshot 预设 base_url（非 `moonshot-v1*`）省略；OpenCode Zen / Go 等多模型端点**不**整端 omit，靠叶规则 |
| 未做 | 不为 Kimi 单开 `thinking.type` / `reasoning_effort` 产品档；不做 400 自适应重试补 temperature |

## 四·附、腾讯 Hy / TokenHub（BYOK 预设）

BYOK 厂商预设 id=`hy`；canonical `https://tokenhub.tencentmaas.com/v1`（广州）；备用 `.cn` 与国际站仅作 base_url 匹配别名。模型目录种子：`hy3`（默认）、`hy3-preview`。

| 项 | 约束 |
|---|---|
| 模型名 | `hy3` / `hy3-preview`（wire 精确匹配；其它 TokenHub `hy-*` 不走思考方言） |
| 思考开关 | 与 DeepSeek 同形：`thinking.type=enabled/disabled`；角色策略同上附 |
| 工具调用 | 有 tool call 的回合必须回传 `reasoning_content` |
| 未做 | 不暴露 `reasoning_effort` UI（Hy3 叶无该字段）；不做 `hy/` 平台前缀路由 |

## 四·附、OpenCode Zen / Go（BYOK 预设 + 可作 platform 上游）

OpenCode 两条 OpenAI 兼容上游，**计费与目录不同，必须按精确 base_url 分预设**（禁止前缀 / 包含匹配：`…/zen/go/v1` 不得吃成 Zen，反之亦然）。预设 `models` 仅短种子（发现失败兜底）；全量目录靠上游 `GET /models` 与现有 BYOK 发现合并。

| | Zen | Go |
|---|---|---|
| 预设 id / label | `opencode_zen` · OpenCode Zen | `opencode_go` · OpenCode Go |
| base_url | `https://opencode.ai/zen/v1` | `https://opencode.ai/zen/go/v1` |
| 计费 | 按量余额（含 `-free` 档） | $10/月订阅配额（5 小时 / 周 / 月）；目录**没有** `-free` |
| 默认模型 | `deepseek-v4.1-flash` | `deepseek-v4.1-flash` |
| 短种子 | V4.1 Flash / `kimi-k2.6` / `glm-5.2` | V4.1 Flash / `glm-5.3` / `glm-5.3-flash` / `kimi-k3` |

| 项 | 约束 |
|---|---|
| 协议 | 默认仍跑 OpenAI `chat/completions`。出站协议跟保存的 **`vendor_id`** 走，不跟 `base_url` 是否等于官方域名。OpenCode 协议按**家族**认（`claude-*`、`qwen3.*-plus/max` → `/messages`；`gpt-` / `grok-` / `muse-*` → `/responses`），精确表 `offProtocolModels` 只留例外（现 `union-alpha`）。挡出种子；目录合并层仍列出这些 id（不静默隐藏）。OpenCode Zen/Go BYOK：`/messages` 与 `/responses` 均可选（云端按模型分叉）。`vendor_id=openai` 的服务商聊天一律 `POST /responses`（GPT-6 工具调用要求这条；gpt-4o 同样能跑），哪怕把 Base URL 改成中转。OpenRouter 与自定义厂商仍走 `chat/completions`。平台 allowlist 对无卡的非 Completions 家族仍灰；已有 CNY 价卡的 Claude / GPT 走 OpenAI 兼容中转，不灰。`minimax-m2.7` 现已走 `chat/completions`，无家族命中。sidecar 推理代理仍只收 OpenAI 形 `chat/completions`；云端按模型分叉到 `/messages` 或 `/responses`。Responses 叶：`store: false`、自管 transcript、不发 `previous_response_id`、工具 `strict: false`、不发官方托管 `web_search`。过滤不在 HTTP discovery：`GET /models` 原样返回（无协议字段）；OpenAI 厂商目录再丢掉 whisper / TTS / embedding 等非聊天 id。带 `provider/` 的 slug 不认家族（OpenRouter `anthropic/…` 仍走 chat/completions）。被区域闸住的 id 仍可能出现在发现结果里——目录有 ≠ 一定能跑 |
| 会话头 | Go/Zen 出站 `POST /chat/completions`、`POST /messages` 与 `POST /responses` 必带稳定 `x-opencode-session`（值=`user:{user_id}`，无 user 时退回对话 id；两者都没有则 `probe:{trace}`）。OpenCode 按这个头隔开磁盘缓存，同一用户的新对话因此能复用已暖前缀。平台池选钥仍按对话钉，不跟这个头。User-Agent=`AgentCore/1.0`。不冒充 `opencode-cli`。`GET /models` 不带 session。缺头上游 400（2026-09-06 起硬拒） |
| BYOK | 用户自备**对应端点**的 key；价卡与平台同一张 curated CNY（`credential_source` 只分流列，不进平台配额）。打错端点时付费 Flash 会在 Zen 路上 `CreditsError`（扣的是 Zen 余额，Go 订阅管不到） |
| 平台代付 | ✅ `PLATFORM_*` 指向部署方配置的 OpenAI 兼容上游（可与用户 BYOK 的 Zen/Go 预设相同端点；**现网绑定不在公开仓**）。默认 Flash，另上 GLM-5.3 / GLM-5.3-Flash / Kimi K3（见 §五·附）。Claude / GPT-6 已有 CNY 价卡，写入 `PLATFORM_MODELS` 即可上架（切流 env 另日）。Grok / Muse / MiniMax / Qwen 仍不上。换上游 / 改 `quota_*` 须改生产 `.env` 并重启 api |
| 上下文 | 按 **SKU id**：付费 `deepseek-v4-flash` / `deepseek-v4.1-flash` **1M**；仅 `deepseek-v4-flash-free` **200K**（Zen 网关 cap）。禁止按端点猜窗（Go 无 free 档也不把 Flash 当成 200K） |
| 错误分类 | 一张按上游嵌套 `error.type` 的表（信封 `{"type":"error","error":{"type":…}}`），禁止扫 `error.message`。**`GoUsageLimitError`（429）= Go 订阅配额用尽**（等窗口或控制台 `Use balance`），不是余额不足；**`CreditsError`（401）**收窄为无支付方式 / 订阅未激活 / 余额空；`MonthlyLimitError` / `UserLimitError` = 工作区月限或成员限；`ModelError` = 模型不支持 / 禁用 / trial 结束；`AuthError` 才是 Key 废；`RegionError`（403）= 中国区托管 opt-in。BYOK 可带用户自己的工作区链接；**platform 叶绝不回显工作区 URL / id**。上游透传的 `403 This model is not available in your region` **不是** `RegionError`；顶层 `Router.Unavailable` 不在本表。未知 type 走现有兜底 |
| 思考 | DeepSeek 叶与官方同形：聊天/CEO/worker **显式**发 `thinking.type=enabled` + 组合 `reasoning_effort`（未设则 `high`）。Go 省略 `thinking` 时不推 CoT；只开 `thinking.type` 仍可能空思考。入站兼容 `reasoning` / `reasoning_text` 别名。不按端点开特例方言。消费侧：同 chunk 先 reasoning 后 content；思考未停时正文不进时间线（防 Go 交错流把一句 CoT 拆成两段 Thought）。`thinking=False` 的后台 one-shot 不攒。实际发出的档位落在 `run_completed.reasoning_effort`（可缺省），用量详情据此显示，不回读当前组合。Claude / Union Alpha 走 `/messages`：聊天/CEO/worker 发 Anthropic `thinking.enabled`（带 `budget_tokens`）；工具环回传带 signature 的 thinking block（`LLMMessage.thinking_blocks`，sidecar 经推理代理原样透传）。Qwen `/messages` 不发该字段。Anthropic 省略 = 关（与 DeepSeek 省略=开相反）。官方 OpenAI / OpenCode GPT·Grok·Muse 走 `/responses`：`reasoning.effort`（GPT-5 `minimal/low/medium/high`，GPT-6 / o 系列无 `none`，后台 one-shot 发该表最低档、不发 summary）；聊天/CEO/worker 另发 `reasoning.summary=auto`。`store: false` 时发 `include: reasoning.encrypted_content`；工具环回传 reasoning item（`encrypted_content` 进同一 `thinking_blocks`） |
| 未做 | `zen/` / `opencode-go/` 前缀路由；官方 Anthropic 预设（非 Zen 中转）；平台代付不上 Claude / Union Alpha / GPT |
| 隐私 | Zen **BYOK** free 档限时且可能用于改进模型。Go 路上 DeepSeek 的 zero-retention 以 OpenCode 当期条款为准，不得写成永久承诺，不得沿用免费档措辞。中国区托管 opt-in 是另一维度，勿与 ZDR 混成一句 |

## 五、platform 模式与故障排查

`billing_mode=platform` 走 `PLATFORM_*` 与账号池；改 env 三项须重启后端。池成员的增删改/禁用/解封经 admin 热更，不须重启。

**多模型 + 每模型凭据覆盖**（成本 §〇·六 F3）：`PLATFORM_MODELS` allowlist（非空时 `PLATFORM_MODEL` / 后台档须 ∈ 列表，否则启动 fail-fast）；`PLATFORM_MODEL_CREDENTIALS`（JSON `{model → {api_key?, base_url?, upstream_model?, id?}}`）给「一 key 一模型」绑独立凭据；可选 `upstream_model` 让目录 id 与上游 id 解耦（如目录 id `glm-5.2-alt` → 上游仍发 `glm-5.2`；计费 / 目录仍用目录 id）。单点 `platform_llm_credentials(model=…)` + 出站改写 `platform_wire_model`（`PlatformProvider`）。

**平台额度账号池**（admin 可热更）：成员表 `platform_credentials`，每行是绑定的 `(api_key, base_url)` + 该号自己的订阅日。Key 走既有 `KeyEncryptor` / `ENCRYPTION_KEY`（与 BYOK 同主密钥），明文永不回前端。每成员可声明 **上游工具面上限**（`tool_surface_limits`：`max_tools` / `max_properties_total` / `max_properties_per_tool`，均可空；**未声明 = 不限**）。数字由运维按该号订阅档填写，代码不硬编码任何厂商帽子。装配期（平台叶把 `tools` 装进请求、发出 HTTP **之前**）用我方 OpenAI 形开场表测量（条数 + 顶层 `function.parameters.properties` 键数，**不**假装对齐上游嵌套算法）对照声明：超限则记 `llm.tool_surface.limit_exceeded`，并以我方诚实错误结束该次调用——**不会发给上游，也不会自动裁剪或换一档工具表**。选钥：fill-first（打满一个再用下一个；冷却 / 月耗尽 / 401·403 封禁的号跳过）+ 同一 `conversation_id` 钉在同一号（该号耗尽才换，避免打散 prompt cache）。流式 **commit 前** 的 429、`CreditsError` / `MonthlyLimitError` / `UserLimitError`（该号没额度）与 403 `RegionError` 换到下一个启用号；commit 后维持现状（半成品 + `LLM_ERROR`，不做续写拼接）。401 **`AuthError`**（封号与坏 key 不可区分）摘除该号并告警，**不**拿其余号重试同一请求。`CreditsError`（401 空钱包 / 订阅未激活）与工作区月限走耗尽态，commit 前换号——不是 AuthError。OpenAI 兼容中转：401/403 带结构化 `insufficient_quota` / `billing_not_active` / `quota_exceeded` 或正文「额度不足」等，同样走余额耗尽换号，**不**当坏 key 摘号。403 `RegionError`（漏做中国区托管 opt-in）同样摘除并告警，但允许 commit 前换号；OpenCode 分类只认上游嵌套 `error.type`，兼容中转另认 `error.code` / 既有余额正文标记。全池冷却或封禁时诚实报错（既有「接入自己的 Key」CTA），**不**回落 env、不假装排队、不静默降级模型。**池为空或全禁用 → 回落现有 `PLATFORM_API_KEY` / `PLATFORM_BASE_URL`**。带自己 `api_key` 的 `PLATFORM_MODEL_CREDENTIALS` 覆盖仍优先于池。**运行态可见与解封**：冷却 / 耗尽 / 封禁及 `recovery_at` / 触发的限流窗名 / `source` 只活在 `platform_pool_state`（Redis / 进程内存、不落库），但随 `GET /v1/admin/platform-credentials` 每行下发（另标当前 fill-first 选中号、是否与 env key 同一把）；`POST …/{id}/clear-runtime` 手动清标记让该号重新可调度并写审计——**封禁号复活的唯一正规入口**，耗尽号充值后也可由此提前恢复。缺上游 `Retry-After` 时：带窗名（`5 hour` / `weekly`）的冷却仍按 5h；**无窗名**的裸 429 按 60s；月/余额耗尽按该号订阅月末。禁止 1s 占位让 fill-first 立刻打回空号。**不做** 80% 阈值提前切。可用性 = 默认 env key **或**任一覆盖有 key **或**池中有启用成员。缺 curated 价卡的 allowlist id → 不上架。

平台代付每次调用在日志（`llm.call` / `llm.call_failed`）与 `cost_calls.platform_credential_id` 记下用的是哪把凭据：池成员 = 该行 UUID；env / 覆盖路径 = `PLATFORM_CREDENTIAL_ID` 或覆盖 `id`，否则 `(api_key, base_url)` 稳定哈希；非 key 明文 / 后四位。只进日志与台账，不进用户可见 SSE error context。BYOK 该列为空。

**排查**：curl 直连 `{PLATFORM_BASE_URL}/chat/completions` 分辨代理 vs 上游；日志 `inference.proxy_upstream_error` / `llm.*`。值班探测配方不在公开仓。

**本机系统代理**：产品出网 httpx 默认 `trust_env=False`（不继承 `HTTP(S)_PROXY` / `ALL_PROXY`）。用户装 Clash 等把 `ALL_PROXY` 设成 `socks5://…` 时，旧行为会因缺少可选依赖 `socksio` 报「调用失败」；桌面 sidecar 启动时另剥离 SOCKS 类代理环境变量（HTTP 代理保留）。显式应用内代理配置仍可后续加，不靠默吃系统 SOCKS。

**出站连接复用**：LLM 叶子按 `base_url` 进程内共用一条 httpx 客户端（keep-alive **60s**；httpx 默认 5s 跨不了回合间隔）。鉴权、自定义头、OpenCode session、Anthropic version **每请求带**，不冻在客户端上——多用户、平台换号、队员 clone 共用同一条 TCP/TLS。回合 `close()` 只关掉该叶子实例，不拆连接。进程关停（API lifespan / sidecar 退出）才真正 aclose。桌面本机会话在输入框**首次聚焦**时经 sidecar `warmLlmHttp` 对推理 hop 握一次手（`GET /models`，404 也算握上；不发草稿、不 POST 补全）；API 进程不预连默认厂商。与 SearXNG 长活客户端同构，只是按上游源分槽。→ 见代码 `llm/http_pool.py`

**验收**：无 Key 账号可开聊并扣额度；平台目录 = `PLATFORM_MODELS`（须有 CNY 价卡）；用户面芯片写品牌名、不标「平台」；`run_completed.model` / `cost_events.model` 仍为 allowlist 内 id。旧会话钉着已下架系统预置时 expand 回落到新默认。

## 五·附、平台目录：部署 env

> 平台目录上架有 CNY 价卡、且走 `chat/completions` 的模型（默认 Flash）；自带 Key 仍为进阶选项（F7）。`PLATFORM_*` 的主机与 Key **只写在不入仓的部署凭据**，公开仓不写现网供应商。改生产 `.env` 的 `PLATFORM_*` / `QUOTA_*` 后**必须重启 api**（无热更）。**加 / 改 / 禁用池成员不须重启**（admin「供给 · 额度」· 平台额度账号）——每个新成员入池前必须单独按该上游自己的地区 / 模型闸实测（老号做过不代表新号做过）。
>
> 用户面平台档写品牌名，不标「平台」。自带 Key 的目录仍用品牌名；可在设置 · 模型改显示名。官方 BYOK 种子仍是 `deepseek-flash`。Go / Zen **作为用户 BYOK 预设**时的 id 与 `hideFromPicker` 见 §四·附。贴图按模型 vendor 合同（Flash 不收图；GLM-5.3-Flash / Kimi K3 收图）。开发者模式「这一回合」与 Admin / 日志仍写真实 id。现网绑定不在公开仓。

| 项 | 值 |
|---|---|
| `BILLING_MODE` | `platform` |
| `PLATFORM_BASE_URL` / `PLATFORM_API_KEY` | 不入仓的部署 local；公开示例不是现网供应商 |
| `PLATFORM_MODEL` | `deepseek-v4.1-flash`（默认） |
| `PLATFORM_MODELS` | 部署方 allowlist；须 ∈ 列表否则 fail-fast；缺卡不上架。示例：`deepseek-v4.1-flash,glm-5.3,glm-5.3-flash,kimi-k3`。Claude Opus / Fable、GPT-6 Astra / Sol **已有 CNY 价卡**，写入 allowlist 即可上架（OpenAI 兼容端点，不灰）。切流 env 另日。Grok / Muse / MiniMax / Qwen / K2.7 Code 默认不上 |
| 后台档 | 标题钉 `PLATFORM_BACKGROUND_MODEL=deepseek-v4.1-flash`（须 ∈ allowlist）。人不可选；关代付跟随这场主模型 |
| 额度 | 免费版月 5 积分 · 日 5 积分（`quota_*`）；付费席位 per-user 覆盖 → [成本配额 · 套餐](/docs/05-平台与运维/成本配额与计费.md) |
| 价卡 | Flash 官网（空闲 ¥0.02 / ¥1 / ¥4 每百万；高峰 2×）平台再 ×0.25。Claude ×0.3、GPT-6 ×0.18（USD 列表 × 冻结 7.2）。GLM-5.3 / GLM-5.3-Flash / Kimi K3 官网 ×2。BYOK 官网 1.0、费用位写 ¥、不扣积分。上游实付不进这把尺 |
| 上下文窗 | 跟 SKU id，不按端点猜。近顶默认按窗 × 80%；装配调低后按有效窗口 × 80% |
| Vision | 识图旁路已卸。对话贴图 / 工作区光栅走当前主力多模态；主模型不收图则诚实说明 |
| 公告 | 恢复时归档进行中的 `quota_unavailable`；发模板 **`quota_platform_restored`**（**不**点名供应商）→ [产品公告文案模板 §4.2](/docs/05-平台与运维/产品公告文案模板.md)。旧模板 id 的归档清单不在公开仓 |

**硬前置**：切 `billing_mode=platform` 前，用**同一把**将上线的 key 对将上线的 `BASE_URL` + 目录 id 打通 `chat/completions`。未过闸（地区拒绝、模型未开通、key 无效）时平台代付一上线**全体用户**一起撞，不是个别账号。换 id 须再打一枪，不能口头继承。

**共享订阅窗口（若绑定端点适用）**：有的上游把 5 小时 / 周 / 月帽算在**整户**上，不是按 AgentCore 用户分。单号打满时池会换到下一个号；**全池**打满时全体用户一起被挡（产品侧每用户额度拦不住这条上游共享帽）。用户面只说「平台模型繁忙」，并给「接入自己的 Key」。Admin「供给 · 成本」的窗口累计是我方名义价，**禁止**当成上游账单。空池时月窗锚见部署 env（UTC 日）。→ [管理员后台](/docs/05-平台与运维/管理员后台.md)

**隐私（用户面）**：平台推理经选用的模型服务商；不在公告里写某家零留存为永久承诺。BYOK 走用户自己填的端点，条款跟该端点。Go / Zen 预设的 ZDR / 免费档措辞只在 §四·附，不进产品公告。

验收信号：无 Key 账号可开聊并扣额度；`GET /v1/users/me/models` 平台行仅 allowlist 内 id、品牌 `display_name`、不戳 `badge=平台`；输入区芯片写品牌名、不标「平台」；接入行 `display_name` 可被账号别名覆盖；`run_completed.model` / `cost_events.model` 仍为该 id。旧会话钉着已下架系统预置时 expand 回落到新默认。
