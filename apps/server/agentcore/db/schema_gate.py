"""Schema gate: migration head must match the ORM's expected schema.

Catches the class of incident where a destructive migration (DROP COLUMN /
DROP TABLE) lands while application code or ORM metadata still references the
removed artifact — or the inverse (models lag behind migrations).

Offline checks (no database):

1. Exactly one Alembic head (branched migrations refuse ``upgrade head``).
2. Net tombstones from the migration chain (tables/columns dropped and never
   re-added before head) must be absent from ``Base.metadata``.
3. Production package ``agentcore/`` (excluding ``db/migrations/``) must not
   reference tombstoned table names or known dropped symbols.

Optional ``--live`` (needs ``DATABASE_URL``): ``alembic check`` after the DB is
at head — the same zero-drift contract documented on ``db.models``.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

import agentcore.db as _db_pkg

# Import side-effect: register every ORM table on Base.metadata.
import agentcore.db.models  # noqa: F401
from agentcore.db.base import Base

# Symbols that historically mapped 1:1 to dropped tables/columns. Kept as an
# extra source-scan needle so renames (UserLlmKey → UserLlmProvider) cannot
# silently keep the old class import after the table is gone.
_EXTRA_SOURCE_NEEDLES: tuple[str, ...] = (
    "UserLlmKey",
    "billing_preference",
    # Auth invite codes retired (table ``invites`` is ambiguous vs shared-space
    # invite helpers — skip that table name in source scan; these catch reintro).
    "InviteRepository",
    "generate_invite_code",
    "CreateInviteRequest",
    # In-app ticket product retired (table ``feedback`` is ambiguous vs
    # ``messages.feedback`` 点赞/点踩 and runtime "feedback" identifiers).
    "FeedbackRow",
    "FeedbackRepository",
    "get_feedback_repo",
    "CreateFeedbackRequest",
    # Official HOW overlay retired (tables ``skill_slot_replacements`` /
    # ``skill_slot_homes`` dropped).
    "SkillSlotHome",
    "SkillSlotReplacement",
    "SkillSlotRepository",
)

# Dropped table names that collide with unrelated identifiers (e.g. shared-space
# invite helpers; ``feedback`` vs message rating / runtime feedback). Still
# enforced via ORM metadata; skipped in source scan.
_AMBIGUOUS_TOMBSTONE_TABLES: frozenset[str] = frozenset({"invites", "feedback"})

_AGENTCORE_ROOT = Path(_db_pkg.__file__).resolve().parent.parent
_MIGRATIONS_DIR = Path(_db_pkg.__file__).resolve().parent / "migrations"


@dataclass(frozen=True)
class SchemaGateResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    heads: tuple[str, ...] = ()
    dropped_tables: tuple[str, ...] = ()
    dropped_columns: tuple[tuple[str, str], ...] = ()


def _alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    return cfg


def script_heads() -> list[str]:
    return sorted(ScriptDirectory.from_config(_alembic_config()).get_heads())


def _literal_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_op_call(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "op"
        and node.func.attr == name
    )


def _upgrade_body(tree: ast.AST) -> list[ast.stmt]:
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade":
            return list(node.body)
    return []


def _walk_upgrade_ops(path: Path) -> list[tuple[str, tuple[str, ...]]]:
    """Return ordered ``(op_name, args)`` from a migration's ``upgrade()``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    ops: list[tuple[str, tuple[str, ...]]] = []
    for stmt in _upgrade_body(tree):
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            if _is_op_call(node, "drop_table"):
                table = _literal_str(node.args[0]) if node.args else None
                if table:
                    ops.append(("drop_table", (table,)))
            elif _is_op_call(node, "create_table"):
                table = _literal_str(node.args[0]) if node.args else None
                if table:
                    ops.append(("create_table", (table,)))
            elif _is_op_call(node, "drop_column"):
                if len(node.args) >= 2:
                    table = _literal_str(node.args[0])
                    column = _literal_str(node.args[1])
                    if table and column:
                        ops.append(("drop_column", (table, column)))
            elif _is_op_call(node, "add_column"):
                table = _literal_str(node.args[0]) if node.args else None
                column = None
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Call):
                    # sa.Column("name", ...)
                    col_call = node.args[1]
                    if col_call.args:
                        column = _literal_str(col_call.args[0])
                if table and column:
                    ops.append(("add_column", (table, column)))
    return ops


def net_tombstones() -> tuple[set[str], set[tuple[str, str]]]:
    """Tables/columns dropped on the path to head and never re-created/added."""
    script = ScriptDirectory.from_config(_alembic_config())
    heads = script.get_heads()
    if len(heads) != 1:
        # Caller reports the branch error; tombstones undefined under branches.
        return set(), set()

    present_tables: set[str] = set()
    present_columns: set[tuple[str, str]] = set()
    dropped_tables: set[str] = set()
    dropped_columns: set[tuple[str, str]] = set()

    # walk_revisions yields newest→oldest; reverse to apply base→head.
    chain = list(script.walk_revisions(base="base", head=heads[0]))
    chain.reverse()

    for rev in chain:
        path = Path(rev.path)
        for op_name, args in _walk_upgrade_ops(path):
            if op_name == "create_table":
                (table,) = args
                present_tables.add(table)
                dropped_tables.discard(table)
            elif op_name == "drop_table":
                (table,) = args
                present_tables.discard(table)
                dropped_tables.add(table)
                # Columns of a dropped table are gone with it.
                present_columns = {c for c in present_columns if c[0] != table}
                dropped_columns = {c for c in dropped_columns if c[0] != table}
            elif op_name == "add_column":
                table, column = args
                present_columns.add((table, column))
                dropped_columns.discard((table, column))
            elif op_name == "drop_column":
                table, column = args
                present_columns.discard((table, column))
                dropped_columns.add((table, column))

    # Only absences that remain absent at head matter.
    dropped_tables -= present_tables
    dropped_columns -= present_columns
    return dropped_tables, dropped_columns


