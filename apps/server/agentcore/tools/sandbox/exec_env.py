"""Execution-environment failure markers + remaining health-check probes.

Per-execute classification is driven by the **first real run**, not a
shortest-program preflight. A missing interpreter (exit 127) or a spawn-site
refused-spawn tag retires the language it proved; a timeout never does (that
is slow user code or machine jitter, not a dead environment). Generic OS
permission strings never retire a real run.

The one remaining pre-run is a sandbox whose health signal starts no
interpreter at all — gVisor smoke-runs the ``runsc`` runtime — which keeps a
single backend-wide verdict, expressed here as ``language=None``.
``probe_interpreter`` stays for cloud boot / ``cloud_health`` only.

Timeout redesign (定案): idle/silence is the primary kill; a high disaster
wall is only a safety net — not a「verify budget」contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agentcore.tools.sandbox.protocol import ExecutionResult

EXEC_ENV_PROBE_FAIL_MARKER = "ExecEnvProbeFailed:"
# User-facing product sentence for the *unclassified* fallback (also
# ``tool_use_end.failure`` via curated code — keep byte-equal with
# ``runtime.engine.tool_failure_face._CURATED_BY_CODE``).
EXEC_ENV_PROBE_FAIL_USER_MESSAGE = (
    "本机执行环境不可用：这次没能判断出具体原因，代码没有运行。"
    "我会换个方式继续。"
)
EXEC_ENV_NOT_LINUX_USER_MESSAGE = (
    "云端隔离执行只在云上的 Linux 环境可用。当前对话跑在你的电脑上，代码没有运行。"
    "我会换个方式继续。"
)
EXEC_ENV_SANDBOX_UNAVAILABLE_USER_MESSAGE = (
    "云端隔离执行环境当前不可用，代码没有运行。我会换个方式继续。"
)
EXEC_ENV_SANDBOX_UNAVAILABLE_BROWSER_MESSAGE = (
    "云端隔离浏览器当前不可用，没有打开页面。"
)
# Stable wire code for probe fail (distinct from idle ``exec_timeout``).
EXEC_ENV_PROBE_FAIL_CODE = "exec_env_probe_failed"
# Classified reasons. A code is only assigned when one of the three fields the
# failure already hands back (exit_code / duration_ms / stderr) proves it; anything
# else stays on ``EXEC_ENV_PROBE_FAIL_CODE`` rather than inventing a cause.
EXEC_ENV_NO_INTERPRETER_CODE = "exec_env_no_interpreter"
# Only a health check can reach this now: a real run that times out keeps its own
# ``exec_timeout`` envelope and retires nothing.
EXEC_ENV_PROBE_TIMEOUT_CODE = "exec_env_probe_timeout"
EXEC_ENV_SPAWN_DENIED_CODE = "exec_env_spawn_denied"
# gVisor health_check: isolation needs Linux (sidecar on Windows, or any
# non-Linux host that still constructed GVisorSandbox).
EXEC_ENV_NOT_LINUX_CODE = "exec_env_not_linux"
# gVisor health_check: sandboxd / runsc / OS error — cloud isolation is down.
EXEC_ENV_SANDBOX_UNAVAILABLE_CODE = "exec_env_sandbox_unavailable"
EXEC_ENV_PROBE_FAIL_CODES: frozenset[str] = frozenset(
    {
        EXEC_ENV_PROBE_FAIL_CODE,
        EXEC_ENV_NO_INTERPRETER_CODE,
        EXEC_ENV_PROBE_TIMEOUT_CODE,
        EXEC_ENV_SPAWN_DENIED_CODE,
        EXEC_ENV_NOT_LINUX_CODE,
        EXEC_ENV_SANDBOX_UNAVAILABLE_CODE,
    }
)

# Budget for the health checks that remain (cloud boot / ``cloud_health``); no
# per-execute preflight spends it anymore.
EXEC_ENV_PROBE_TIMEOUT_S = 5

# Shortest program per language, for the ``cloud_health`` interpreter check.
# Per-execute paths do not run these — they classify the real run instead.
_PROBE_SNIPPETS: dict[str, str] = {
    "python": "print('ok')",
    "javascript": "console.log('ok')",
    "bash": "echo ok",
}
# What every snippet prints; a probe only passes when this reaches stdout.
PROBE_OK_TOKEN = "ok"
# Binary each language is launched with — the thing a「找不到解释器」verdict is
# actually about (mirrors ``subprocess._LANGUAGE_COMMANDS`` / desktop ``EXEC_LANGS``).
_LANGUAGE_LAUNCHER: dict[str, str] = {
    "python": "python",
    "javascript": "node",
    "bash": "bash",
}


def probe_snippet(language: str | None) -> str | None:
    """Probe source for ``language``, or ``None`` when we have no probe for it."""
    return _PROBE_SNIPPETS.get((language or "").strip())


def probe_launcher(language: str | None) -> str | None:
    """Launcher binary ``language`` is started with, or ``None`` when unknown."""
    return _LANGUAGE_LAUNCHER.get((language or "").strip())


# Coarse local-turn / journal failure bucket (also accepted as client ``code``).
EXEC_TIMEOUT_CODE = "exec_timeout"
EXEC_FORCED_STOP_CODE = "exec_forced_stop"

# Outer-loop verify (test_run): idle = primary; disaster = safety net only.
EXEC_IDLE_TIMEOUT_DEFAULT_S = 60
EXEC_IDLE_TIMEOUT_INSTALL_S = 120
EXEC_DISASTER_TIMEOUT_S = 1200  # 20 minutes
_ENGINE_TIMEOUT_SLACK_SECONDS = 30

TIMEOUT_IDLE_MARKER = "Timeout: no output for"
TIMEOUT_DISASTER_MARKER = "Timeout: forced stop after"
# Legacy wall-clock wording (old clients / journals) — still classified.
TIMEOUT_LEGACY_MARKER = "Timeout: execution exceeded"


def idle_timeout_stderr(idle_seconds: int) -> str:
    return f"{TIMEOUT_IDLE_MARKER} {int(idle_seconds)}s (execution stalled)"


def disaster_timeout_stderr(wall_seconds: int) -> str:
    return f"{TIMEOUT_DISASTER_MARKER} {int(wall_seconds)}s (forced stop)"


def is_idle_timeout_text(text: str | None) -> bool:
    raw = text or ""
    return TIMEOUT_IDLE_MARKER in raw


def is_disaster_timeout_text(text: str | None) -> bool:
    """True only for the new disaster-wall marker (not legacy wall-clock text)."""
    return TIMEOUT_DISASTER_MARKER in (text or "")


def is_legacy_wall_timeout_text(text: str | None) -> bool:
    raw = text or ""
    return TIMEOUT_LEGACY_MARKER in raw and TIMEOUT_IDLE_MARKER not in raw


def is_exec_env_probe_failure(stderr_or_text: str | None) -> bool:
    """True when stderr/output carries the sticky probe-fail marker."""
    return EXEC_ENV_PROBE_FAIL_MARKER in (stderr_or_text or "")


# Probe-only. These are generic OS strings: a user script's ``PermissionError``
# (busy file, etc.) contains them too. Real-run retire of a refused spawn is the
# spawn-site tag (``spawn_denied_stderr`` / desktop ``spawnDeniedStderr``), never
# these tokens. Probe may keep reading them — it only ever runs ``print('ok')``.
_SPAWN_DENIED_MARKERS = (
    "eacces",
    "eperm",
    "permissionerror",
    "permission denied",
    "operation not permitted",
    "winerror 5",
    "access is denied",
    "拒绝访问",
)
# Launcher resolution rejected before spawn, or the binary vanished between the
# PATH lookup and ``CreateProcess``/``execve``: desktop ``launcherMissingStderr``
# and sandbox ``_launcher_missing_stderr`` share the Chinese phrasing; ENOENT /
# FileNotFoundError cover the spawn-time race.
_NO_INTERPRETER_MARKERS = (
    "找不到命令",
    "找不到可用的命令",
    "enoent",
    "filenotfounderror",
    "no such file or directory",
    "winerror 2",
    "系统找不到指定的文件",
)
# POSIX / desktop convention for "launcher not found" (both backends answer 127).
_NO_INTERPRETER_EXIT_CODE = 127

# Closing advice, identical whatever the failure hit — the scope sentence in front
# of it is what varies by language.
_PROBE_FAIL_MODEL_ADVICE = (
    "若本回合有 terminal 工具，它走桌面进程通道，可改用它跑命令；"
    "否则请改静态核验，并如实说明命令未实跑。"
)

# Model-facing head per reason: state the fact the evidence supports, and what it
# rules out. ``{interpreter}`` / ``{snippet}`` are filled from the language the
# failed run asked for. The user-facing wording lives in ``tool_failure_face`` /
# ``limits``.
_PROBE_FAIL_MODEL_HEAD: dict[str, str] = {
    EXEC_ENV_NO_INTERPRETER_CODE: (
        "本机执行失败：PATH 上找不到{interpreter}（退出码 127）。"
        "缺的是解释器本身，与权限或安全软件无关。"
    ),
    EXEC_ENV_PROBE_TIMEOUT_CODE: (
        f"执行环境健康检查超时：一句 {{snippet}} 在 {EXEC_ENV_PROBE_TIMEOUT_S}s 内没跑完。"
        "解释器在，只是起得太慢；既不是缺解释器，也不是被系统拒绝。"
    ),
    EXEC_ENV_SPAWN_DENIED_CODE: (
        "本机执行被拒：启动{interpreter}进程时被系统拒绝（EACCES / EPERM）。"
        "解释器在，是进程启动这一步被拦下的。"
    ),
    EXEC_ENV_NOT_LINUX_CODE: (
        "云端隔离执行不可用：隔离沙箱只在 Linux 上运行，当前进程不是 Linux。"
        "代码没有执行；这不是本机解释器坏了。"
    ),
    EXEC_ENV_SANDBOX_UNAVAILABLE_CODE: (
        "云端隔离执行不可用：隔离沙箱健康检查未通过。代码没有执行。"
    ),
    EXEC_ENV_PROBE_FAIL_CODE: (
        "本机执行失败：这次运行没能完成；"
        "退出码 / 用时 / stderr 都不足以判定具体原因。"
    ),
}

# Short cause clause for the retire steer (same verdicts, steer voice).
_PROBE_FAIL_RETIRE_CAUSE: dict[str, str] = {
    EXEC_ENV_NO_INTERPRETER_CODE: "PATH 上没有{interpreter}",
    EXEC_ENV_PROBE_TIMEOUT_CODE: f"最短 print 未在 {EXEC_ENV_PROBE_TIMEOUT_S}s 内跑完",
    EXEC_ENV_SPAWN_DENIED_CODE: "启动解释器进程被系统拒绝",
    EXEC_ENV_NOT_LINUX_CODE: "当前引擎不在 Linux 云上",
    EXEC_ENV_SANDBOX_UNAVAILABLE_CODE: "云端隔离沙箱未就绪",
    EXEC_ENV_PROBE_FAIL_CODE: "原因未判明",
}

# What one probe verdict actually retires. ``test_run`` wraps every check in a
# python script, so a dead python takes it down along with python execution;
# any other language only takes itself out — ``code_execute`` stays listed so the
# next call can pick a language whose interpreter is present. A verdict that
# names no language (gVisor's runtime smoke test, or legacy untagged text) still
# speaks for the whole backend, which is what cloud has always done.
_PROBE_FAIL_RETIRE_TOOLS: dict[str, tuple[str, ...]] = {"python": ("test_run",)}
_PROBE_FAIL_RETIRE_ALL: tuple[str, ...] = ("code_execute", "test_run")

_PROBE_FAIL_CODE_TAG = re.compile(
    re.escape(EXEC_ENV_PROBE_FAIL_MARKER) + r"\s*\[([a-z0-9_]+)\]"
)
# Language tag, emitted after the reason tag so the code tag stays the first
# thing behind the marker (matchers and journals key on that order).
_PROBE_FAIL_LANG_TAG = re.compile(r"\[lang:([a-z0-9_+#-]+)\]")


def _interpreter_noun(language: str | None) -> str:
    """``" python 解释器"`` for a known launcher, bare ``"解释器"`` otherwise.

    Carries its own leading space so the templates read naturally in both cases
    (「找不到 node 解释器」 vs 「找不到解释器」).
    """
    launcher = probe_launcher(language)
    return f" {launcher} 解释器" if launcher else "解释器"


def probe_failure_retire_tools(language: str | None) -> tuple[str, ...]:
    """Tools a probe failure for ``language`` retires (see the table above)."""
    lang = (language or "").strip()
    if not lang:
        return _PROBE_FAIL_RETIRE_ALL
    return _PROBE_FAIL_RETIRE_TOOLS.get(lang, ())


def sandbox_unavailable_tool_meta() -> dict[str, Any]:
    """``ToolResult.metadata`` / attempt meta for a dead cloud desk (not 本机).

    First hit retires ``code_execute``/``test_run``. Not a contract_failure —
    switching language will not start a guest that is down.
    """
    return {
        "code": EXEC_ENV_SANDBOX_UNAVAILABLE_CODE,
        "error_class": "permanent",
        "retire_tools": list(probe_failure_retire_tools(None)),
        "retire_message": probe_failure_retire_steer(EXEC_ENV_SANDBOX_UNAVAILABLE_CODE),
    }


def is_sandbox_unavailable_error(exc: BaseException) -> bool:
    """True when ``SandboxError.details['code']`` is cloud-desk death."""
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        return False
    return details.get("code") == EXEC_ENV_SANDBOX_UNAVAILABLE_CODE


def _probe_fail_scope_sentence(language: str | None) -> str:
    """What is off for the rest of the turn, in the model's voice."""
    lang = (language or "").strip()
    if not lang:
        return "本回合 code_execute / test_run 已停用，原样重试只会再失败一次；"
    if lang == "python":
        return (
            "本回合 python 执行与 `test_run` 已停用"
            "（test_run 把每条 check 都包成 python 脚本跑），原样重试只会再失败一次；"
            "其它语言不在本次判定范围内，可另行尝试；"
        )
    return (
        f"本回合 {lang} 执行已停用，原样重试只会再失败一次；"
        "`test_run` 与其它语言不在本次判定范围内，可另行尝试；"
    )


