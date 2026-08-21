"""Central tool-failure user face (``tool_use_end.failure``)."""

import ast
import re
from pathlib import Path

import agentcore
from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import ToolError, ValidationError
from agentcore.runtime.engine.tool_failure_face import (
    _CURATED_BY_CODE,
    DEFAULT_TOOL_FAILURE_MESSAGE,
    tool_failure_fields,
    tool_failure_from_result,
)
from agentcore.runtime.events import tool_use_end
from agentcore.tools.protocol import ToolResult


def test_tool_failure_fields_passes_agentcore_product_copy():
    face = tool_failure_fields(exc=ToolError("沙箱启动失败，请稍后重试"))
    assert face == {
        "message": "沙箱启动失败，请稍后重试",
        "code": ErrorCode.TOOL_ERROR,
    }


def test_tool_failure_fields_collapses_unclassified_exc_to_curated():
    face = tool_failure_fields(exc=RuntimeError("ConnectError: 127.0.0.1:8888 boom"))
    assert face["code"] == ErrorCode.TOOL_ERROR
    assert face["message"] == DEFAULT_TOOL_FAILURE_MESSAGE
    assert "127.0.0.1" not in face["message"]
    assert "ConnectError" not in face["message"]


def test_tool_failure_fields_passes_authored_product_message():
    face = tool_failure_fields(
        code="args_parse_failed",
        product_message="长文保存失败，改成分段写入继续。",
    )
    assert face == {
        "message": "长文保存失败，改成分段写入继续。",
        "code": "args_parse_failed",
    }


def test_tool_failure_fields_curates_by_stable_code():
    face = tool_failure_fields(code="retrieval_budget_exhausted")
    assert face == {
        "message": "本次任务的联网查资料次数已用完，这一次没有再去搜；我会基于已经查到的内容继续。",
        "code": "retrieval_budget_exhausted",
    }


def test_tool_failure_from_result_never_lifts_error_output():
    result = ToolResult(
        tool_call_id="t1",
        success=False,
        output="stderr:\nExecEnvProbeFailed: no docker",
        error="ConnectError: host:8080 refused",
    )
    face = tool_failure_from_result(result)
    assert face["message"] == DEFAULT_TOOL_FAILURE_MESSAGE
    assert face["code"] == ErrorCode.TOOL_ERROR
    assert "ExecEnvProbeFailed" not in face["message"]
    assert "host:8080" not in face["message"]


def test_tool_failure_from_result_honors_optional_user_fields():
    result = ToolResult(
        tool_call_id="t1",
        success=False,
        output="model detail with tokens",
        error="str(exc)",
        failure_message="浏览器宿主暂时不可用，请稍后重试。",
        failure_code="host_unavailable",
    )
    assert tool_failure_from_result(result) == {
        "message": "浏览器宿主暂时不可用，请稍后重试。",
        "code": "host_unavailable",
    }


def test_tool_failure_from_result_uses_metadata_code_curated():
    result = ToolResult(
        tool_call_id="t1",
        success=False,
        output="Timeout: no output for 60s",
        error="idle",
        metadata={"code": "exec_timeout"},
    )
    assert tool_failure_from_result(result) == {
        "message": "执行超时，请缩小范围后重试。",
        "code": "exec_timeout",
    }


def test_tool_failure_from_result_coded_gets_curated_uncoded_stays_generic():
    """Acceptance: authored stable code → specialty sentence; bare fail → default."""
    from agentcore.db.errors import DATABASE_UNAVAILABLE_CODE, DATABASE_UNAVAILABLE_MESSAGE
    from agentcore.tools.sandbox.exec_env import (
        EXEC_ENV_PROBE_FAIL_CODE,
        EXEC_ENV_PROBE_FAIL_USER_MESSAGE,
    )

    coded = ToolResult(
        tool_call_id="t1",
        success=False,
        output=f"列出项目失败。{DATABASE_UNAVAILABLE_MESSAGE}",
        error=DATABASE_UNAVAILABLE_CODE,
        failure_code=DATABASE_UNAVAILABLE_CODE,
    )
    assert tool_failure_from_result(coded) == {
        "message": DATABASE_UNAVAILABLE_MESSAGE,
        "code": DATABASE_UNAVAILABLE_CODE,
    }

    probe = ToolResult(
        tool_call_id="t2",
        success=False,
        output="stderr:\nExecEnvProbeFailed: …",
        error="exit 1",
        metadata={"code": EXEC_ENV_PROBE_FAIL_CODE},
    )
    assert tool_failure_from_result(probe) == {
        "message": EXEC_ENV_PROBE_FAIL_USER_MESSAGE,
        "code": EXEC_ENV_PROBE_FAIL_CODE,
    }

    uncoded = ToolResult(
        tool_call_id="t3",
        success=False,
        output="weird internal token XYZ",
        error="RuntimeError: boom",
    )
    face = tool_failure_from_result(uncoded)
    assert face == {
        "message": DEFAULT_TOOL_FAILURE_MESSAGE,
        "code": ErrorCode.TOOL_ERROR,
    }
    assert "XYZ" not in face["message"]
    assert "RuntimeError" not in face["message"]


