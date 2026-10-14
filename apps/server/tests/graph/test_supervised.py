"""没有暂停中的计划时，改计划与收尾都直接拒绝。"""

from tests.delegate.conftest import Provider, tool


async def test_replan_without_supervised_run_errors():
    t = tool(Provider([]))
    result = await t.replan({})
    assert result.success is False
    assert "没有已暂停的图" in (result.error or "")


async def test_dispose_open_supervised_noop_without_supervised():
    t = tool(Provider(["only"]))
    assert await t.dispose_open_supervised() is None