def _one_line(text: str | None, *, limit: int = 200) -> str:
    """Collapse a captured stderr blob into one trimmed diagnostic line."""
    return " ".join((text or "").split())[:limit]


def spawn_denied_stderr(detail: str | None = None) -> str:
    """Thin spawn-site envelope: marker + reason tag + the OS error we caught.

    Real-run retire keys on the marker/tag (see ``annotate_real_exec_failure``),
    never on the OS prose — that prose is evidence, not the classifier.
    """
    trimmed = (detail or "").strip()
    if trimmed:
        return f"{EXEC_ENV_PROBE_FAIL_MARKER} [{EXEC_ENV_SPAWN_DENIED_CODE}] {trimmed}"
    return f"{EXEC_ENV_PROBE_FAIL_MARKER} [{EXEC_ENV_SPAWN_DENIED_CODE}]"


def classify_probe_failure(
    *,
    exit_code: int | None,
    duration_ms: int | None,
    stderr: str | None,
    timeout_seconds: int = EXEC_ENV_PROBE_TIMEOUT_S,
) -> str:
    """Name the probe failure from what the probe already returned.

    Only evidence-backed verdicts get a code — an unrecognised failure keeps
    ``EXEC_ENV_PROBE_FAIL_CODE`` instead of picking a plausible-sounding cause
    (the「安全软件」guess this taxonomy replaces). Order matters: a spawn-site
    tag is the declaration (not OS-string guessing); a killed probe reports
    its own timeout envelope; unmarked probe denials still use the OS tokens
    below (probe never runs user code).
    """
    text = (stderr or "").strip()
    if is_exec_env_probe_failure(text):
        return exec_env_probe_failure_code(text)
    lowered = text.lower()
    if (
        is_idle_timeout_text(text)
        or is_disaster_timeout_text(text)
        or is_legacy_wall_timeout_text(text)
    ):
        return EXEC_ENV_PROBE_TIMEOUT_CODE
    if any(m in lowered for m in _SPAWN_DENIED_MARKERS):
        return EXEC_ENV_SPAWN_DENIED_CODE
    if exit_code == _NO_INTERPRETER_EXIT_CODE or any(
        m in lowered for m in _NO_INTERPRETER_MARKERS
    ):
        return EXEC_ENV_NO_INTERPRETER_CODE
    # Nothing said why, but the probe burned its whole budget and never exited
    # cleanly — that is a deadline kill, whoever wrote the envelope.
    if (
        timeout_seconds > 0
        and duration_ms is not None
        and duration_ms >= timeout_seconds * 1000
        and exit_code != 0
    ):
        return EXEC_ENV_PROBE_TIMEOUT_CODE
    return EXEC_ENV_PROBE_FAIL_CODE


