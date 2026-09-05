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

> **现状**：目标仍是 `billing_mode=platform`（额度 `quota_*` · ¥10/月·¥10/日 · 台账 **nano-CNY**，无 FX）；**现网可临时 `byok`**（见 [成本配额与计费](/docs/05-平台与运维/成本配额与计费.md) 文首）。**dev 默认仍 BYOK**。本文只记上游接入事实（厂商坑、BYOK 去向、platform 排查）。

## 一、三条上游路径

| 路径 | 何时走 | 上游 |
|---|---|---|
| **BYOK 直连** | 用户配了 OpenAI 兼容服务商 | 用户自带端点（多服务商；典型 DeepSeek） |
| **多厂商 provider 路由** | model 串带 `厂商/` 前缀 | 豆包 / Moonshot / 智谱 等（§四） |
| **platform 平台凭据** | `billing_mode=platform` / 显式 platform | `PLATFORM_*` 三项 |

**BYOK 去向**：每用户多服务商列表（`user_llm_providers`：AES-GCM 密文 key + base_url + `default_model`）；账号/会话选的是**模型组合**（`llm_model_profiles` → `{main, worker?, background?, vision?}` 槽，每槽解析为目录身份 `@platform/{id}` / `@byok/{provider_id}/{id}`，库内仍存 `(model, origin, provider_id)`）。服务商上的 `default_model` 仅作连接测试 / 目录种子（UI 在「高级选项 · 连接测试用模型」，Input+datalist 可手填；换厂商预设时保留已填自定义值），**不是**日常聊天默认。测连：优先 `GET /models`（合法 JSON）；空列表或不在列表的 default → `POST /chat/completions` **且验 body**（拒 HTML/非 JSON/缺 choices）。目录已成功列出模型后 probe 仍 401/403（非余额）→ 点名「连接测试用模型」不被上游接受，**禁止**说 Key 无效；目录未证明 Key 时同时请核 Key 与该模型。成功文案须标明连通≠聊天就绪，并提示自定义 Base URL 通常需含 `/v1`。服务商卡片露出测试用模型 id（不是聊天默认）。key **不在 `.env`**。BYOK 且无服务商、又无 platform 回退 → `402 LLM_KEY_REQUIRED`。

## 二、模型与凭据解析

