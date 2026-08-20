"""Dual recognition for live ``browser`` + pre-merge ``browser_*`` names."""

from agentcore.runtime.browser.call_identity import (
    browser_call_action,
    browser_tool_face,
    is_browser_navigate_call,
    is_browser_tool_name,
)


def test_live_name_and_legacy_names_are_browser_tools():
    assert is_browser_tool_name("browser")
    assert is_browser_tool_name("browser_navigate")
    assert is_browser_tool_name("browser_screenshot")
    assert not is_browser_tool_name("read_url")
    assert not is_browser_tool_name("host")


def test_action_from_legacy_name_or_unified_args():
    assert browser_call_action("browser_navigate") == "navigate"
    assert browser_call_action("browser_console") == "console"
    assert browser_call_action("browser", {"action": "snapshot"}) == "snapshot"
    assert browser_call_action("browser", '{"action":"click"}') == "click"
    assert browser_call_action("file_read") == ""


def test_navigate_call_dual_recognition():
    assert is_browser_navigate_call("browser_navigate", '{"url":"https://ex.com"}')
    assert is_browser_navigate_call("browser", '{"action":"navigate"}')
    assert not is_browser_navigate_call("browser", '{"action":"screenshot"}')
    assert not is_browser_navigate_call("browser_click")


def test_harvest_face_covers_new_and_old_names():
    assert browser_tool_face("browser") == "浏览网页"
    assert browser_tool_face("browser_navigate") == "浏览网页"
    assert browser_tool_face("terminal") is None