# Real-run retire is only these two. Timeout / unclassified stay on the original
# result so a slow script or a 5s jitter never takes ``test_run`` down with it.
_HARD_EXEC_ENV_RETIRE_CODES: frozenset[str] = frozenset(
    {EXEC_ENV_NO_INTERPRETER_CODE, EXEC_ENV_SPAWN_DENIED_CODE}
)


def should_retire_exec_env(code: str, *, language: str | None) -> bool:
    """Whether this classified failure should retire tools.

    Backend-wide smoke (``language`` empty — gVisor ``runsc``) still retires the
    family on any classified death. A per-language real run retires only on
    hard evidence: missing interpreter or refused spawn. Timeout never retires.
    """
    if not (language or "").strip():
        return True
    return code in _HARD_EXEC_ENV_RETIRE_CODES


def annotate_real_exec_failure(
    result: ExecutionResult, *, language: str | None
) -> tuple[ExecutionResult, ExecEnvProbeVerdict | None]:
    """Wrap a real-run failure when it proves the environment is dead.

    Returns ``(result, None)`` when the run succeeded, timed out, or failed
    without hard evidence — the original envelope stands. A missing interpreter
    (exit 127) or a spawn-site refused-spawn tag becomes the sticky marker
    consumers already know how to retire on, plus a dead verdict the workspace
    can memo. Generic OS permission strings never retire a real run.
    """
    if result.success:
        return result, None
    # A real run always names its language. Without one there is no per-language
    # verdict to record, and inheriting the backend-wide「any death retires the
    # family」rule here would rebuild the over-reach this redesign removed.
    if not (language or "").strip():
        return result, None
    stderr = result.stderr or result.stdout
    if is_exec_env_probe_failure(stderr):
        tagged = exec_env_probe_failure_code(stderr)
        # Spawn site declares a refused spawn with the thin marker+tag. Wrap
        # once into the model-facing envelope (skip if already wrapped).
        if tagged == EXEC_ENV_SPAWN_DENIED_CODE and should_retire_exec_env(
            tagged, language=language
        ):
            verdict = ExecEnvProbeVerdict(
                alive=False,
                code=tagged,
                evidence=probe_evidence(
                    exit_code=result.exit_code,
                    duration_ms=result.duration_ms,
                    stderr=stderr,
                ),
            )
            if exec_env_probe_failure_language(stderr) is not None:
                return result, verdict
            wrapped = verdict.failure_result(
                language=language, duration_ms=result.duration_ms
            )
            return wrapped, verdict
        return result, None
    code = classify_probe_failure(
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
        stderr=stderr,
        timeout_seconds=0,
    )
    # Probe still reads ``_NO_INTERPRETER_MARKERS`` (spawn-time ENOENT race).
    # The same strings appear in a user script's ``open(missing)`` traceback at
    # exit 1; real-run retire of a missing interpreter is only POSIX 127.
    if (
        code == EXEC_ENV_NO_INTERPRETER_CODE
        and result.exit_code != _NO_INTERPRETER_EXIT_CODE
    ):
        return result, None
    # Same cut for spawn-denied: the eight OS strings are not evidence. Only a
    # spawn-site tag (handled above) retires. Unmarked EACCES from an old
    # desktop build must not take ``test_run`` down.
    if code == EXEC_ENV_SPAWN_DENIED_CODE:
        return result, None
    if not should_retire_exec_env(code, language=language):
        return result, None
    verdict = ExecEnvProbeVerdict(
        alive=False,
        code=code,
        evidence=probe_evidence(
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            stderr=stderr,
        ),
    )
    wrapped = verdict.failure_result(language=language, duration_ms=result.duration_ms)
    return wrapped, verdict


