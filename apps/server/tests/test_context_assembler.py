"""Unit tests for the context assembly spine (ContextAssembler + PromptContributor)
and the CEO turn prompt it renders (pipeline.assemble.build_chat_system_prompt)."""

from __future__ import annotations

from agentcore.runtime.context import (
    ContextAssembler,
    PromptContributor,
    SectionOrder,
    assembly_hash,
)


def test_render_sorts_by_order_not_contribution_sequence():
    # Contribute OUT of order; render must still follow `order` (the centralization win).
    out = (
        ContextAssembler()
        .add("attach", "ATT", SectionOrder.ATTACHMENT)
        .add("base", "BASE", SectionOrder.BASE)
        .add("memory", "MEM", SectionOrder.MEMORY)
        .render()
    )
    assert out == "BASE\nMEM\nATT"


def test_falsy_text_is_skipped_no_blank_line():
    out = (
        ContextAssembler()
        .add("base", "BASE", SectionOrder.BASE)
        .add("memory", None, SectionOrder.MEMORY)  # absent this turn
        .add("attach", "", SectionOrder.ATTACHMENT)  # empty
        .render()
    )
    assert out == "BASE"


def test_contribute_accepts_a_contributor_object():
    out = (
        ContextAssembler()
        .contribute(PromptContributor("a", "A", order=200))
        .contribute(PromptContributor("b", "B", order=100))
        .render()
    )
    assert out == "B\nA"


def test_equal_order_keeps_contribution_order_stable():
    out = ContextAssembler().add("first", "1", 500).add("second", "2", 500).render()
    assert out == "1\n2"


def test_contributors_returns_kept_in_render_order():
    asm = (
        ContextAssembler()
        .add("attach", "ATT", SectionOrder.ATTACHMENT)
        .add("base", "BASE", SectionOrder.BASE)
        .add("skip", None, SectionOrder.MEMORY)
    )
    keys = [c.key for c in asm.contributors()]
    assert keys == ["base", "attach"]  # sorted by order, falsy dropped


def test_budget_is_carried_but_not_enforced_today():
    # budget rides on the contributor; assembler does not trim.
    asm = ContextAssembler().add("base", "x" * 100, SectionOrder.BASE, budget=10)
    assert asm.render() == "x" * 100
    assert asm.contributors()[0].budget == 10


def test_observe_is_chainable_and_side_effect_free():
    # COST-004 仅观测起步: observe() only logs per-section chars — it returns self (chainable)
    # and the rendered prompt is byte-identical with or without it (zero behavior change).
    asm = (
        ContextAssembler()
        .add("base", "BASE", SectionOrder.BASE)
        .add("attach", "ATTACH", SectionOrder.ATTACHMENT)
    )
    before = asm.render()
    assert asm.observe(scope="test", soft_cap=1000) is asm  # chainable (returns self)
    assert asm.render() == before == "BASE\nATTACH"  # observe trimmed/changed nothing


def test_assembly_hash_stable_for_identical_render():
    # M6 方案1: same sections → same hash (searchable drift signal on cost.prompt_assembled).
    a = (
        ContextAssembler()
        .add("base", "BASE", SectionOrder.BASE)
        .add("memory", "MEM", SectionOrder.MEMORY)
        .render()
    )
    b = (
        ContextAssembler()
        .add("memory", "MEM", SectionOrder.MEMORY)  # contribution order differs
        .add("base", "BASE", SectionOrder.BASE)
        .render()
    )
    assert a == b
    assert assembly_hash(a) == assembly_hash(b)


def test_assembly_hash_changes_when_section_order_or_body_changes():
    base = ContextAssembler().add("base", "BASE", SectionOrder.BASE).add(
        "memory", "MEM", SectionOrder.MEMORY
    )
    swapped = ContextAssembler().add("base", "MEM", SectionOrder.BASE).add(
        "memory", "BASE", SectionOrder.MEMORY
    )
    assert assembly_hash(base.render()) != assembly_hash(swapped.render())
    # Volatile tail content change also busts the product hash (expected).
    with_tail = (
        ContextAssembler()
        .add("base", "BASE", SectionOrder.BASE)
        .add("attach", "ATT-1", SectionOrder.ATTACHMENT)
    )
    with_tail2 = (
        ContextAssembler()
        .add("base", "BASE", SectionOrder.BASE)
        .add("attach", "ATT-2", SectionOrder.ATTACHMENT)
    )
    assert assembly_hash(with_tail.render()) != assembly_hash(with_tail2.render())