def _orm_tables() -> set[str]:
    return set(Base.metadata.tables)


def _orm_columns() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for table_name, table in Base.metadata.tables.items():
        for column in table.columns:
            out.add((table_name, column.name))
    return out


def _iter_package_py_files() -> list[Path]:
    root = _AGENTCORE_ROOT
    skip_names = {"schema_gate.py"}  # this module names tombstones on purpose
    files: list[Path] = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        parts = rel.parts
        if parts and parts[0] == "db" and len(parts) > 1 and parts[1] == "migrations":
            continue
        if path.name in skip_names:
            continue
        files.append(path)
    return files


_IDENT_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _ident_pattern(name: str) -> re.Pattern[str]:
    compiled = _IDENT_RE_CACHE.get(name)
    if compiled is None:
        compiled = re.compile(rf"\b{re.escape(name)}\b")
        _IDENT_RE_CACHE[name] = compiled
    return compiled


def scan_source_for_tombstones(dropped_tables: set[str]) -> list[str]:
    """Fail if production package still names a net-dropped table / known symbol.

    Column names alone are too ambiguous for a source scan (``base_url``,
    ``status``, …); column tombstones are enforced via ORM metadata only.
    """
    needles: list[str] = list(_EXTRA_SOURCE_NEEDLES)
    needles.extend(sorted(dropped_tables - _AMBIGUOUS_TOMBSTONE_TABLES))
    seen: set[str] = set()
    ordered: list[str] = []
    for n in needles:
        if n not in seen:
            seen.add(n)
            ordered.append(n)

    errors: list[str] = []
    for path in _iter_package_py_files():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(_AGENTCORE_ROOT.parent)
        for needle in ordered:
            if _ident_pattern(needle).search(text):
                errors.append(f"{rel}: still references tombstoned symbol `{needle}`")
    return errors


def check_orm_against_tombstones(
    dropped_tables: set[str],
    dropped_columns: set[tuple[str, str]],
) -> list[str]:
    errors: list[str] = []
    orm_tables = _orm_tables()
    orm_columns = _orm_columns()
    for table in sorted(dropped_tables):
        if table in orm_tables:
            errors.append(
                f"ORM still defines table `{table}` which migrations drop before head"
            )
    for table, column in sorted(dropped_columns):
        if (table, column) in orm_columns:
            errors.append(
                f"ORM still defines column `{table}.{column}` which migrations drop before head"
            )
    return errors


def run_offline_checks(*, simulate_stale_orm: bool = False) -> SchemaGateResult:
    """Run DB-free consistency checks. Exit-worthy errors go in ``errors``."""
    errors: list[str] = []
    warnings: list[str] = []
    heads = script_heads()

    if not heads:
        errors.append("no Alembic heads found under agentcore/db/migrations")
        return SchemaGateResult(ok=False, errors=errors, heads=tuple(heads))

    if len(heads) > 1:
        errors.append(
            "multiple Alembic heads — merge migrations before deploy: " + ", ".join(heads)
        )
        return SchemaGateResult(ok=False, errors=errors, heads=tuple(heads))

    dropped_tables, dropped_columns = net_tombstones()

    if simulate_stale_orm:
        # Demo / self-test: pretend the ORM still carries a known dropped column.
        dropped_columns = set(dropped_columns)
        dropped_columns.add(("users", "billing_preference"))
        # Force the ORM check to see the column even if metadata is clean.
        if ("users", "billing_preference") not in _orm_columns():
            errors.append(
                "ORM still defines column `users.billing_preference` which migrations "
                "drop before head  [simulated]"
            )

    errors.extend(check_orm_against_tombstones(dropped_tables, dropped_columns))
    if not simulate_stale_orm:
        errors.extend(scan_source_for_tombstones(dropped_tables))

    return SchemaGateResult(
        ok=not errors,
        errors=errors,
        warnings=warnings,
        heads=tuple(heads),
        dropped_tables=tuple(sorted(dropped_tables)),
        dropped_columns=tuple(sorted(dropped_columns)),
    )


def run_live_alembic_check() -> list[str]:
    """Compare ORM metadata to the live DB schema (``alembic check``).

    Caller must ensure the database is already at migration head (deploy does
    ``alembic upgrade head`` first; CI upgrades a throwaway Postgres).
    """
    from alembic import command

    # Prefer alembic.ini when present (cwd = apps/server in CI/deploy); fall
    # back to the package-local Config used by offline checks.
    ini = Path(_db_pkg.__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(ini)) if ini.is_file() else _alembic_config()
    try:
        command.check(cfg)
    except Exception as exc:  # noqa: BLE001 — surface any drift as gate errors
        return [f"alembic check failed: {exc}"]
    return []


def run_schema_gate(
    *,
    live: bool = False,
    simulate_stale_orm: bool = False,
) -> SchemaGateResult:
    result = run_offline_checks(simulate_stale_orm=simulate_stale_orm)
    if not result.ok or simulate_stale_orm:
        return result

    if live:
        live_errors = run_live_alembic_check()
        if live_errors:
            return SchemaGateResult(
                ok=False,
                errors=[*result.errors, *live_errors],
                warnings=list(result.warnings),
                heads=result.heads,
                dropped_tables=result.dropped_tables,
                dropped_columns=result.dropped_columns,
            )

    return result