def probe_evidence(
    *,
    exit_code: int | None = None,
    duration_ms: int | None = None,
    stderr: str | None = None,
) -> str:
    """Compact one-line probe facts for the model / logs (never the user face)."""
    parts: list[str] = []
    if exit_code is not None:
        parts.append(f"exit={exit_code}")
    if duration_ms is not None:
        parts.append(f"duration_ms={max(0, int(duration_ms))}")
    detail = _one_line(stderr)
    if detail:
        parts.append(f"stderr={detail}")
    return " ".join(parts)


def probe_failure_stderr(
    code: str = EXEC_ENV_PROBE_FAIL_CODE,
    *,
    language: str | None = None,
    evidence: str | None = None,
) -> str:
    """Sticky probe-fail stderr: marker + reason tag + honest model-facing text.

    The tags are what carry the verdict to ``code_execute`` / ``test_run`` (they
    only ever see this string), so they stay machine-readable; the marker prefix
    and the reason tag right behind it are unchanged for the taxonomy matchers
    that key on them. ``language`` is the language the probe ran — absent means
    the verdict is backend-wide (gVisor runtime smoke) and scoped accordingly.
    """
    resolved = code if code in EXEC_ENV_PROBE_FAIL_CODES else EXEC_ENV_PROBE_FAIL_CODE
    lang = (language or "").strip()
    head = _PROBE_FAIL_MODEL_HEAD[resolved].format(
        interpreter=_interpreter_noun(lang),
        snippet=probe_snippet(lang) or _PROBE_SNIPPETS["python"],
    )
    lang_tag = f"[lang:{lang}] " if lang else ""
    scope = _probe_fail_scope_sentence(lang)
    tail = f"（证据：{evidence}）" if (evidence or "").strip() else ""
    return (
        f"{EXEC_ENV_PROBE_FAIL_MARKER} [{resolved}] {lang_tag}"
        f"{head}{scope}{_PROBE_FAIL_MODEL_ADVICE}{tail}"
    )


