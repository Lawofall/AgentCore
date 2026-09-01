"""Single vision capability bit shared by CEO assembly and attachment honesty.

False ⇒ do not put ``read_image`` on the CEO surface. Same reason
``WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS`` withdraws it: a listed tool that cannot
succeed only invites 「换个工具再看一眼图」 rounds that never can work.

True when a :class:`~agentcore.vision.protocol.VisionReader` is wired **or** the
main chat model accepts images (:func:`~agentcore.llm.image_accept.model_accepts_images`).
Empty BYOK vision slot does not invent a platform reader
(``build_vision_reader`` already refuses that); a reader may still be wired from
an image-accepting main.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentcore.vision.protocol import VisionReader


def vision_capability_available(
    *,
    vision_reader: VisionReader | None,
    main_native_vision: bool,
) -> bool:
    """True iff this turn can actually see images (reader or native multimodal)."""
    return vision_reader is not None or main_native_vision