def test_curated_copy_stays_synced_with_tool_sources():
    """Curated table must stay byte-equal to tool/db product constants (no import cycle)."""
    from agentcore.db.errors import DATABASE_UNAVAILABLE_MESSAGE
    from agentcore.runtime.engine.tool_failure_face import _CURATED_BY_CODE
    from agentcore.tools.sandbox.exec_env import EXEC_ENV_PROBE_FAIL_USER_MESSAGE

    assert _CURATED_BY_CODE["database_unavailable"] == DATABASE_UNAVAILABLE_MESSAGE
    assert _CURATED_BY_CODE[ErrorCode.DATABASE_UNAVAILABLE] == DATABASE_UNAVAILABLE_MESSAGE
    assert _CURATED_BY_CODE["exec_env_probe_failed"] == EXEC_ENV_PROBE_FAIL_USER_MESSAGE
    assert _CURATED_BY_CODE["searxng_unreachable"] == "本地搜索服务不可用，请稍后重试"
    assert _CURATED_BY_CODE["workspace_channel_dead"] == _CURATED_BY_CODE[ErrorCode.STREAM_ERROR]


def test_tool_use_end_omits_failure_on_success():
    ev = tool_use_end(
        "tc1",
        "web_search",
        success=True,
        output="ok",
        failure={"message": "should not appear", "code": "TOOL_ERROR"},
    )
    assert "failure" not in ev.payload
    assert ev.payload["status"] == "success"


def test_tool_use_end_attaches_failure_on_error():
    ev = tool_use_end(
        "tc1",
        "web_search",
        success=False,
        output="搜索失败：ConnectError: host:8080",
        failure=tool_failure_fields(code=ErrorCode.TOOL_ERROR),
    )
    assert ev.payload["status"] == "error"
    assert ev.payload["result"] == "搜索失败：ConnectError: host:8080"
    assert ev.payload["failure"] == {
        "message": DEFAULT_TOOL_FAILURE_MESSAGE,
        "code": "TOOL_ERROR",
    }


def test_validation_error_exc_passes_through():
    face = tool_failure_fields(exc=ValidationError("参数缺少 query"))
    assert face == {"message": "参数缺少 query", "code": ErrorCode.VALIDATION_ERROR}


# --- Registration sentinel -------------------------------------------------------------
# A tool that invents a code with no sentence in ``_CURATED_BY_CODE`` collapses silently to
# the default — a dozen live codes drifted in unnoticed that way, because the per-code tests
# above only spell out a handful. These scan the producing sources instead of a hand list,
# and follow the code through each tool's own ToolResult helper (a literal-only scan saw
# none of read_url's, since every one of them arrives as ``_failed(code=…)``).

# Source trees that build the user failure face: tools author ``metadata["code"]`` /
# ``failure_code``, the engine deny paths call ``tool_failure_fields(code=...)``, and
# workspace limits hands the engine ready-made ToolResult metadata.
_SCANNED_PACKAGE_DIRS = ("tools", "runtime/engine")
_SCANNED_FILES = ("workspace/limits.py",)