**模型组合**：CRUD `/v1/users/me/llm-model-profiles`；会话只认 `model_profile_id`（**新建拍快照**：create 写入当时账号默认或客户端所选 uuid；改账号默认不改旧会话）。存量 `null` 仍按账号默认展开（兼容活跟随）。PATCH 显式 `null` = 再钉当时默认（非清成活跟随）。设默认只在设置 / `PUT …/default`；输入框 picker 只选具体组合。**元数据事实源** = `llm/catalog.py`（上架集）+ `llm/model_metadata.py`（展示 enrichment）；`model_profiles` 只做组合 CRUD / expand，系统预置 = 对 catalog 可见上架集的 uuid5 投影（`uuid5(…, agentcore:platform-preset:{model_id})`，无硬编码产品 UUID）。逻辑默认 = `PLATFORM_MODEL` 对应预置（须在上架集内）否则 allowlist 首个。明确不做：质量档矩阵、账号级角色→模型矩阵、输入框双 picker /「跟随账号默认」行。✅ **Per-worker 节点显式覆盖**（执行链 + sidecar proxy；确认面不提供人改模）与组合槽正交 → [编排器 · Per-worker 模型覆盖](/docs/03-AI核心/编排器与CEO主Agent.md#per-worker-模型覆盖abc-同一功能)。

**识图槽 `vision`（可选）**：组合列不 persist follow main（空槽 ≠ 把 main 抄进槽）。解析 `VisionReader`：有槽 → 该槽凭据；槽空且 main 收图 → 复用 main（白板 / `read_image`）；否则仅 `billing_mode=platform` 且 `VISION_*` 齐全走运维兜底（默认 `kimi-k2.5`，不上架 `PLATFORM_MODELS`）。BYOK 填槽不因 `billing_mode=byok` 关死。

**对话贴图路由**：main 收图（`llm/image_accept.model_accepts_images`）→ 原生 multimodal（`image_url` 挂当前 user，跳过眼睛轨）；否则有 `VisionReader` → 眼→文；否则诚实「当前主模型不收图且未配置识图兜底」，不静默丢像素。同一图禁止双路径。能力位只认该厂商契约（精确 id + 进程内负例），不是展示元数据家族继承、也不是 id 关键词。visual critic **已退役**。白板 `board_read` 与 CEO `read_image` 走 `VisionReader`（可来自槽或收图的 main）。

`llm/resolve.py` 单点：

- **主对话**：用户 key 优先；无 key 才 platform。
- **长对话压缩**（连续性，不是 chrome）：跟 **这条对话** 的聊天付款方走（会话钉住的组合）。用户**显式**把组合的 background 槽指向自带 Key 时该槽仍优先。平台对话 → 平台 key + `enforce_quota`；自带 Key 对话 → 用户 key，**不**吃平台 ¥ 帽。入口 `billing/gate.py::run_compaction_llm`。回合后压缩仍 skip（不 429 刚结束的回合）；近顶折不进去 → 拒发。
- **后台档**（title/memory/workflow.slots）：用户**显式**把组合的 background 槽指向自带 Key 时 **该槽优先**（自己的钱，无「白嫖」可防；不过 `enforce_quota`，model id 原样透传不降档）；槽空（跟随主模型）/ 指向平台模型时才 **平台优先** + 必过 `enforce_quota`（防白嫖），平台不可用（配置缺失 **或** 上游 auth 拒绝 / 余额不足）才回落用户 BYOK。统一入口 `billing/gate.py::run_background_llm`（`resolve_and_gate_background` 解析 + 一次 auth 或余额→BYOK；耗尽 / 两边都失败 → skip，不 429 主回合、不对用户弹出「请改用自己的 API Key」）。禁止调用点各自 try/except 拼回落、禁止进程内 auth 熔断缓存。原 followups（「下一步」chips）已下线，不再走后台档。
- **回合内鉴权死短路（甲+乙）**：同一用户回合、同一付款方（`credential_source`）首次确认真 API Key `LLMAuthError`（不含 `INFERENCE_TOKEN_EXPIRED`）或余额不足后，`llm/turn_auth_dead.py` 按来源闩死后续未启动的同源 LLM（主聊后续轮 / 未开跑 worker / 本回合 chrome）；另一付款方不受影响（平台 chrome 死亡不得短路同回合 BYOK chat，反之亦然）。已在飞可自然失败。**不做**跨回合 TTL 负缓存（丙暂缓）。用户文案 / CTA 按 `credential_source` 分流（BYOK→去设置；平台→改用自己的 Key / 联系管理员）。
- **`platform_billing_selectable`**：仅 `billing_mode=platform` 时可选；BYOK 部署不开放平台代付。
- **Worker 槽**：空 = 跟随主模型；跨 origin 时 `build_turn_router` 注入 extras。Sidecar `cost_role=member`：请求 body `model` 为目录路由键（`platform/{id}` / `{provider_id}/{id}`）且合法 → **按该身份重解析凭据/model**；裸 mint/chat id 或未带显式 → 仍跟本槽。非法路由键 **硬失败**（`VALIDATION_ERROR`），禁 silent 回退野模型。→ [编排器 · Per-worker](/docs/03-AI核心/编排器与CEO主Agent.md#per-worker-模型覆盖abc-同一功能)。
- **统一目录** `GET /v1/users/me/models`：产品身份 = `ref`（`@platform/{id}` / `@byok/{provider_id}/{id}`）；行属性仍带 `(id, origin, provider_id)` 供分组。BYOK 行 = `default_model` ∪ 按 `base_url` 匹配的厂商预设 models ∪ 上游 `GET /models` 发现（发现失败/空仍保留预设，避免同厂商下拉只剩一项）；**不是**用前端硬编码清单取代发现。组合槽对 BYOK = **始终可手填 combobox**（服务商 + model id，目录进 datalist 建议；火山 `ep-…`、私有中转等）；platform 仍只 allowlist。platform 行有补贴才列。

## 三、sidecar 推理代理

桌面本地引擎**不拿 BYOK key**——经服务端出网：`POST /v1/inference/token` 铸 scoped token + 服务端解析 `model`；`POST /v1/inference/v1/chat/completions` 过同一道计费闸后转发。模型以服务端解析为准。→ `api/routes/inference/`；整体 → [双模式工作区](/docs/02-架构/双模式工作区.md)。

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
| 模型名 | `deepseek-v4-pro` / `deepseek-v4-flash`；旧名 `deepseek-chat` / `deepseek-reasoner` 已停用 |
| 识图 | 仅官方 id `deepseek-v4-flash-vision-exp` 收图；Flash / Pro 文本 id 不收 |
| 上下文 | 官方 **1M**（input+output 合计）；max output 384K。目录 `context_length` 与近顶压缩跟这条，不跟过期的 128K 记忆 |
| base_url | `https://api.deepseek.com`（兼容 `/v1`） |
| 思考开关 | `extra_body.thinking.type=enabled/disabled`。官方省略 = 默认 enabled；**AgentCore 聊天/CEO/worker 显式发 enabled**，DeepSeek V4 同时发官方默认档 `reasoning_effort=high`（不暴露强度 UI）。OpenCode Go 省略 `thinking` 时思考 token=0；只发 `thinking.enabled` 仍可能不回 CoT |
| 温度坑 | **思考模式下** `temperature`/`top_p`/penalty **静默忽略** |
| 工具调用 | 有 tool call 的回合必须原样回传 `reasoning_content`，否则 400 |
| 其它 | 不支持强制 `tool_choice=required`（probe 遇 400 回退）；无 `developer` role |

**思考开关按角色**：CEO / worker / 单聊 = 出站写 `thinking.type=enabled`（不省略）；后台 one-shot（title/memory/compaction/file.rewrite）= `disabled`。无 per-agent 思考强度档。

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
| 未做 | 不暴露 `reasoning_effort` UI；不做 `hy/` 平台前缀路由 |

## 四·附、OpenCode Zen / Go（BYOK 预设 + 可作 platform 上游）

OpenCode 两条 OpenAI 兼容上游，**计费与目录不同，必须按精确 base_url 分预设**（禁止前缀 / 包含匹配：`…/zen/go/v1` 不得吃成 Zen，反之亦然）。预设 `models` 仅短种子（发现失败兜底）；全量目录靠上游 `GET /models` 与现有 BYOK 发现合并。

| | Zen | Go |
|---|---|---|
| 预设 id / label | `opencode_zen` · OpenCode Zen | `opencode_go` · OpenCode Go |
| base_url | `https://opencode.ai/zen/v1` | `https://opencode.ai/zen/go/v1` |
| 计费 | 按量余额（含 `-free` 档） | $10/月订阅配额（5 小时 / 周 / 月）；目录**没有** `-free` |
| 默认模型 | `deepseek-v4-flash` | `deepseek-v4-flash` |
| 短种子 | Flash / `kimi-k2.6` / `glm-5.2` | Flash / Pro / `glm-5.2` |

| 项 | 约束 |
|---|---|
| 协议 | 只跑 OpenAI `chat/completions` 子集。已知只走 `/responses`（`grok-4.5`、`gpt-5.6-luna`）或 `/messages`（`minimax-m2.7`、`qwen3.7-max`）的 id **不进种子**（与目录过滤同一份清单）。目录合并层仍列出这些 id（不静默隐藏），但标为不可选并带结构化原因「本网关未实现该模型所需的上游协议」。过滤不在 HTTP discovery：`GET /models` 原样返回。被区域闸住的 id 仍可能出现在发现结果里——目录有 ≠ 一定能跑 |
| BYOK | 用户自备**对应端点**的 key；估算价卡按现有 BYOK 两层解析；**不**进平台配额。打错端点时付费 Flash 会在 Zen 路上 `CreditsError`（扣的是 Zen 余额，Go 订阅管不到） |
| 平台代付 | ✅ `PLATFORM_*` 可指向 Zen **或** Go；**现网钉 Go + 付费 Flash**（见 §五·附）。换上游 / 改 `quota_*` 须改生产 `.env` 并重启 api |
| 上下文 | 按 **SKU id**：付费 `deepseek-v4-flash` **1M**；仅 `deepseek-v4-flash-free` **200K**（Zen 网关 cap）。禁止按端点猜窗（Go 无 free 档也不把 Flash 当成 200K） |
| 错误分类 | 一张按上游嵌套 `error.type` 的表（信封 `{"type":"error","error":{"type":…}}`），禁止扫 `error.message`。**`GoUsageLimitError`（429）= Go 订阅配额用尽**（等窗口或控制台 `Use balance`），不是余额不足；**`CreditsError`（401）**收窄为无支付方式 / 订阅未激活 / 余额空；`MonthlyLimitError` / `UserLimitError` = 工作区月限或成员限；`ModelError` = 模型不支持 / 禁用 / trial 结束；`AuthError` 才是 Key 废；`RegionError`（403）= 中国区托管 opt-in。BYOK 可带用户自己的工作区链接；**platform 叶绝不回显工作区 URL / id**。上游透传的 `403 This model is not available in your region` **不是** `RegionError`；顶层 `Router.Unavailable` 不在本表。未知 type 走现有兜底 |
| 思考 | DeepSeek 叶与官方同形：聊天/CEO/worker **显式**发 `thinking.type=enabled` + `reasoning_effort=high`。Go 省略 `thinking` 时不推 CoT；只开 `thinking.type` 仍可能空思考。入站兼容 `reasoning` / `reasoning_text` 别名。不按端点开特例方言。消费侧：同 chunk 先 reasoning 后 content；思考未停时正文不进时间线（防 Go 交错流把一句 CoT 拆成两段 Thought）。`thinking=False` 的后台 one-shot 不攒 |
| 未做 | `zen/` / `opencode-go/` 前缀路由；为本网关开 Anthropic `/messages` / OpenAI `/responses` 协议分叉（触发条件：产品要上一个只说这两种协议的模型，而不是工具调用质量问题） |
| 隐私 | Zen **BYOK** free 档限时且可能用于改进模型。**现网 platform 走 Go**：DeepSeek ZDR 写到 **2026-08-31 且按月续约**（见 §五·附），不得写成永久承诺，也不得沿用免费档措辞。中国区托管 opt-in 是另一维度，勿与 ZDR 混成一句 |

## 五、platform 模式与故障排查

`billing_mode=platform` 走 `PLATFORM_*` 与账号池；改 env 三项须重启后端。池成员的增删改/禁用/解封经 admin 热更，不须重启。

**多模型 + 每模型凭据覆盖**（成本 §〇·六 F3）：`PLATFORM_MODELS` allowlist（非空时 `PLATFORM_MODEL` / 后台档须 ∈ 列表，否则启动 fail-fast）；`PLATFORM_MODEL_CREDENTIALS`（JSON `{model → {api_key?, base_url?, upstream_model?, id?}}`）给「一 key 一模型」中转绑独立凭据；可选 `upstream_model` 让目录 id 与上游 id 解耦（如目录 id `glm-5.2-alt` → 上游仍发 `glm-5.2`；计费 / 目录仍用目录 id）。单点 `platform_llm_credentials(model=…)` + 出站改写 `platform_wire_model`（`PlatformProvider`）。

**平台额度账号池**（admin 可热更）：成员表 `platform_credentials`，每行是绑定的 `(api_key, base_url)` + 该号自己的订阅日。Key 走既有 `KeyEncryptor` / `ENCRYPTION_KEY`（与 BYOK 同主密钥），明文永不回前端。每成员可声明 **上游工具面上限**（`tool_surface_limits`：`max_tools` / `max_properties_total` / `max_properties_per_tool`，均可空；**未声明 = 不限**）。数字由运维按该号订阅档填写，代码不硬编码任何厂商帽子。装配期（平台叶把 `tools` 装进请求、发出 HTTP **之前**）用我方 OpenAI 形开场表测量（条数 + 顶层 `function.parameters.properties` 键数，**不**假装对齐上游嵌套算法）对照声明：超限则记 `llm.tool_surface.limit_exceeded`，并以我方诚实错误结束该次调用——**不会发给上游，也不会自动裁剪或换一档工具表**。选钥：fill-first（打满一个再用下一个；冷却 / 月耗尽 / 401·403 封禁的号跳过）+ 同一 `conversation_id` 钉在同一号（该号耗尽才换，避免打散 prompt cache）。流式 **commit 前** 的 429、`CreditsError` / `MonthlyLimitError` / `UserLimitError`（该号没额度）与 403 `RegionError` 换到下一个启用号；commit 后维持现状（半成品 + `LLM_ERROR`，不做续写拼接）。401 **`AuthError`**（封号与坏 key 不可区分）摘除该号并告警，**不**拿其余号重试同一请求。`CreditsError`（401 空钱包 / 订阅未激活）与工作区月限走耗尽态，commit 前换号——不是 AuthError。403 `RegionError`（漏做中国区托管 opt-in）同样摘除并告警，但允许 commit 前换号；分类只认上游嵌套 `error.type`。全池冷却或封禁时诚实报错（既有「接入自己的 Key」CTA），**不**回落 env、不假装排队、不静默降级模型。**池为空或全禁用 → 回落现有 `PLATFORM_API_KEY` / `PLATFORM_BASE_URL`**。带自己 `api_key` 的 `PLATFORM_MODEL_CREDENTIALS` 覆盖仍优先于池。**运行态可见与解封**：冷却 / 耗尽 / 封禁及 `recovery_at` / 触发的限流窗名 / `source` 只活在 `platform_pool_state`（Redis / 进程内存、不落库），但随 `GET /v1/admin/platform-credentials` 每行下发（另标当前 fill-first 选中号、是否与 env key 同一把）；`POST …/{id}/clear-runtime` 手动清标记让该号重新可调度并写审计——**封禁号复活的唯一正规入口**，耗尽号充值后也可由此提前恢复。缺上游 `Retry-After` 时窗口冷却按 5h、月/余额耗尽按该号订阅月末，禁止 1s 占位让 fill-first 立刻打回空号。**不做** 80% 阈值提前切（名义价 ↔ 上游美元尚未校准）。可用性 = 默认 env key **或**任一覆盖有 key **或**池中有启用成员。缺 curated 价卡的 allowlist id → 不上架。

平台代付每次调用在日志（`llm.call` / `llm.call_failed`）与 `cost_calls.platform_credential_id` 记下用的是哪把凭据：池成员 = 该行 UUID；env / 覆盖路径 = `PLATFORM_CREDENTIAL_ID` 或覆盖 `id`，否则 `(api_key, base_url)` 稳定哈希；非 key 明文 / 后四位。只进日志与台账，不进用户可见 SSE error context。BYOK 该列为空。

**排查**：curl 直连 `{PLATFORM_BASE_URL}/chat/completions` 分辨代理 vs 上游；日志 `inference.proxy_upstream_error` / `llm.*`。可选 `SUB2API_ADMIN_*` 探测（非当前上游）。

**本机系统代理**：产品出网 httpx 默认 `trust_env=False`（不继承 `HTTP(S)_PROXY` / `ALL_PROXY`）。用户装 Clash 等把 `ALL_PROXY` 设成 `socks5://…` 时，旧行为会因缺少可选依赖 `socksio` 报「调用失败」；桌面 sidecar 启动时另剥离 SOCKS 类代理环境变量（HTTP 代理保留）。显式应用内代理配置仍可后续加，不靠默吃系统 SOCKS。

## 五·附、现网单模型：OpenCode Go · DeepSeek V4 Flash

> 运维定案：平台目录只上架 **一个**模型；BYOK 仍为高级选项（F7）。改生产 `.env` 的 `PLATFORM_*` / `QUOTA_*` 后**必须重启 api**（无热更）。**加 / 改 / 禁用池成员不须重启**（admin「系统」页 · 平台额度账号）——但**每个新成员入池前必须单独完成下条硬前置**（opt-in 是逐工作区的，老号做过不代表新号做过；漏做的号上游回 403 `RegionError`）。真实 key 只在不入仓的部署凭据文件或加密库里。
>
> **上游修订**：从 Zen 限时免费档切到 OpenCode **Go** 端点上的付费 `deepseek-v4-flash`。Zen 控制台同一把 key 两个端点通用（不必换 `PLATFORM_API_KEY`）。Go 目录**没有** `-free`。这不是免费档，也不是无限算力。
>
> **硬前置**：贴这套 `.env` / 上线前，必须先在 OpenCode 控制台为该工作区完成 DeepSeek 的中国区托管 opt-in，并用同一把 key 打 `POST https://opencode.ai/zen/go/v1/chat/completions` + `deepseek-v4-flash` 实测通过。未同意时上游回结构化 `RegionError`，平台代付一上线**全体用户**一起撞（不是个别账号）。已证事实仅限 **Go 端点的 DeepSeek**。

| 项 | 值 |
|---|---|
| `BILLING_MODE` | `platform` |
| `PLATFORM_BASE_URL` | `https://opencode.ai/zen/go/v1` |
| `PLATFORM_MODEL` / `PLATFORM_MODELS` | `deepseek-v4-flash`（仅此；须有 CNY curated 价卡，否则启动后不上架 / allowlist 与默认冲突则 fail-fast） |
| 后台档 | 同钉 `deepseek-v4-flash`（可显式 `PLATFORM_BACKGROUND_MODEL`，须 ∈ allowlist） |
| `PLATFORM_API_KEY` | 与 Zen 控制台同一把（不换） |
| 额度 | 月 ¥10 · 日 ¥10 · 日请求 500（`quota_*`） |
| 价卡 | curated 名义价 **同** 付费 Flash（¥0.02 / ¥1 / ¥2）——上游成本由 Go 订阅月费摊，产品仍按名义价扣额度 |
| 上下文窗 | `deepseek-v4-flash` **1M**（SKU）。目录展示与近顶压缩（窗 × 80% ≈ 800K）跟 SKU，禁止按端点猜成 Zen free 的 200K |
| Vision | 本阶段不配 `VISION_*`（白板读图：BYOK 填 vision 槽，或槽空且 main 收图时复用 main） |
| 公告 | 恢复时归档 `quota_unavailable`（以及仍在线的旧 `quota_jiurelay`）；发模板 **`quota_platform_restored`** → [产品公告文案模板 §4.2](/docs/05-平台与运维/产品公告文案模板.md) |

**运维动作（按序，不得跳）**

1. **硬前置**：上条 opt-in + 实测通过——排在贴 `.env`、切流量、以及下面 `Use balance` 之前。
2. Go 共享帽打满时，控制台开 `Use balance` 回落 Zen 余额（须 Zen 账户有钱）。这是人工运维动作，代码不能保证。

**Go 订阅共享限额（不得省略）**：OpenCode Go 的 5 小时 $12 / 周 $30 / 月 $60 是**整个订阅共享**，不是按 AgentCore 用户分。单账号窗口打满时池会换到下一个号；**全池**打满时全体用户一起被挡（产品侧每用户 ¥10 额度拦不住这条上游共享帽）。上游类型是 **`GoUsageLimitError`（429）**，不是 `CreditsError`。兜底见上条 `Use balance`，以及用户侧「接入自己的 Key」。

Admin「分析 · 成本」展示这三个窗口的**我方名义价累计**，外加一列按 OpenCode 公开单价读时估算的美元（`GET /v1/admin/usage/go-windows`）——名义价用来在撞 429 时对上「那一刻我们记了多少」；美元列只用来看离 $12 / $30 / $60 还有多远。**禁止**把任一列当成上游账单或余额，也禁止据此写死换算系数。公开单价表与 curated 名义价卡分开放，取值日期随调价更新。估算有两处未证死：Go 计入窗口前可能乘未公开的 `costMultiplier`（默认 1）；上游网关是否识别 DeepSeek cache 命中未经实包验证，若不识别则估算偏低。空池时月窗锚 `PLATFORM_GO_SUBSCRIPTION_DAY`（UTC 日，短月钳到月末），须配成 env 那把 key 的真实 Go 订阅日，默认 1 只是能启动的回退。池中每个成员带自己的订阅日（号是分批买的，锚点不同）；响应 `members[]` 按账号拆窗。周窗按 UTC 周一；5 小时是固定窗 + 空闲超窗归零，不是近 5 小时滑动求和。→ [管理员后台](/docs/05-平台与运维/管理员后台.md)

**隐私**：Go 路上的 DeepSeek 走 zero-retention（ZDR **声明写到 2026-08-31，且按月续约**，以 OpenCode 当期条款为准；**不得写成永久承诺**，月底复查）。与 Zen `-free`「限时免费、可能用于改进模型」不是同一事实；公告与对外叙述不得沿用免费档措辞，也不得把 Go 说成免费或无限。

**中国区托管 opt-in**（另一维度，勿与上段 ZDR 混成一句）：Go 端点的 DeepSeek 需工作区在 OpenCode 控制台显式同意中国区托管后才放行。未同意时上游回结构化 `RegionError`。`GET /models` **仍会列出**被闸住的 id（目录有 ≠ 能跑）。此处只记 opt-in 事实，不据此推断数据驻留或训练条款。

验收信号：无 Key 账号可开聊并扣额度；`GET /v1/users/me/models` 平台行仅 `deepseek-v4-flash`；输入区徽章 / `run_completed.model` / `cost_events.model` 同为该 id。
