"""Official HOW omit list: known names, registry order, consult roots."""

from agentcore.runtime.skills.factory_switches import (
    consult_root_name,
    normalize_omitted_factory_skills,
)
from agentcore.runtime.skills.product_help import PRODUCT_HELP_NAME


def test_normalize_keeps_known_names_in_registry_order():
    assert normalize_omitted_factory_skills(None) == ()
    assert normalize_omitted_factory_skills("page_ui") == ()
    assert normalize_omitted_factory_skills(["nope", "page_ui", "page_ui", "prompts"]) == (
        "prompts",
        "page_ui",
    )
    assert normalize_omitted_factory_skills(["product_help", "prompts", "page_ui"]) == (
        "prompts",
        "page_ui",
        "product_help",
    )


def test_consult_root_maps_product_help_sections():
    assert consult_root_name("page_ui") == "page_ui"
    assert consult_root_name(f"{PRODUCT_HELP_NAME}:这是什么") == PRODUCT_HELP_NAME
