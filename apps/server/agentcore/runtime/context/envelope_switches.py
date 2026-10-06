"""Per-conversation envelope projections the model is not told.

Empty list = every row present. Each row is one engine-rendered line (date,
one ``<工作区>`` fact, or the CEO file index). Turning a row off drops that
line only: the boundary, approvals, and fuses still run. Bodies are not
editable. The turn entry binds the row; renderers read it. A change applies
on the next turn entry, not the turn already running. No account copy.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

RUNTIME_DATE = "runtime_date"
EXECUTION = "execution"
BOUNDARY = "boundary"
DESK = "desk"
SYSTEM = "system"
GIT = "git"
CLIENT = "client"
MOUNTS = "mounts"
GAPS = "gaps"
SANDBOX = "sandbox"
INTERPRETERS = "interpreters"
FILE_INDEX = "file_index"


@dataclass(frozen=True)
class EnvelopeProjection:
    id: str
    label: str
    summary: str


# Vocabulary order is the assembly-page order and the normalize order.
PROJECTIONS: tuple[EnvelopeProjection, ...] = (
    EnvelopeProjection(
        RUNTIME_DATE,
        "日期",
        "信封里报当前日期。主管和队员读同一场。",
    ),
    EnvelopeProjection(
        EXECUTION,
        "执行",
        "信封报云端还是本机、出站、原件能不能改。",
    ),
    EnvelopeProjection(
        BOUNDARY,
        "边界",
        "信封报只看、这个文件夹或这台电脑。关掉不改这道边界。",
    ),
    EnvelopeProjection(
        DESK,
        "桌",
        "信封报坐在哪个文件夹或本会话草稿。",
    ),
    EnvelopeProjection(
        SYSTEM,
        "系统",
        "信封报操作系统和壳。",
    ),
    EnvelopeProjection(
        GIT,
        "Git",
        "信封报工作区根有没有仓库、在哪条分支。",
    ),
    EnvelopeProjection(
        CLIENT,
        "客户端",
        "信封报桌面连没连上。本机工具已在表里时这行本来就空。",
    ),
    EnvelopeProjection(
        MOUNTS,
        "区外",
        "信封报挂进来的区外目录和能不能改。",
    ),
    EnvelopeProjection(
        GAPS,
        "缺口",
        "信封报边界允许、但这场没装上的名字。",
    ),
    EnvelopeProjection(
        SANDBOX,
        "沙箱",
        "云端跑命令不可用时，信封报原因。",
    ),
    EnvelopeProjection(
        INTERPRETERS,
        "解释器",
        "本机解释器没配齐时，信封报缺哪些。",
    ),
    EnvelopeProjection(
        FILE_INDEX,
        "文件索引",
        "主管信封附上文件名单。队员不读这份名单。",
    ),
)

_ALLOWED = tuple(row.id for row in PROJECTIONS)


def all_projection_ids() -> tuple[str, ...]:
    """Every envelope row, in assembly-page order."""
    return _ALLOWED
_OMITTED: ContextVar[frozenset[str]] = ContextVar(
    "envelope_omissions",
    default=frozenset(),
)


def normalize_omitted_projections(raw: object) -> tuple[str, ...]:
    """Keep known ids, drop the rest, stable vocabulary order."""
    if not isinstance(raw, list):
        return ()
    present = {item for item in raw if isinstance(item, str)}
    return tuple(name for name in _ALLOWED if name in present)


def bind_envelope_omissions(raw: object) -> Token[frozenset[str]]:
    """Publish this turn entry's omissions. Reset with :func:`reset_envelope_omissions`."""
    return _OMITTED.set(frozenset(normalize_omitted_projections(raw)))


def reset_envelope_omissions(token: Token[frozenset[str]]) -> None:
    _OMITTED.reset(token)


def current_omitted_projections() -> frozenset[str]:
    return _OMITTED.get()


def include_projection(projection_id: str) -> bool:
    """True when this conversation still tells the model that line."""
    return projection_id not in _OMITTED.get()


def include_runtime_date() -> bool:
    return include_projection(RUNTIME_DATE)


def include_file_index() -> bool:
    return include_projection(FILE_INDEX)