def probe_failure_retire_steer(
    code: str = EXEC_ENV_PROBE_FAIL_CODE, *, language: str | None = None
) -> str:
    """Retire steer that names the probe verdict and what it actually took out.

    Twin of ``EXEC_ENV_TIMEOUT_RETIRE_STEER``, which stays on the genuine
    consecutive-idle-hang path: a probe that failed for a missing interpreter
    never「连续超时」, and saying so sends the model hunting the wrong workaround.
    """
    cause = _PROBE_FAIL_RETIRE_CAUSE.get(
        code, _PROBE_FAIL_RETIRE_CAUSE[EXEC_ENV_PROBE_FAIL_CODE]
    ).format(interpreter=_interpreter_noun(language))
    retired = "、".join(f"`{name}`" for name in probe_failure_retire_tools(language))
    lang = (language or "").strip()
    scope = f"本回合起停用 {retired}" if retired else "本回合起停用该语言的执行"
    if lang:
        scope += f"（这次跑的是 {lang}，其它语言未被本次判定）"
    if code in (EXEC_ENV_NOT_LINUX_CODE, EXEC_ENV_SANDBOX_UNAVAILABLE_CODE):
        return (
            f"云端隔离执行不可用（{cause}），{scope}——"
            "请改静态核验 / 读文件取证，并如实报告「云端代码执行未运行」；"
            "禁止再原样重试跑命令。"
        )
    return (
        f"本机执行环境不可用（{cause}），{scope}——"
        "请改静态核验 / 读文件取证，并如实报告「执行环境不可用、验证未实跑」；"
        "本机若有 `terminal` 工具，它走桌面进程通道，可用它跑命令；"
        "禁止再原样重试跑命令。"
    )