def test_observe_emits_assembly_hash(monkeypatch):
    captured: list[dict] = []

    class _Spy:
        def info(self, event: str, **kwargs: object) -> None:
            captured.append({"event": event, **kwargs})

    monkeypatch.setattr(
        "agentcore.runtime.context.assembler.logger",
        _Spy(),
    )
    asm = (
        ContextAssembler()
        .add("base", "BASE", SectionOrder.BASE)
        .add("memory", "MEM", SectionOrder.MEMORY)
    )
    expected = assembly_hash(asm.render())
    asm.observe(scope="unit", soft_cap=None)
    assert len(captured) == 1
    row = captured[0]
    assert row["event"] == "cost.prompt_assembled"
    assert row["scope"] == "unit"
    assert row["assembly_hash"] == expected
    assert row["sections"] == {"base": 4, "memory": 3}
    assert row["total_chars"] == 7


# --- CEO turn prompt (runtime/pipeline/assemble.py) ---------------------------------------


def _spy_on_observe(monkeypatch) -> list[dict]:
    captured: list[dict] = []

    class _Spy:
        def info(self, event: str, **kwargs: object) -> None:
            captured.append({"event": event, **kwargs})

    monkeypatch.setattr("agentcore.runtime.context.assembler.logger", _Spy())
    return captured


def _ceo_turn(**overrides: object) -> str:
    from agentcore.runtime.pipeline.assemble import build_chat_system_prompt

    sections: dict[str, object] = {
        "ceo_prompt": "CEO",
        "prior_delegate_retry": "",
        "attachment_context": "",
        "registered_sources": "",
        "soft_cap": None,
    }
    sections.update(overrides)
    return build_chat_system_prompt(**sections)  # type: ignore[arg-type]


def test_ceo_turn_renders_the_source_ledger_after_the_volatile_tail():
    # CTX-A3: the ledger used to be f-string appended by the caller; it keeps the SAME
    # position (last, after attachments) now that it is a section.
    out = _ceo_turn(
        attachment_context="<attachments/>",
        registered_sources="<已登记来源/>",
    )
    assert out == "CEO\n<attachments/>\n<已登记来源/>"


def test_ceo_turn_has_no_working_set_section():
    out = _ceo_turn()
    assert out == "CEO"
    assert "工作集" not in out
    assert "近期团队图" not in out
    assert "上轮交付缺口" not in out


def test_ceo_turn_prompt_omits_retired_futile_retry_section():
    out = _ceo_turn(prior_delegate_retry="<上轮重派/>")
    assert out == "CEO\n<上轮重派/>"
    assert "上轮徒劳重试" not in out


def test_ceo_turn_observation_covers_the_source_ledger(monkeypatch):
    # CTX-A3 ratchet: assembly_hash / total_chars / section_digests must see the ledger.
    # Appended outside the assembler, both under-reported by the whole block — so the
    # prefix-drift signal went blind to the one section that grows every turn.
    from agentcore.observability.prefix_cache import digest_text

    captured = _spy_on_observe(monkeypatch)
    ledger = "<已登记来源>\n- #r1 · url=https://example.com\n</已登记来源>"
    out = _ceo_turn(registered_sources=ledger)

    row = captured[0]
    assert row["event"] == "cost.prompt_assembled"
    assert row["sections"]["registered_sources"] == len(ledger)
    assert row["total_chars"] == len("CEO") + len(ledger)
    assert row["section_digests"]["registered_sources"] == digest_text(ledger)
    assert row["assembly_hash"] == assembly_hash(out)  # hash covers the rendered ledger


def test_ceo_turn_soft_cap_fires_on_a_ledger_that_alone_blows_it(monkeypatch):
    # CTX-A3 ratchet: prompt_budget_char_soft_cap was unreachable via the ledger — the
    # section that grows unbounded across a conversation never counted toward it.
    captured = _spy_on_observe(monkeypatch)
    ledger = "- #r1 · url=https://example.com · deep_read=是\n" * 40

    _ceo_turn(registered_sources=ledger, soft_cap=200)
    assert captured[-1]["over_soft_cap"] is True

    _ceo_turn(soft_cap=200)  # same turn without the ledger stays well under
    assert captured[-1]["over_soft_cap"] is False
