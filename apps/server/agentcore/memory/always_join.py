"""Scope-first always-on ``<设定>`` join (slots then that layer's user rules).

Not an author split. Nearer folders come later; 导航 does not inherit.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class LayerFragment:
    """One rendered ``<设定>`` slice (a whole scope layer)."""

    body: str


def layer_fragment(
    label: str | None, parts: Sequence[str | None]
) -> LayerFragment | None:
    """One scope layer. ``label`` is location (not author); empty parts drop the layer."""
    cleaned: list[str] = []
    for part in parts:
        if not part:
            continue
        text = part.strip()
        if text:
            cleaned.append(text)
    if not cleaned:
        return None
    body = "\n\n".join(cleaned)
    if label:
        body = f"{label}\n{body}"
    return LayerFragment(body=body)


def join_always_layers(
    *,
    folder_settings_label: str,
    ancestor_settings_label: str,
    folder_nav_label: str,
    global_pref: str | None = None,
    global_profile: str | None = None,
    global_rules: Sequence[str] = (),
    ancestor_layers: Sequence[tuple[str | None, Sequence[str]]] = (),
    current_profile: str | None = None,
    current_nav: str | None = None,
    current_rules: Sequence[str] = (),
    include_current: bool = False,
) -> list[LayerFragment]:
    frags: list[LayerFragment] = []
    global_frag = layer_fragment(None, [global_pref, global_profile, *global_rules])
    if global_frag:
        frags.append(global_frag)
    for profile, rules in ancestor_layers:
        frag = layer_fragment(ancestor_settings_label, [profile, *rules])
        if frag:
            frags.append(frag)
    if include_current:
        nav_part = f"{folder_nav_label}\n{current_nav}" if current_nav else None
        frag = layer_fragment(
            folder_settings_label,
            [current_profile, nav_part, *current_rules],
        )
        if frag:
            frags.append(frag)
    return frags


def ancestor_rule_bodies_by_scope(
    docs: Sequence[Mapping[str, object]],
    ancestors: Sequence[str],
    *,
    body_of,
) -> list[list[str]]:
    """Split flat ancestor rule docs onto ``ancestors`` (outermost-first).

    Tagged ``folder_id`` wins. Untagged: one-to-one zip when counts match; else the
    whole bag lands on the outermost ancestor (old cloud without folder_id).
    """
    buckets: list[list[str]] = [[] for _ in ancestors]
    parsed: list[tuple[str | None, str]] = []
    for doc in docs:
        body = body_of(doc)
        if not body:
            continue
        raw_fid = doc.get("folder_id")
        fid = str(raw_fid) if raw_fid else None
        parsed.append((fid, body))
    if not parsed or not ancestors:
        return buckets
    if any(fid for fid, _ in parsed):
        index = {scope: i for i, scope in enumerate(ancestors)}
        for fid, body in parsed:
            i = index.get(fid or "")
            if i is not None:
                buckets[i].append(body)
        return buckets
    if len(parsed) == len(ancestors):
        for i, (_, body) in enumerate(parsed):
            buckets[i].append(body)
        return buckets
    buckets[0] = [body for _, body in parsed]
    return buckets
