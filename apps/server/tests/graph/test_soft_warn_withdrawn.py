"""委派一次性软提示族已撤：旧命中形仍成功入图，结果尾无告警文案。"""

from __future__ import annotations

import pytest

from tests.delegate.conftest import Provider, ctx, tool

_WARN_SNIPPETS = (
    "吃同批队友产出",
    "同时塞了设计与实现",
    "首批单节点手写写工程",
)
