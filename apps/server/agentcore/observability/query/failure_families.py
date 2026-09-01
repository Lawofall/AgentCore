"""失败家族表：巡检轴 1 失败榜的可累积知识资产。

每轮线上巡检都要回答同一个问题——「这窗有哪些失败、各多少次、比上窗多还是少」。以前这张表
靠一次性脚本互相复制传承：`_tmp_scan_20260811_*.py` 带 10 条，`_tmp_scan_20260812_*.py`
带 15 条，中间还发生过静默改名（`temperature_deprecated` → `temperature_invalid`），
跨窗计数序列因此断裂而无人察觉。这个模块把表收进仓，并让「改名 / 改口径」不再是静默事件。

三条防漂移设计：

``key``
    序列身份，**永不改名**。展示名要换改 ``label``；旧 key 退役进 ``aliases``，
    :func:`resolve_family_key` 把历史快照里的旧 key 映射到今天的 key，跨窗序列不断。

``digest``
    对「口径」（events / patterns / detector / revision / 大小写敏感）取的指纹。快照会把
    每个家族的 digest 一起落盘；跨窗 diff 发现 digest 变了就把该家族标成 ``redefined``，
    counts 判为**不可比**，而不是把口径变化伪装成数量涨跌。

``revision``
    有意改口径时手动 +1，并在 ``note`` 写清改了什么。digest 会跟着变，diff 因此会响。

新增家族是安全动作：diff 里显示为 ``new``，不影响任何既有序列。

家族之间**允许重叠**（同一条事件可同时命中 `contract_failed` 与 `write_pass_exhausted`）。
每个家族的 count 是独立集合的大小，彼此不做互斥，勿把各家族 count 相加当总数。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

# 非 regex / 非事件名判定的家族，交给 patrol 里的同名内建判定器。
Detector = Literal["", "tool_failure"]


@dataclass(frozen=True, slots=True)
class FailureFamily:
    """一个失败家族的口径定义。"""

    key: str
    """序列身份。禁止改名——改名会断掉跨窗计数序列；退役旧名进 ``aliases``。"""

    label: str
    """人读展示名。随便改，不进 digest。"""

    patterns: tuple[str, ...] = ()
    """正则源码，命中**抽取出的失败文本**（非整行 JSON）即算一次。"""

    events: tuple[str, ...] = ()
    """精确事件名，命中即算一次。"""

    detector: Detector = ""
    """内建判定器名；空 = 只用 events / patterns。"""

    aliases: tuple[str, ...] = ()
    """本家族用过的历史 key。旧快照靠它接回今天的序列。"""

    revision: int = 1
    """口径版本。有意改口径时 +1（digest 会变 → diff 标 redefined）。"""

    since: str = ""
    """这个家族最早从哪一窗开始记。用来区分「上窗是 0」和「上窗还没这个家族」。"""

    case_sensitive: bool = False
    """patterns 是否区分大小写。中文文案标记通常设 True 以免误伤。"""

    note: str = ""
    """口径说明 / 改动理由。不进 digest。"""

    def compiled(self) -> tuple[re.Pattern[str], ...]:
        flags = 0 if self.case_sensitive else re.IGNORECASE
        return tuple(re.compile(p, flags) for p in self.patterns)

    def digest(self) -> str:
        """口径指纹。label / since / note 变化不影响它——只有判定口径算数。"""
        payload = "\x1f".join(
            [
                self.key,
                str(self.revision),
                "1" if self.case_sensitive else "0",
                self.detector,
                "\x1e".join(sorted(self.events)),
                "\x1e".join(sorted(self.patterns)),
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


# event_text 抽取口径。改首部 / 末行规则必须 +1，并进 :func:`registry_digest`，
# 否则各族命中数会跨窗静默漂移。本模块不得 import patrol（别成环）；
# patrol.event_text 必须走 :func:`clip_diagnostic_value`。
TEXT_EXTRACT_REVISION = 2
TEXT_EXTRACT_HEAD = 480
TEXT_EXTRACT_TAIL_MAX = 240


def clip_diagnostic_value(
    value: str,
    *,
    head: int = TEXT_EXTRACT_HEAD,
    tail_max: int = TEXT_EXTRACT_TAIL_MAX,
) -> str:
    """取诊断字段的首部 + 末行。

    长 traceback（约 11k）的异常类型在末尾；只切前 N 字会让
    ``workspace.protocol.NotADirectory`` 永远进不了匹配面。
    """
    if len(value) <= head:
        return value
    header = value[:head]
    stripped = value.rstrip("\n")
    last_nl = stripped.rfind("\n")
    tail = stripped[-tail_max:] if last_nl < 0 else stripped[last_nl + 1 :]
    if len(tail) > tail_max:
        tail = tail[-tail_max:]
    if not tail or tail in header:
        return header
    return f"{header}\n{tail}"


# ---------------------------------------------------------------------------
# 表本体
#
# 顺序 = 聚类打标时的优先级（一条文本命中多族时，谁在前面谁当 primary family）。
# 具体事件名以 docs/05-平台与运维/对话日志分析指南.md 的事件词表为准。
# ---------------------------------------------------------------------------

FAILURE_FAMILIES: tuple[FailureFamily, ...] = (
    FailureFamily(
        key="contract_failed",
        label="契约验收失败",
        events=("contract.failed",),
        since="2026-08-04",
        note="轴 1 必扫信号（logs/reviews/README.md）。",
    ),
    FailureFamily(
        key="write_pass_exhausted",
        label="写盘闸重试耗尽 / 未落盘",
        events=("contract.write_pass_exhausted",),
        patterns=(r"未把产物写入工作区", r"write_pass_exhausted"),
        case_sensitive=True,
        since="2026-08-04",
        note="轴 1 必扫；「未把产物写入工作区」是必须按完整文案聚类的高频串。",
    ),
    FailureFamily(
        key="run_failed",
        label="队员 run 失败",
        events=("run.failed", "run.captain_failed"),
        patterns=(r"\brun_failed\b",),
        since="2026-08-04",
        note="turn_journal kind=run_failed 与 events 两路都算。",
    ),
    FailureFamily(
        key="llm_call_failed",
        label="LLM 调用失败",
        events=(
            "llm.call_failed",
            "engine.llm_round_exception",
            "engine.llm_failed_terminal",
        ),
        since="2026-08-04",
    ),
    FailureFamily(
        key="stream_stall",
        label="流停滞",
        events=("llm.stream_stalled",),
        patterns=(r"stream[_.]?stall",),
        since="2026-08-04",
    ),
    FailureFamily(
        key="ceiling_finalize",
        label="硬顶收尾",
        events=("engine.ceiling_finalize", "engine.token_budget_exhausted"),
        patterns=(r"token_budget",),
        since="2026-08-04",
        note="看是否对用户撒谎「做完了」——命中只说明收尾，不等于失败。",
    ),
    FailureFamily(
        key="tool_failed",
        label="工具执行失败",
        detector="tool_failure",
        since="2026-08-04",
        note=(
            "tool.execute_end 且 is_tool_failure（query.tool_end；"
            "含 allowlist_deny 等未执行；redirect 不算）。"
        ),
    ),
    FailureFamily(
        key="test_run_budget",
        label="外环验证预算耗尽",
        events=("contract.retry_skipped_budget",),
        patterns=(
            r"验证未在\s*\d+\s*秒?s?\s*预算内完成",
            r"预算耗尽",
            r"budget_exceeded",
            r"retry_skipped_budget",
        ),
        since="2026-08-06",
        note="README 标「必审」：即使会话有终稿，验证 incomplete 也不得记 ok。",
    ),
    FailureFamily(
        key="runner_mismatch",
        label="test_run runner 错配",
        patterns=(
            r"purpose\W{0,3}vitest[^\n]{0,80}(?:npx\s+)?jest",
            r"framework\W{0,3}vitest[^\n]{0,80}npx\s+jest",
            r"npx\s+jest[^\n]{0,80}(?:purpose|framework)\W{0,3}vitest",
            r"vitest\s+npx\s+jest",
            r"npx\s+jest\s+vitest",
        ),
        revision=2,
        since="2026-08-06",
        note=(
            "模型误填 runner（purpose/framework=vitest 而 command=npx jest）；与预算耗尽常纠缠。"
            "rev2：撤掉 `jest.{0,40}vitest`（扫中 test_run 帮助文案"
            "「…scripts.test（vitest/jest）、vitest.config…」）和裸 `npx jest`"
            "（正当 jest 仓库也会命中）。只认 vitest 意图与 jest 命令同时出现的错配。"
            "与 rev1 计数不可比。"
        ),
    ),
    FailureFamily(
        key="channel_dead",
        label="工作区通道死",
        events=("workspace.channel_dead", "workspace.op_rejected_channel_dead"),
        patterns=(r"channel[_.]?dead", r"通道不可用"),
        since="2026-08-06",
    ),
    FailureFamily(
        key="turn_phase_gate",
        label="回合 phase 闸拒绝",
        patterns=(r"turn_phase_gate", r"回合\s*phase="),
        since="2026-08-11",
    ),
    FailureFamily(
        key="byok_key_balance",
        label="BYOK 钥匙 / 余额",
        patterns=(
            r"API\s*Key\s*(?:无效|失效|错误|缺失)",
            r"api[_ ]?key[^\n]{0,20}(?:invalid|missing|expired|not\s*found)",
            r"invalid[^\n]{0,20}api[_ ]?key",
            r"余额不足",
            r"insufficient[^\n]{0,20}(?:balance|quota|credit)",
            r"凭证(?:无效|失效|缺失|错误)",
            r"authentication[_ ]?(?:failed|error)",
            r"\b(?:401|403)\s+(?:Unauthorized|Forbidden)\b",
        ),
        revision=2,
        since="2026-08-06",
        note=(
            "rev2（进仓时收紧）：原表是 `KEY|403|401|无权限` 裸词，对失败文本近乎全匹配——"
            "网页工具的 HTTP 403 也会被记成钥匙问题。改为要求钥匙/余额/授权语境。"
            "与 rev1 计数不可比。"
        ),
    ),
    FailureFamily(
        key="model_id_zombie",
        label="模型 ID 僵尸 / 不支持",
        patterns=(
            r"仅支持指定模型",
            r"model[_ ]?id",
            r"模型\s*ID",
            r"model[^\n]{0,20}not\s*found",
            r"unsupported[^\n]{0,20}model",
        ),
        since="2026-08-06",
    ),
    FailureFamily(
        key="empty_cancelled",
        label="空泡 / 空脸取消",
        patterns=(
            r"empty[_.]?cancel",
            r"cancelled[_.]?empty",
            r"empty[_.]?assistant",
            r"空泡",
            r"空脸",
        ),
        since="2026-08-06",
    ),
    FailureFamily(
        key="temperature_invalid",
        label="temperature 参数被拒",
        patterns=(r"invalid\s+temperature", r"temperature[^\n]{0,20}deprecat"),
        aliases=("temperature_deprecated",),
        since="2026-08-11",
        note=(
            "2026-08-12 那份一次性脚本把 temperature_deprecated 静默改成本名，"
            "跨窗序列因此断掉——本表用 aliases 接回，这也是 aliases 机制的由来。"
            "口径是「上游拒了 temperature」这件事本身：修复后的 llm.temperature_omitted_retry "
            "会带着上游原文 body_preview 一起命中，所以命中数 ≠ 用户可见失败数。"
        ),
    ),
    FailureFamily(
        key="path_not_dir",
        label="路径不是目录",
        patterns=(r"不是目录", r"not\s+a\s+directory", r"NotADirectory"),
        revision=2,
        since="2026-08-11",
        note=(
            "2026-08-11 午后窗 108 次；跨窗核验的典型样本。"
            "rev2：产品异常是 workspace.protocol.NotADirectory（不是 stdlib "
            "NotADirectoryError）；配合 event_text 首部+末行，11k traceback 末行才能入匹配面。"
            "与 rev1 计数不可比。"
        ),
    ),
    FailureFamily(
        key="queue_pool",
        label="连接池打满",
        events=("http.db_pool_exhausted", "db.pool_exhausted_snapshot"),
        patterns=(r"QueuePool", r"pool[_.]?exhaust", r"connection[_ ]?pool[^\n]{0,20}timeout"),
        since="2026-08-11",
    ),
    FailureFamily(
        key="unicode_header",
        label="中文 header 编码错",
        patterns=(r"UnicodeEncodeError", r"ascii.{0,10}codec.{0,20}encode"),
        since="2026-08-11",
    ),
    FailureFamily(
        key="write_arg_reject",
        label="写参回灌被拒",
        patterns=(
            r"写参回灌",
            r"回灌拒绝",
            r"拒绝回灌",
            r"非法写参",
            r"write_args?_rejected",
        ),
        since="2026-08-11",
        note="engine.write_args_clear 是清理动作，不是拒绝，故不入本族。",
    ),
    FailureFamily(
        key="memory_consolidation",
        label="记忆固化失败 / 丢窗",
        events=("memory.consolidation_failed", "memory.consolidation_window_dropped"),
        patterns=(r"consolidation_failed", r"consolidation_window_dropped"),
        since="2026-08-12",
    ),
    FailureFamily(
        key="db_client_cert_perm",
        label="DB 客户端证书权限拒绝",
        events=("cost.ledger_drain_failed",),
        patterns=(r"\.postgresql[/\\]", r"postgresql\.key"),
        since="2026-08-16",
        note=(
            "0.6.3 拉起瞬间 6058 次 cost.ledger_drain_failed + 21 次 http.unhandled_error 同根："
            "PermissionError '/root/.postgresql/postgresql.key'。属容器启动期而非产品会话失败；"
            "认领它是为了别让启动洪峰淹掉 unknown_or_new 残差。"
        ),
    ),
    FailureFamily(
        key="local_turn_id_invalid",
        label="本机回合 id 非 UUID",
        patterns=(
            r"http\.unhandled_error[\s\S]{0,4000}pgproto\.uuid_encode",
            r"pgproto\.uuid_encode[\s\S]{0,4000}http\.unhandled_error",
            r"http\.unhandled_error[\s\S]{0,4000}pg_uuid_bytes_from_str",
            r"pg_uuid_bytes_from_str[\s\S]{0,4000}http\.unhandled_error",
            r"invalid UUID '",
            r"length must be between 32\.\.36",
        ),
        revision=2,
        since="2026-08-16",
        note=(
            "sidecar 冷 resume 缺 user_message_id 时 mint 'resume-{turn_id}'（43 字符），"
            "云端当 messages.id 查即 500，桌面按 5xx 无限退避重试（约 6min/次）。"
            "不用 events 收 chat.regenerate_error——那个事件名下还有桌面离线闸，非同根。"
            "rev2：生产是 http.unhandled_error + sqlalchemy.exc.DBAPIError，13k traceback"
            "经 clip（首 480 + 末行）后头停在 `asyncpg.pgproto.pgproto.uuid_encode` /"
            " `uuid.pyx`，`ValueError: invalid UUID '…'` 是下一行被切掉，末行是"
            " sqlalche.me/e/20/dbapi。rev1 只匹 Python uuid.UUID 文案，整窗假阴性。"
            "改认 clip 里仍在的 `pgproto.uuid_encode`（UUID bind 专属栈帧，不是泛 DBAPIError），"
            "且要求同条诊断文本带 `http.unhandled_error`，以免工具把路径当 UUID 的 bind 误入本族；"
            "短 traceback 仍认 invalid UUID 原文。与 rev1 计数不可比。"
        ),
    ),
    FailureFamily(
        key="local_turn_tool_failures",
        label="本机回合工具失败",
        events=("chat.local_turn_tool_failures",),
        since="2026-08-16",
        note=(
            "云端唯一能看见本机回合工具失败的事件（info 级，codes/tools 原样透传）。"
            "本机回合 runtime 日志在用户机器 desktop.jsonl，云端 jsonl 没有"
            " sandbox.exec_env_probe_failed；榜上 0 次 ≠ 没发生——看本族。"
        ),
    ),
    FailureFamily(
        key="exec_env_probe_dead",
        label="执行环境判死",
        events=(
            "sandbox.exec_env_probe_failed",
            "coordination.exec_env_dead_user_notice",
        ),
        patterns=(
            r"exec_env_probe_timeout",
            r"exec_env_probe_failed",
            r"exec_env_no_interpreter",
            r"exec_env_spawn_denied",
        ),
        since="2026-08-16",
        note=(
            "执行环境被判死并停用工具；云端 gVisor 侧同形也走这两个事件名。"
            "本机回合的 runtime 日志在用户机器 desktop.jsonl，云端只能靠"
            " chat.local_turn_tool_failures（codes 含 exec_env_*）汇总看见——"
            "本族事件名在云端榜上 0 次 ≠ 没发生。"
            "回归哨兵：执行前预跑自检已撤，判死只认 127 / EACCES，超时不再判死；"
            " exec_env_probe_timeout 此后只应来自 cloud_health / gVisor runsc 冒烟，"
            "再从本机通道冒出来即回归（旧误伤规模见 20260816 纪要）。"
        ),
    ),
    FailureFamily(
        key="force_finalize_failed",
        label="强制收尾自身超时",
        events=("engine.force_finalize_failed",),
        patterns=(r"force_finalize wall clock exceeded",),
        since="2026-08-16",
        note="硬顶后连强制收尾都没做完；与 ceiling_finalize 重叠但更严重，单列以免被硬顶量盖住。",
    ),
    FailureFamily(
        key="stream_closed_by_consumer",
        label="SSE 被消费方关闭",
        patterns=(r"stream_closed_by_consumer",),
        since="2026-08-16",
        note="2026-08-15 窗 12 次残差未认领。多为客户端主动断流，命中不等于缺陷。",
    ),
    FailureFamily(
        key="asyncio_task_destroyed",
        label="asyncio 任务未收尾即销毁",
        patterns=(r"Task was destroyed but it is pending",),
        since="2026-08-16",
        note="事件循环退出时仍挂着的 Queue.get() / 长命请求；观测噪音档，认领只为不重复发现。",
    ),
    FailureFamily(
        key="desktop_offline_gate",
        label="桌面离线闸拒绝",
        patterns=(r"本机桌面未连接",),
        case_sensitive=True,
        since="2026-08-16",
        note=(
            "门闸的诚实拒绝（提示用户开桌面后点重新生成），**不是**缺陷。"
            "认领它是为了让 unknown_or_new 只剩真未知；量涨才值得看。"
        ),
    ),
    FailureFamily(
        key="sse_backpressure_drop",
        label="SSE 背压弃帧",
        events=("event_sink.backpressure_drop",),
        since="2026-08-17",
        note=(
            "慢消费者弃最旧；事件本身已心跳聚合（首丢 + 1s/1000 帧 + 结束冲余数），"
            "族计数是心跳条数不是弃帧总数。看 dropped_total。"
        ),
    ),
    FailureFamily(
        key="event_loop_lag",
        label="事件环卡顿",
        events=("event_loop.lag",),
        since="2026-08-17",
        note=(
            "单进程 uvicorn 事件环 sleep 超限（默认 ≥250ms）。"
            "首卡立刻一条，之后 10s 心跳（suppressed=其间超限次数），"
            "避免 1Hz 探针在持续 300ms 负载下刷屏。lag_summary 不入本族。"
        ),
    ),
    FailureFamily(
        key="readyz_failed",
        label="/readyz 探针失败",
        events=("http.readyz_failed",),
        since="2026-08-17",
        note=(
            "HTTP 503 / not_ready（硬依赖 Postgres）。首败立刻一条，之后 10s 心跳"
            "（fail_count=本拍探针次数），避免 db.ping_failed 那种 9s/14 次刷屏。"
            "恢复走 http.readyz，不入本族。"
        ),
    ),
    FailureFamily(
        key="disk_high_watermark",
        label="磁盘水位过高",
        events=("disk.high_watermark", "disk.probe_failed"),
        since="2026-08-17",
        note=(
            "宿主挂载点用量 ≥80%（或水位读失败）。/readyz body 带 used_pct，"
            "但不参与 200/503——盘快满把还能服务的实例判不可用会触发编排器重启循环。"
            "path 走 DATA_DIR 的非 overlay 挂载（容器 overlay 的 df / 是假数字）。"
            "首超立刻一条，之后 10s 心跳。"
        ),
    ),
    FailureFamily(
        key="rate_limit_fail_open",
        label="限流 Redis 失败开放（安全）",
        events=("rate_limit.redis_fail_open",),
        since="2026-08-17",
        note=(
            "Redis 超时/不可用时限流 fail-open 放行——限流失效窗口存在滥用风险，"
            "MUST_REVIEW，不是普通 warning。"
            "发射侧每请求一条（限流逻辑不改）；巡检按 10s 心跳记脉冲以免刷榜，"
            "原始放行次数看 log_stats summaries.rate_limit_fail_open.requests。"
        ),
    ),
)

# Patrol / stats share this so a Redis outage is first-hit + 10s pulses, not 1 line/request.
FAIL_OPEN_FAMILY_KEY = "rate_limit_fail_open"
FAIL_OPEN_PULSE_S = 10.0

UNKNOWN_FAMILY = "unknown_or_new"
"""伪家族：level=error / warning 却没被任何家族认领的失败文本。