# Failures whose cause is settled: the identical call fails again, so「稍后重试」would be
# a lie. They may still name a fix the user can make (configure credentials, turn off the
# proxy's fake-ip mode) — only unconditional wait-and-retry advice is banned.
_DETERMINISTIC_CODES = (
    "allowlist_deny",
    "approval_denied",
    "args_parse_failed",
    "auth_failed",
    "blocked_host",
    "egress_unavailable",
    "fake_ip_proxy_blocked",
    "invalid_args",
    "language_unavailable",
    "launcher_unavailable",
    "local_workspace_required",
    "long_running_redirect",
    "loopback_host",
    "no_default_branch",
    "no_remote",
    "not_found",
    "not_github",
    "novel_domain_blocked",
    "password_blocked",
    "postcondition_failed",
    "private_address_blocked",
    "project_verify_redirect",
    "read_url_retired",
    "repo_unusable",
    "sandbox_network_unsupported",
    "session_bound_elsewhere",
    "session_not_found",
    "site_access_denied",
    "too_many_redirects",
    "validation_failed",
    "unauthenticated",
    "user_in_control",
    "verify_contract",
    "verify_policy_inner",
    "verify_result",
    "wait_for_required",
)

# Engine vocabulary + model-channel imperatives that must never reach the user sentence.
_INTERNAL_VOCAB = (
    "收口",
    "台账",
    "落盘",
    "活性挂起",
    "白名单",
    "检索预算",
    "收尾窗口",
    "结构闸",
    "handoff",
    "contract_failure",
    "metadata",
    "ToolResult",
)
_MODEL_IMPERATIVES = ("禁止", "不得", "不要原样重试", "请改用其他方案")


# A code is snake_case by convention; the shape filter is what keeps the model-facing
# Chinese steer out when a classifier returns ``(steer, code)`` pairs.
_CODE_SHAPED = re.compile(r"^[a-z][a-z0-9_]{2,48}$")
# Tools do not hand the engine a code directly: each file wraps ToolResult in its own
# helper (``read_url._failed``, ``terminal._error``, ``file_ops._error``) and passes the
# code as a keyword. The helper is therefore found by shape, never by a hardcoded name.
_SINK_PARAM_NAMES = ("code", "failure_code")


def _str_constant(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _assigned_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Tuple | ast.List):
        return [n for element in target.elts for n in _assigned_names(element)]
    return []


def _alias_map(fn: ast.AST) -> dict[str, str]:
    """``x = y`` chains inside one function, so a renamed parameter still counts."""
    aliases: dict[str, str] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
            for name in _assigned_names(node.targets[0] if node.targets else node):
                aliases[name] = node.value.id
    return aliases


def _root_alias(name: str, aliases: dict[str, str]) -> str:
    seen: set[str] = set()
    while name in aliases and name not in seen:
        seen.add(name)
        name = aliases[name]
    return name


def _code_carrying_names(fn: ast.AST) -> set[str]:
    """Names this function feeds into a ``"code"`` slot of a ToolResult."""
    found: set[str] = set()

    def collect(expr: ast.AST) -> None:
        found.update(sub.id for sub in ast.walk(expr) if isinstance(sub, ast.Name))

    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if key is not None and _str_constant(key) == "code":
                    collect(value)
        elif isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "setdefault"
                and len(node.args) == 2
                and _str_constant(node.args[0]) == "code"
            ):
                collect(node.args[1])
            for kw in node.keywords:
                if kw.arg in _SINK_PARAM_NAMES:
                    collect(kw.value)
    return found