def exec_env_probe_failure_code(text: str | None) -> str:
    """Reason code carried by a probe-fail stderr (fallback for untagged text)."""
    match = _PROBE_FAIL_CODE_TAG.search(text or "")
    if match and match.group(1) in EXEC_ENV_PROBE_FAIL_CODES:
        return match.group(1)
    return EXEC_ENV_PROBE_FAIL_CODE


def exec_env_probe_failure_language(text: str | None) -> str | None:
    """Language the failed probe ran, or ``None`` for a backend-wide verdict.

    Legacy / untagged text also answers ``None``, which keeps its scope at the
    whole family — the behaviour those journals were written under.
    """
    match = _PROBE_FAIL_LANG_TAG.search(text or "")
    return match.group(1) if match else None


def probe_failure_result(
    *,
    duration_ms: int = 0,
    code: str = EXEC_ENV_PROBE_FAIL_CODE,
    language: str | None = None,
    evidence: str | None = None,
) -> ExecutionResult:
    """Canonical fail-fast result when a real run proved the environment dead."""
    return ExecutionResult(
        success=False,
        stdout="",
        stderr=probe_failure_stderr(code, language=language, evidence=evidence),
        exit_code=-1,
        duration_ms=max(0, duration_ms),
    )


EXEC_ENV_PROBE_FAIL_STDERR = probe_failure_stderr()


