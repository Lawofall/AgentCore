"""Unit tests for the context assembly spine (ContextAssembler + PromptContributor)
and the CEO turn envelope it renders (render_ceo_turn_envelope)."""

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
    from agentcore.runtime.resolve.prompt import render_ceo_turn_envelope

    sections: dict[str, object] = {
        "attachment_context": "",
        "soft_cap": None,
        "include_runtime": False,
    }
    sections.update(overrides)
    return render_ceo_turn_envelope(**sections)  # type: ignore[arg-type]


def test_ceo_turn_table_facts_follow_attachments():
    out = _ceo_turn(
        attachment_context="<附件/>",
        table_context="<表格/>",
    )
    assert out == "[系统提示]\n<附件/>\n<表格/>"


def test_ceo_turn_empty_sections_render_prefix_only():
    assert _ceo_turn() == ""


def test_ceo_turn_observation_covers_table_facts(monkeypatch):
    from agentcore.observability.prefix_cache import digest_text

    captured = _spy_on_observe(monkeypatch)
    table = "<表格>\n选中 1 行\n</表格>"
    out = _ceo_turn(table_context=table)

    row = captured[0]
    assert row["event"] == "cost.prompt_assembled"
    assert row["sections"]["table_context"] == len(table)
    assert row["total_chars"] == len(table)
    assert row["section_digests"]["table_context"] == digest_text(table)
    from agentcore.runtime.resolve.prompt import strip_turn_envelope_fence

    assert row["assembly_hash"] == assembly_hash(strip_turn_envelope_fence(out))


def test_ceo_turn_soft_cap_fires_on_table_facts_that_alone_blow_it(monkeypatch):
    captured = _spy_on_observe(monkeypatch)
    table = "选中一行，单元格里有一段较长的事实。\n" * 20

    _ceo_turn(table_context=table, soft_cap=200)
    assert captured[-1]["over_soft_cap"] is True

    _ceo_turn(soft_cap=200)
    assert captured[-1]["over_soft_cap"] is False