class _ProducerModule:
    """One producing source read statically: code literals plus a hop of indirection.

    Just enough dataflow for the shapes tools actually use — a module constant, a
    ``URLBlock → code`` table, an imported ``*_CODE`` constant, and a classifier that
    returns ``(model steer, user code)`` — without pretending to be a type checker.
    """

    _imported: dict[tuple[Path, str], list[str]] = {}
    _success_builders: dict[tuple[Path, str], bool] = {}

    def __init__(self, path: Path, root: Path) -> None:
        self.path = path
        self.root = root
        self.tree = ast.parse(path.read_text(encoding="utf-8"))
        self.constants: dict[str, str] = {}
        self.tables: dict[str, list[str]] = {}
        self.imports: dict[str, tuple[str, int]] = {}
        self.import_origins: dict[str, str] = {}
        self.functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self.var_sources: dict[str, list[ast.AST]] = {}
        self._returns: dict[str, list[str]] = {}
        self._index()

    def _index(self) -> None:
        for node in self.tree.body:
            target: ast.AST | None = None
            value: ast.AST | None = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign):
                target, value = node.target, node.value
            if not isinstance(target, ast.Name) or value is None:
                continue
            literal = _str_constant(value)
            if literal is not None:
                self.constants[target.id] = literal
            elif isinstance(value, ast.Dict):
                # Module order matters: a table's values may be constants defined above it.
                codes = [c for element in value.values for c in self.resolve(element)]
                if codes:
                    self.tables[target.id] = [c for c in codes if _CODE_SHAPED.match(c)]
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    local = alias.asname or alias.name
                    self.imports[local] = (node.module or "", node.level)
                    self.import_origins[local] = alias.name
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                self.functions[node.name] = node
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    for name in _assigned_names(target):
                        self.var_sources.setdefault(name, []).append(node.value)

    def resolve(self, node: ast.AST | None, *, depth: int = 0) -> list[str]:
        """Every code string ``node`` can evaluate to, as far as the source shows."""
        if node is None or depth > 4:
            return []
        direct = _str_constant(node)
        if direct is not None:
            return [direct]
        if isinstance(node, ast.IfExp):
            return self.resolve(node.body, depth=depth + 1) + self.resolve(
                node.orelse, depth=depth + 1
            )
        if isinstance(node, ast.Name):
            if node.id in self.constants:
                return [self.constants[node.id]]
            if node.id in self.tables:
                return list(self.tables[node.id])
            if node.id in self.imports:
                return self._imported_constant(node.id)
            # A local: follow what it was assigned from (shape-filtered, bounded).
            out: list[str] = []
            for source in self.var_sources.get(node.id, [])[:4]:
                out += [c for c in self.resolve(source, depth=depth + 1) if _CODE_SHAPED.match(c)]
            return out
        if isinstance(node, ast.Attribute):
            return self._module_alias_constant(node)
        if isinstance(node, ast.Subscript):
            return self.resolve(node.value, depth=depth + 1)
        if isinstance(node, ast.Call):
            func = node.func
            # ``_BLOCK_CODES.get(block, default)`` — the table plus the fallback argument.
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id in self.tables
            ):
                out = list(self.tables[func.value.id])
                for arg in node.args:
                    out += self.resolve(arg, depth=depth + 1)
                return out
            if isinstance(func, ast.Name) and func.id in self.functions:
                return self.function_codes(func.id, depth=depth + 1)
        return []

    def function_codes(self, name: str, *, depth: int = 0) -> list[str]:
        """Code-shaped strings a same-file classifier can return (tuple returns included)."""
        cached = self._returns.get(name)
        if cached is not None:
            return cached
        self._returns[name] = []  # recursion guard
        out: list[str] = []
        fn = self.functions.get(name)
        if fn is not None:
            for node in ast.walk(fn):
                if not isinstance(node, ast.Return) or node.value is None:
                    continue
                value = node.value
                parts = list(value.elts) if isinstance(value, ast.Tuple) else [value]
                for part in parts:
                    out += [c for c in self.resolve(part, depth=depth + 1) if _CODE_SHAPED.match(c)]
        self._returns[name] = out
        return out

    def _import_candidates(self, name: str, *, submodule: bool = False) -> list[Path]:
        """Source file(s) an imported name may come from (absolute + relative imports).

        ``submodule`` asks for the file of a module imported *as* a name
        (``from . import policy as policy_mod``), not the file that defines the name.
        """
        module, level = self.imports[name]
        if level:
            base = self.path.parent
            for _ in range(level - 1):
                base = base.parent
            parts = module.split(".") if module else []
        elif module.startswith("agentcore."):
            base = self.root
            parts = module.split(".")[1:]
        else:
            return []
        if submodule:
            parts = [*parts, self.import_origins.get(name, name)]
        target = base
        for part in parts:
            target = target / part
        return [p for p in (target.with_suffix(".py"), target / "__init__.py") if p.is_file()]

    def _imported_constant(self, name: str) -> list[str]:
        for candidate in self._import_candidates(name):
            key = (candidate, name)
            if key not in _ProducerModule._imported:
                _ProducerModule._imported[key] = _module_constant(candidate, name)
            return _ProducerModule._imported[key]
        return []

    def _module_alias_constant(self, node: ast.Attribute) -> list[str]:
        """``policy_mod._REPO_UNUSABLE_CODE`` — a constant inside an imported *module*.

        The import statement names the file, so this is as provable as a bare module
        constant. An attribute on an *object* (``exc.code``) is not: the source never
        says which class it is, and that stays out.
        """
        base = node.value
        if not isinstance(base, ast.Name) or base.id not in self.imports:
            return []
        for candidate in self._import_candidates(base.id, submodule=True):
            key = (candidate, node.attr)
            if key not in _ProducerModule._imported:
                _ProducerModule._imported[key] = _module_constant(candidate, node.attr)
            return _ProducerModule._imported[key]
        return []

    def builds_success(self, name: str) -> bool:
        """True when ``name`` is a helper that returns a *successful* ToolResult.

        git attaches machine-readable codes to soft successes too (``dirty_skip``: the repo
        is fine, nothing was committed). Those never reach the failure face, so demanding
        user copy for them would make this gate cry wolf.
        """
        fn = self.functions.get(name)
        if fn is not None:
            return _has_success_true(fn)
        if name not in self.imports:
            return False
        for candidate in self._import_candidates(name):
            key = (candidate, name)
            if key not in _ProducerModule._success_builders:
                _ProducerModule._success_builders[key] = _module_function_succeeds(candidate, name)
            return _ProducerModule._success_builders[key]
        return False

    def sinks(self) -> dict[str, dict[str, int | None]]:
        """Helper name → ``{param: positional index}`` for params carrying a user code."""
        out: dict[str, dict[str, int | None]] = {}
        for name, fn in self.functions.items():
            positional = list(fn.args.posonlyargs) + list(fn.args.args)
            index_of = {arg.arg: i for i, arg in enumerate(positional)}
            params = [arg.arg for arg in positional + list(fn.args.kwonlyargs)]
            builds_result = fn.returns is not None and "ToolResult" in ast.unparse(fn.returns)
            aliases = _alias_map(fn)
            carried = {_root_alias(n, aliases) for n in _code_carrying_names(fn)}
            sink_params = {
                p for p in params if p in carried or (p in _SINK_PARAM_NAMES and builds_result)
            }
            if sink_params:
                out[name] = {p: index_of.get(p) for p in sink_params}
        return out