@dataclass(frozen=True)
class ExecEnvProbeVerdict:
    """One probe outcome, kept so every later fail-fast repeats the same cause."""

    alive: bool
    code: str = EXEC_ENV_PROBE_FAIL_CODE
    evidence: str = ""

    def failure_result(
        self, *, language: str | None = None, duration_ms: int = 0
    ) -> ExecutionResult:
        """Fail-fast result for this verdict (never call on a live one)."""
        return probe_failure_result(
            duration_ms=duration_ms,
            code=self.code,
            language=language,
            evidence=self.evidence,
        )


EXEC_ENV_PROBE_ALIVE = ExecEnvProbeVerdict(alive=True)


class ExecEnvProbeMemo:
    """Once-per-language memo of a proved-dead execution environment.

    Keyed by the language the failed run asked for, so a host missing
    ``python`` still runs ``node``. ``None`` is a single backend-wide key for
    health checks that start no interpreter (gVisor's ``runsc`` smoke).
    Only hard-evidence deaths are recorded on the per-language path; a timeout
    never lands here.
    """

    __slots__ = ("_verdicts",)

    def __init__(self) -> None:
        self._verdicts: dict[str | None, ExecEnvProbeVerdict] = {}

    def get(self, language: str | None) -> ExecEnvProbeVerdict | None:
        """Verdict already recorded for ``language``, or ``None`` if unprobed."""
        return self._verdicts.get(language)

    def record(
        self, language: str | None, verdict: ExecEnvProbeVerdict
    ) -> ExecEnvProbeVerdict:
        """Remember ``verdict`` for ``language`` and hand it back."""
        self._verdicts[language] = verdict
        return verdict


def looks_like_exec_timeout_text(text: str | None) -> bool:
    """Keyword / marker match for idle/legacy hang taxonomy (not disaster wall)."""
    raw = text or ""
    if not raw:
        return False
    if is_exec_env_probe_failure(raw):
        return True
    if is_idle_timeout_text(raw) or is_legacy_wall_timeout_text(raw):
        return True
    if "验证未在" in raw and "预算内完成" in raw:
        return True
    lower = raw.lower()
    return "exec_timeout" in lower or "execenvprobe" in lower.replace("_", "")