README 的「新文案 → 必审」就落在这里。它不在 :data:`FAILURE_FAMILIES` 里，
因为它没有口径可言——它恰恰是「表还没覆盖到」的残差。
"""


def _build_index() -> tuple[dict[str, FailureFamily], dict[str, str]]:
    by_key: dict[str, FailureFamily] = {}
    alias_map: dict[str, str] = {}
    for fam in FAILURE_FAMILIES:
        if fam.key in by_key:
            raise ValueError(f"duplicate failure family key: {fam.key}")
        by_key[fam.key] = fam
    for fam in FAILURE_FAMILIES:
        for alias in fam.aliases:
            if alias in by_key:
                raise ValueError(f"alias {alias!r} collides with live family key")
            if alias in alias_map:
                raise ValueError(f"alias {alias!r} claimed by two families")
            alias_map[alias] = fam.key
    return by_key, alias_map


FAMILIES_BY_KEY, _ALIAS_TO_KEY = _build_index()


def resolve_family_key(key: str) -> str | None:
    """历史 key → 今天的 key；不认识则 None。

    跨窗 diff 读旧快照时先过这里，改过名的家族才不会被当成「一个消失 + 一个新增」。
    """
    if key in FAMILIES_BY_KEY:
        return key
    return _ALIAS_TO_KEY.get(key)


def family_digests() -> dict[str, str]:
    """key → 口径指纹。落进快照，供跨窗 diff 判「counts 是否可比」。"""
    return {fam.key: fam.digest() for fam in FAILURE_FAMILIES}


def registry_digest() -> str:
    """整表指纹。一眼看出两份快照是不是同一张表跑出来的。

    抽取口径（首部+末行）也进指纹：改 ``event_text`` 取值会让各族命中数漂移，
    必须标成整表变了，不能假装还是同一口径。
    """
    joined = "\x1f".join(f"{k}:{v}" for k, v in sorted(family_digests().items()))
    extract = (
        f"extract:{TEXT_EXTRACT_REVISION}:{TEXT_EXTRACT_HEAD}:{TEXT_EXTRACT_TAIL_MAX}"
    )
    return hashlib.sha256(f"{joined}\x1f{extract}".encode()).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class CompiledFamily:
    """预编译的家族，扫描热路径用。"""

    family: FailureFamily
    patterns: tuple[re.Pattern[str], ...] = field(default=())
    event_names: frozenset[str] = field(default=frozenset())


@dataclass(frozen=True, slots=True)
class CompiledRegistry:
    """整表预编译产物 + 一个廉价的整体预筛正则。"""

    families: tuple[CompiledFamily, ...]
    probe: re.Pattern[str] | None
    events_index: frozenset[str]

    def match(self, *, event: str, text: str, tool_failed: bool = False) -> list[str]:
        """返回这条事件命中的所有家族 key，按表内顺序。"""
        hits: list[str] = []
        probe_hit = bool(text) and self.probe is not None and self.probe.search(text) is not None
        for cf in self.families:
            fam = cf.family
            if event and event in cf.event_names:
                hits.append(fam.key)
                continue
            if fam.detector == "tool_failure" and tool_failed:
                hits.append(fam.key)
                continue
            if probe_hit and any(rx.search(text) for rx in cf.patterns):
                hits.append(fam.key)
        return hits


def compile_registry(
    families: tuple[FailureFamily, ...] = FAILURE_FAMILIES,
) -> CompiledRegistry:
    """编译家族表。``probe`` 是所有 pattern 的合并预筛——绝大多数行一次匹配就否掉。"""
    compiled = tuple(
        CompiledFamily(
            family=fam,
            patterns=fam.compiled(),
            event_names=frozenset(fam.events),
        )
        for fam in families
    )
    # 大小写敏感的 pattern 也塞进不敏感的预筛：预筛只负责放行，精判仍归各族自己。
    sources = [p for fam in families for p in fam.patterns]
    probe = re.compile("|".join(f"(?:{s})" for s in sources), re.IGNORECASE) if sources else None
    events_index = frozenset(name for fam in families for name in fam.events)
    return CompiledRegistry(families=compiled, probe=probe, events_index=events_index)