def _call_name(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else str(getattr(func, "id", ""))


def _builds_successful_result(node: ast.Call) -> bool:
    """``ToolResult(success=True, …)`` spelled out at this call."""
    if _call_name(node) != "ToolResult":
        return False
    return any(
        kw.arg == "success" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in node.keywords
    )


def _has_success_true(fn: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Call) and _builds_successful_result(node) for node in ast.walk(fn)
    )


def _module_function_succeeds(path: Path, name: str) -> bool:
    """Whether another file's helper builds a successful ToolResult (imported ``_ok``)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):  # pragma: no cover - unreadable source
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return _has_success_true(node)
    return False


def _module_constant(path: Path, name: str) -> list[str]:
    """Module-level ``NAME = "literal"`` from another file (imported code constants)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):  # pragma: no cover - unreadable source
        return []
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        value = _str_constant(getattr(node, "value", None))
        if value is None:
            continue
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return [value]
    return []


def _scan_produced_codes() -> dict[str, set[str]]:
    """Map each failure ``code`` in the producing sources to the call sites that emit it.

    Covers the shapes that actually reach :func:`tool_failure_fields`: a literal on a
    ``metadata`` / ``failure_code`` site, and — since every tool routes through its own
    private helper — a code handed to a helper whose parameter lands in ``metadata["code"]``,
    resolved through module constants, ``code`` tables, imported ``*_CODE`` constants,
    constants read off an imported module (``policy_mod._REPO_UNUSABLE_CODE``) and
    same-file classifiers returning ``(steer, code)``. Unrelated ``"code"`` keys (the CDP
    key event in the browser driver) stay out.

    Codes riding a *successful* ToolResult (git's ``dirty_skip`` / ``already_repo`` /
    ``no_repo``) are dropped on purpose: they are machine-readable outcomes, and demanding
    user copy for them would make this gate cry wolf.

    Residual blind spots, which land on the advice-free default rather than a wrong
    sentence. Each was walked to its producer by hand and pinned in
    :func:`test_attribute_reached_codes_have_copy`:
    - an attribute on an *object* (``exc.code``, ``result.code``, ``self.code``): the
      source never says which class it is, so the value cannot be read statically;
    - an ``ErrorCode`` member (``ErrorCode.VALIDATION_ERROR``): the enum is small enough
      to walk by hand, and the curated table is keyed by those very members;
    - a code assembled at runtime, or handed in from outside the scanned trees.
    """
    root = Path(agentcore.__file__).resolve().parent
    sources = [
        p
        for d in _SCANNED_PACKAGE_DIRS
        for p in sorted((root / d).rglob("*.py"))
        # sandboxd JSON-RPC ``code`` is a daemon wire field, not a tool-failure face.
        if "sandbox/sandboxd" not in p.as_posix()
    ]
    sources += [root / rel for rel in _SCANNED_FILES]

    found: dict[str, set[str]] = {}

    def record(codes: list[str], path: Path, node: ast.AST) -> None:
        for code in codes:
            found.setdefault(code, set()).add(f"{path.name}:{getattr(node, 'lineno', 0)}")

    for path in sources:
        assert path.is_file(), f"扫描源已移动：{path}"
        mod = _ProducerModule(path, root)
        sinks = mod.sinks()

        def dict_codes(node: ast.AST, module: _ProducerModule = mod) -> list[str]:
            out: list[str] = []
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Dict):
                    continue
                for key, value in zip(sub.keys, sub.values, strict=True):
                    if key is not None and _str_constant(key) == "code":
                        out += module.resolve(value)
            return out

        # ``*_metadata()`` helpers return a ToolResult metadata dict wholesale.
        for fn in ast.walk(mod.tree):
            if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) and fn.name.endswith(
                "_metadata"
            ):
                record(dict_codes(fn), path, fn)
        for node in ast.walk(mod.tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Subscript) and _str_constant(target.slice) == "code":
                        record(mod.resolve(node.value), path, node)
                continue
            if not isinstance(node, ast.Call):
                continue
            fname = _call_name(node)
            # A code on a successful result is a machine-readable outcome, not a face.
            if _builds_successful_result(node) or mod.builds_success(fname):
                continue
            if (
                fname == "setdefault"
                and len(node.args) == 2
                and _str_constant(node.args[0]) == "code"
            ):
                record(mod.resolve(node.args[1]), path, node)
            for kw in node.keywords:
                if kw.arg == "metadata":
                    record(dict_codes(kw.value), path, node)
                elif kw.arg == "failure_code" or (
                    kw.arg == "code" and fname == "tool_failure_fields"
                ):
                    record(mod.resolve(kw.value), path, node)
            # Tool-private helper: ``_failed(..., code=X)`` / ``_error(..., code=X)``.
            for param, index in sinks.get(fname, {}).items():
                arg = next((kw.value for kw in node.keywords if kw.arg == param), None)
                if arg is None and index is not None and index < len(node.args):
                    arg = node.args[index]
                record(mod.resolve(arg), path, node)
    return found


