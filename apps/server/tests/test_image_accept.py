"""Vendor image-accept contract (shared by catalog vision + native routing)."""

import pytest

from agentcore.llm.image_accept import (
    clear_images_rejected,
    model_accepts_images,
    note_images_rejected,
)
from agentcore.llm.model_metadata import CAPABILITY_VISION, model_metadata_for


@pytest.fixture(autouse=True)
def _clear_rejected():
    clear_images_rejected()
    yield
    clear_images_rejected()


def test_deepseek_only_exact_vision_exp():
    assert model_accepts_images("deepseek-v4-flash-vision-exp") is True
    assert model_accepts_images("deepseek/deepseek-v4-flash-vision-exp") is True
    assert model_accepts_images("deepseek-v4-flash") is False
    assert model_accepts_images("deepseek-v4-pro") is False
    assert model_accepts_images("deepseek-v4-flash-free") is False


def test_negative_example_overrides_table():
    assert model_accepts_images("deepseek-v4-flash-vision-exp") is True
    note_images_rejected("deepseek-v4-flash-vision-exp")
    assert model_accepts_images("deepseek-v4-flash-vision-exp") is False
    assert (
        CAPABILITY_VISION
        not in model_metadata_for("deepseek-v4-flash-vision-exp").capabilities
    )
    clear_images_rejected()
    assert model_accepts_images("deepseek-v4-flash-vision-exp") is True


def test_openai_family_prefix_sku_boundary():
    assert model_accepts_images("gpt-4o") is True
    assert model_accepts_images("gpt-4o-mini") is True
    assert model_accepts_images("gpt-4o-custom-build") is True
    assert model_accepts_images("gpt-4.1") is True
    assert model_accepts_images("gpt-4.1-mini") is True
    # No SKU boundary after the family id.
    assert model_accepts_images("gpt-4omni") is False
    assert model_accepts_images("gpt-4.10") is False
    assert model_accepts_images("mystery-4o-clone") is False


def test_moonshot_exact_not_k2_family():
    assert model_accepts_images("kimi-k2.5") is True
    assert model_accepts_images("kimi-k2.6") is True
    assert model_accepts_images("kimi-k3") is True
    assert model_accepts_images("kimi-k2") is False
    assert model_accepts_images("kimi-k2.6-preview") is False


def test_glm_4v_and_qwen_vl_prefix():
    assert model_accepts_images("glm-4v") is True
    assert model_accepts_images("glm-4v-plus") is True
    assert model_accepts_images("glm-4.6") is False
    assert model_accepts_images("qwen-vl") is True
    assert model_accepts_images("qwen-vl-max") is True
    assert model_accepts_images("qwen-max") is False


def test_keywords_do_not_open_the_gate():
    assert model_accepts_images("acme-vl-special") is False
    assert model_accepts_images("mystery-4o-clone") is False
    assert model_accepts_images("some-vision-model") is False