def test_every_produced_failure_code_has_curated_copy():
    """Sentinel: a code authored tool/engine-side must have a sentence in the table."""
    produced = _scan_produced_codes()

    # Guard the scanner itself: a refactor that stops resolving one of these shapes would
    # silently re-open the blind spot instead of failing, so each shape gets an anchor.
    assert len(produced) >= 45, f"扫描器可能失效，只找到 {sorted(produced)}"
    for anchor, shape in {
        "verify_policy_inner": "ToolResult(metadata=…) 里的字面量",
        "allowlist_deny": "引擎 tool_failure_fields(code=…)",
        "workspace_channel_dead": "*_metadata() 辅助函数返回的整块 metadata",
        "read_url_retired": "工具内 metadata 字面量",
        "local_workspace_required": "工具内部辅助函数 + 模块常量",
        "blocked_host": "工具内部辅助函数 + code 映射表",
        "site_access_denied": "工具内部辅助函数 + 同文件分类函数的返回值",
        "bridge_unauthorized": "工具内部辅助函数 + 跨文件导入的 *_CODE 常量",
        "repo_unusable": "经模块别名读到的常量（policy_mod._REPO_UNUSABLE_CODE）",
    }.items():
        assert anchor in produced, f"扫描器已看不到「{shape}」这种产出形状（丢了 {anchor}）"
    # Codes riding a *successful* ToolResult are outcomes, not faces — demanding copy for
    # them would make this gate cry wolf, and someone would delete it.
    for soft_success in ("dirty_skip", "already_repo", "no_repo"):
        assert soft_success not in produced, f"{soft_success} 挂在成功结果上，不该要求文案"

    missing = {
        code: sorted(sites) for code, sites in produced.items() if code not in _CURATED_BY_CODE
    }
    assert not missing, (
        "以下 code 已被工具/引擎产出，但 _CURATED_BY_CODE 里没有文案，"
        f"用户只会看到兜底句：{missing}"
    )


def test_exec_env_timeout_peek_matches_bubble_fact():
    """Tool-row timeout and the exec-env-dead bubble state the same cause."""
    from agentcore.workspace.limits import EXEC_ENV_DEAD_USER_VISIBLE_BY_CODE

    peek = _CURATED_BY_CODE["exec_env_probe_timeout"]
    bubble = EXEC_ENV_DEAD_USER_VISIBLE_BY_CODE["exec_env_probe_timeout"]
    for token in ("时限", "就绪", "命令"):
        assert token in peek, peek
        assert token in bubble, bubble
    assert "代码执行环境" not in peek
    assert bubble.startswith("本机暂时跑不了命令")
    no_interp_peek = _CURATED_BY_CODE["exec_env_no_interpreter"]
    assert "Python" not in no_interp_peek
    assert "python" not in no_interp_peek
    assert "解释器" in no_interp_peek


def test_pre_registered_codes_for_incoming_paths_have_copy():
    """Codes landing with the parallel tool changes — copy ships ahead of the producer."""
    for code in (
        "loopback_host",
        "bridge_unauthorized",
        "exec_env_no_interpreter",
        "exec_env_probe_timeout",
        "exec_env_spawn_denied",
        "workspace_io_error",
    ):
        assert _CURATED_BY_CODE[code].strip()


def test_attribute_reached_codes_have_copy():
    """The hand-walked half of the blind spot: codes read off an object at runtime.

    :func:`_scan_produced_codes` resolves a constant reached through an imported module,
    but not one read off an object — the source never says which class it is. These were
    traced to their producers by hand; keep the list in sync with them.
    """
    for code in (
        # git create_pr → ``CreatePullRequestErr.code`` (``workspace/github_pr.py``)
        "api_error",
        "auth_failed",
        "invalid_args",
        "network_error",
        "no_default_branch",
        "not_found",
        "validation_failed",
        # browser → ``BrowserSessionAcquireError.code`` (``runtime/browser/registry.py``)
        "session_bound_elsewhere",
        "session_not_found",
        # code_execute / test_run → ``ExecEnvProbeVerdict.code`` (``classify_probe_failure``)
        "exec_env_no_interpreter",
        "exec_env_probe_failed",
        "exec_env_probe_timeout",
        "exec_env_spawn_denied",
    ):
        assert _CURATED_BY_CODE[code].strip()


def test_default_sentence_promises_no_retry():
    """The fallback covers deterministic failures, so it must not advise waiting."""
    assert "稍后" not in DEFAULT_TOOL_FAILURE_MESSAGE
    assert "重试" not in DEFAULT_TOOL_FAILURE_MESSAGE
    assert DEFAULT_TOOL_FAILURE_MESSAGE == "这一步没能完成，我会换个方式继续。"


def test_deterministic_codes_never_advise_waiting():
    for code in _DETERMINISTIC_CODES:
        sentence = _CURATED_BY_CODE[code]
        for lie in ("稍后重试", "稍后再试", "请稍后"):
            assert lie not in sentence, f"{code} 是确定性失败，不该让用户等一会儿再试：{sentence}"


def test_curated_copy_keeps_engine_vocabulary_out():
    for code, sentence in _CURATED_BY_CODE.items():
        for word in _INTERNAL_VOCAB:
            assert word not in sentence, f"{code} 文案泄露内部概念「{word}」：{sentence}"
        for word in _MODEL_IMPERATIVES:
            assert word not in sentence, f"{code} 文案带了模型侧祈使「{word}」：{sentence}"
